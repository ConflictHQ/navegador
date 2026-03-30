"""
Ansible playbook/task parser — extracts plays, tasks, handlers, roles,
and variables from Ansible YAML files into the navegador graph.

Unlike other parsers this does NOT use tree-sitter.  Ansible semantics
are encoded in YAML structure (dicts with well-known keys like ``hosts``,
``tasks``, ``handlers``), so we parse with ``yaml.safe_load()`` and walk
the resulting Python data structures directly.

Invoked via a hook in RepoIngester rather than through LANGUAGE_MAP.
"""

import logging
import re
from pathlib import Path

import yaml

from navegador.graph.schema import EdgeType, NodeLabel
from navegador.graph.store import GraphStore
from navegador.ingestion.parser import LanguageParser

logger = logging.getLogger(__name__)

# Well-known Ansible module names — used to identify task dicts that lack
# an explicit ``name`` key and to extract the module used by a task.
_ANSIBLE_MODULES = {
    "apt",
    "yum",
    "dnf",
    "pip",
    "gem",
    "npm",
    "copy",
    "template",
    "file",
    "lineinfile",
    "blockinfile",
    "service",
    "systemd",
    "command",
    "shell",
    "raw",
    "script",
    "git",
    "get_url",
    "uri",
    "unarchive",
    "user",
    "group",
    "cron",
    "mount",
    "docker_container",
    "docker_image",
    "k8s",
    "helm",
    "debug",
    "assert",
    "fail",
    "set_fact",
    "include_tasks",
    "import_tasks",
    "include_role",
    "import_role",
    "block",
    "rescue",
    "always",
    "wait_for",
    "pause",
    "stat",
    "find",
    "replace",
    "package",
    "hostname",
    "timezone",
    "sysctl",
    "authorized_key",
    "firewalld",
    "iptables",
    "aws_s3",
    "ec2",
    "ec2_instance",
    "s3_bucket",
    "ansible.builtin.copy",
    "ansible.builtin.template",
    "ansible.builtin.file",
    "ansible.builtin.command",
    "ansible.builtin.shell",
    "ansible.builtin.service",
    "ansible.builtin.debug",
    "ansible.builtin.set_fact",
    "ansible.builtin.include_tasks",
    "ansible.builtin.import_tasks",
    "ansible.builtin.include_role",
    "ansible.builtin.import_role",
    "ansible.builtin.apt",
    "ansible.builtin.yum",
    "ansible.builtin.pip",
    "ansible.builtin.git",
    "ansible.builtin.user",
    "ansible.builtin.group",
    "ansible.builtin.uri",
    "ansible.builtin.get_url",
    "ansible.builtin.lineinfile",
    "ansible.builtin.blockinfile",
    "ansible.builtin.systemd",
    "ansible.builtin.raw",
    "ansible.builtin.script",
    "ansible.builtin.unarchive",
    "ansible.builtin.assert",
    "ansible.builtin.fail",
    "ansible.builtin.wait_for",
    "ansible.builtin.pause",
    "ansible.builtin.stat",
    "ansible.builtin.find",
    "ansible.builtin.replace",
    "ansible.builtin.package",
}

# Patterns in file paths that strongly suggest Ansible content
_ROLE_TASKS_RE = re.compile(r"roles/[^/]+/tasks/")
_ROLE_HANDLERS_RE = re.compile(r"roles/[^/]+/handlers/")
_ROLE_DEFAULTS_RE = re.compile(r"roles/[^/]+/defaults/")
_ROLE_VARS_RE = re.compile(r"roles/[^/]+/vars/")
_PLAYBOOKS_DIR_RE = re.compile(r"(^|/)playbooks/")
_COMMON_PLAYBOOK_RE = re.compile(
    r"(^|/)(playbook[^/]*|site|main|common|deploy|provision|setup|configure)\.(yml|yaml)$"
)
_GROUP_VARS_RE = re.compile(r"(^|/)group_vars/")
_HOST_VARS_RE = re.compile(r"(^|/)host_vars/")


class AnsibleParser(LanguageParser):
    """Parses Ansible YAML files into the navegador graph."""

    def __init__(self) -> None:
        pass  # no tree-sitter parser needed

    @staticmethod
    def is_ansible_file(path: Path, repo_root: Path | None = None) -> bool:
        """Return True if *path* looks like an Ansible YAML file."""
        if path.suffix not in (".yml", ".yaml"):
            return False

        rel = str(path)
        if repo_root is not None:
            try:
                rel = str(path.relative_to(repo_root))
            except ValueError:
                pass

        # Structural heuristics based on path
        if _ROLE_TASKS_RE.search(rel):
            return True
        if _ROLE_HANDLERS_RE.search(rel):
            return True
        if _ROLE_DEFAULTS_RE.search(rel):
            return True
        if _ROLE_VARS_RE.search(rel):
            return True
        if _PLAYBOOKS_DIR_RE.search(rel):
            return True
        if _GROUP_VARS_RE.search(rel):
            return True
        if _HOST_VARS_RE.search(rel):
            return True

        # ansible.cfg sibling in repo root
        if repo_root is not None and (repo_root / "ansible.cfg").exists():
            if _COMMON_PLAYBOOK_RE.search(rel):
                return True

        # Content-based: top-level list whose items contain "hosts:" key
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return False

        if not text.lstrip().startswith("---"):
            return False

        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError:
            return False

        if isinstance(data, list) and data:
            if any(isinstance(item, dict) and "hosts" in item for item in data):
                return True

        return False

    # ── Main entry point ─────────────────────────────────────────────────────

    def parse_file(self, path: Path, repo_root: Path, store: GraphStore) -> dict[str, int]:
        rel_path = str(path.relative_to(repo_root))
        stats = {"functions": 0, "classes": 0, "edges": 0}

        try:
            text = path.read_text(encoding="utf-8", errors="replace")
            data = yaml.safe_load(text)
        except (OSError, yaml.YAMLError) as exc:
            logger.warning("Could not parse Ansible file %s: %s", rel_path, exc)
            return stats

        if data is None:
            return stats

        # File node
        store.create_node(
            NodeLabel.File,
            {
                "name": path.name,
                "path": rel_path,
                "language": "ansible",
                "line_count": text.count("\n"),
            },
        )

        rel_str = rel_path.replace("\\", "/")

        # Dispatch based on file type
        if _ROLE_DEFAULTS_RE.search(rel_str) or _ROLE_VARS_RE.search(rel_str):
            self._parse_variable_file(data, rel_path, store, stats)
        elif _GROUP_VARS_RE.search(rel_str) or _HOST_VARS_RE.search(rel_str):
            self._parse_variable_file(data, rel_path, store, stats)
        elif _ROLE_HANDLERS_RE.search(rel_str):
            self._parse_handler_file(data, rel_path, store, stats)
        elif _ROLE_TASKS_RE.search(rel_str):
            self._parse_task_file(data, rel_path, store, stats)
        elif (
            isinstance(data, list)
            and data
            and any(isinstance(item, dict) and "hosts" in item for item in data)
        ):
            self._parse_playbook(data, rel_path, store, stats)
        elif isinstance(data, list):
            # Might be a task list (e.g. included task file)
            self._parse_task_file(data, rel_path, store, stats)
        elif isinstance(data, dict):
            # Standalone variable file
            self._parse_variable_file(data, rel_path, store, stats)

        return stats

    # ── Playbook parsing ─────────────────────────────────────────────────────

    def _parse_playbook(
        self,
        data: list,
        file_path: str,
        store: GraphStore,
        stats: dict,
    ) -> None:
        """Parse a full playbook (list of plays)."""
        playbook_name = Path(file_path).stem

        # Module node for the playbook file
        store.create_node(
            NodeLabel.Module,
            {
                "name": playbook_name,
                "file_path": file_path,
                "docstring": "",
                "semantic_type": "ansible_playbook",
            },
        )
        store.create_edge(
            NodeLabel.File,
            {"path": file_path},
            EdgeType.CONTAINS,
            NodeLabel.Module,
            {"name": playbook_name, "file_path": file_path},
        )
        stats["edges"] += 1

        for play in data:
            if not isinstance(play, dict):
                continue
            if "hosts" not in play:
                continue
            self._parse_play(play, file_path, playbook_name, store, stats)

    def _parse_play(
        self,
        play: dict,
        file_path: str,
        playbook_name: str,
        store: GraphStore,
        stats: dict,
    ) -> None:
        """Parse a single play dict."""
        play_name = play.get("name", f"play:{play.get('hosts', 'unknown')}")

        store.create_node(
            NodeLabel.Class,
            {
                "name": play_name,
                "file_path": file_path,
                "line_start": 0,
                "line_end": 0,
                "docstring": f"hosts: {play.get('hosts', '')}",
                "semantic_type": "ansible_play",
            },
        )
        store.create_edge(
            NodeLabel.Module,
            {"name": playbook_name, "file_path": file_path},
            EdgeType.CONTAINS,
            NodeLabel.Class,
            {"name": play_name, "file_path": file_path},
        )
        stats["classes"] += 1
        stats["edges"] += 1

        # Tasks
        for task_dict in play.get("tasks", []) or []:
            if isinstance(task_dict, dict):
                self._parse_task(task_dict, file_path, play_name, store, stats)

        # Pre-tasks
        for task_dict in play.get("pre_tasks", []) or []:
            if isinstance(task_dict, dict):
                self._parse_task(task_dict, file_path, play_name, store, stats)

        # Post-tasks
        for task_dict in play.get("post_tasks", []) or []:
            if isinstance(task_dict, dict):
                self._parse_task(task_dict, file_path, play_name, store, stats)

        # Handlers
        for handler_dict in play.get("handlers", []) or []:
            if isinstance(handler_dict, dict):
                self._parse_handler(handler_dict, file_path, play_name, store, stats)

        # Roles
        for role in play.get("roles", []) or []:
            self._parse_role_reference(role, file_path, play_name, store, stats)

        # Variables
        self._parse_vars_block(play.get("vars"), file_path, play_name, store, stats)

    # ── Task parsing ─────────────────────────────────────────────────────────

    def _task_name(self, task: dict) -> str:
        """Derive a task name from the dict."""
        if "name" in task and task["name"]:
            return str(task["name"])
        # Fall back to module name
        for key in task:
            if key in _ANSIBLE_MODULES:
                return key
        # Last resort: first non-meta key
        _meta_keys = {
            "name",
            "register",
            "when",
            "notify",
            "tags",
            "become",
            "become_user",
            "ignore_errors",
            "changed_when",
            "failed_when",
            "loop",
            "with_items",
            "with_dict",
            "with_fileglob",
            "until",
            "retries",
            "delay",
            "no_log",
            "environment",
            "vars",
            "listen",
            "delegate_to",
            "run_once",
            "timeout",
        }
        for key in task:
            if key not in _meta_keys:
                return key
        return "unnamed_task"

    def _parse_task(
        self,
        task: dict,
        file_path: str,
        parent_name: str,
        store: GraphStore,
        stats: dict,
    ) -> None:
        """Parse a single task dict into a Function node."""
        task_name = self._task_name(task)

        store.create_node(
            NodeLabel.Function,
            {
                "name": task_name,
                "file_path": file_path,
                "line_start": 0,
                "line_end": 0,
                "docstring": "",
                "semantic_type": "ansible_task",
            },
        )
        store.create_edge(
            NodeLabel.Class,
            {"name": parent_name, "file_path": file_path},
            EdgeType.CONTAINS,
            NodeLabel.Function,
            {"name": task_name, "file_path": file_path},
        )
        stats["functions"] += 1
        stats["edges"] += 1

        # notify: -> CALLS edge to handler
        notify = task.get("notify")
        if notify:
            if isinstance(notify, str):
                notify = [notify]
            for handler_name in notify:
                store.create_edge(
                    NodeLabel.Function,
                    {"name": task_name, "file_path": file_path},
                    EdgeType.CALLS,
                    NodeLabel.Function,
                    {"name": str(handler_name), "file_path": file_path},
                )
                stats["edges"] += 1

        # Handle block/rescue/always
        for block_key in ("block", "rescue", "always"):
            block_tasks = task.get(block_key)
            if isinstance(block_tasks, list):
                for sub_task in block_tasks:
                    if isinstance(sub_task, dict):
                        self._parse_task(sub_task, file_path, parent_name, store, stats)

    # ── Handler parsing ──────────────────────────────────────────────────────

    def _parse_handler(
        self,
        handler: dict,
        file_path: str,
        parent_name: str,
        store: GraphStore,
        stats: dict,
    ) -> None:
        """Parse a handler dict into a Function node."""
        handler_name = handler.get("name", self._task_name(handler))

        store.create_node(
            NodeLabel.Function,
            {
                "name": handler_name,
                "file_path": file_path,
                "line_start": 0,
                "line_end": 0,
                "docstring": "",
                "semantic_type": "ansible_handler",
            },
        )
        store.create_edge(
            NodeLabel.Class,
            {"name": parent_name, "file_path": file_path},
            EdgeType.CONTAINS,
            NodeLabel.Function,
            {"name": handler_name, "file_path": file_path},
        )
        stats["functions"] += 1
        stats["edges"] += 1

    # ── Role reference parsing ───────────────────────────────────────────────

    def _parse_role_reference(
        self,
        role,
        file_path: str,
        play_name: str,
        store: GraphStore,
        stats: dict,
    ) -> None:
        """Parse a role reference (string or dict with 'role' key)."""
        if isinstance(role, str):
            role_name = role
        elif isinstance(role, dict):
            role_name = role.get("role") or role.get("name", "")
        else:
            return

        if not role_name:
            return

        store.create_node(
            NodeLabel.Import,
            {
                "name": role_name,
                "file_path": file_path,
                "line_start": 0,
                "module": role_name,
                "semantic_type": "ansible_role",
            },
        )
        store.create_edge(
            NodeLabel.Class,
            {"name": play_name, "file_path": file_path},
            EdgeType.IMPORTS,
            NodeLabel.Import,
            {"name": role_name, "file_path": file_path},
        )
        stats["edges"] += 1

    # ── Variable parsing ─────────────────────────────────────────────────────

    def _parse_vars_block(
        self,
        vars_data,
        file_path: str,
        parent_name: str,
        store: GraphStore,
        stats: dict,
    ) -> None:
        """Parse a vars: block (dict) into Variable nodes."""
        if not isinstance(vars_data, dict):
            return

        for var_name, var_value in vars_data.items():
            store.create_node(
                NodeLabel.Variable,
                {
                    "name": str(var_name),
                    "file_path": file_path,
                    "line_start": 0,
                    "semantic_type": "ansible_variable",
                },
            )
            store.create_edge(
                NodeLabel.Class,
                {"name": parent_name, "file_path": file_path},
                EdgeType.CONTAINS,
                NodeLabel.Variable,
                {"name": str(var_name), "file_path": file_path},
            )
            stats["edges"] += 1

    # ── Standalone file parsers ──────────────────────────────────────────────

    def _parse_task_file(
        self,
        data,
        file_path: str,
        store: GraphStore,
        stats: dict,
    ) -> None:
        """Parse a standalone task file (roles/*/tasks/main.yml or included file)."""
        if not isinstance(data, list):
            return

        # Use file stem as a synthetic parent class
        parent_name = Path(file_path).stem
        store.create_node(
            NodeLabel.Class,
            {
                "name": parent_name,
                "file_path": file_path,
                "line_start": 0,
                "line_end": 0,
                "docstring": "",
                "semantic_type": "ansible_play",
            },
        )
        store.create_edge(
            NodeLabel.File,
            {"path": file_path},
            EdgeType.CONTAINS,
            NodeLabel.Class,
            {"name": parent_name, "file_path": file_path},
        )
        stats["classes"] += 1
        stats["edges"] += 1

        for task_dict in data:
            if isinstance(task_dict, dict):
                self._parse_task(task_dict, file_path, parent_name, store, stats)

    def _parse_handler_file(
        self,
        data,
        file_path: str,
        store: GraphStore,
        stats: dict,
    ) -> None:
        """Parse a standalone handler file (roles/*/handlers/main.yml)."""
        if not isinstance(data, list):
            return

        parent_name = Path(file_path).stem
        store.create_node(
            NodeLabel.Class,
            {
                "name": parent_name,
                "file_path": file_path,
                "line_start": 0,
                "line_end": 0,
                "docstring": "",
                "semantic_type": "ansible_play",
            },
        )
        store.create_edge(
            NodeLabel.File,
            {"path": file_path},
            EdgeType.CONTAINS,
            NodeLabel.Class,
            {"name": parent_name, "file_path": file_path},
        )
        stats["classes"] += 1
        stats["edges"] += 1

        for handler_dict in data:
            if isinstance(handler_dict, dict):
                self._parse_handler(handler_dict, file_path, parent_name, store, stats)

    def _parse_variable_file(
        self,
        data,
        file_path: str,
        store: GraphStore,
        stats: dict,
    ) -> None:
        """Parse a variable file (defaults/main.yml, vars/main.yml, group_vars/, host_vars/)."""
        if not isinstance(data, dict):
            return

        # Use file stem as a synthetic parent
        parent_name = Path(file_path).stem
        store.create_node(
            NodeLabel.Module,
            {
                "name": parent_name,
                "file_path": file_path,
                "docstring": "",
                "semantic_type": "ansible_playbook",
            },
        )
        store.create_edge(
            NodeLabel.File,
            {"path": file_path},
            EdgeType.CONTAINS,
            NodeLabel.Module,
            {"name": parent_name, "file_path": file_path},
        )
        stats["edges"] += 1

        for var_name in data:
            store.create_node(
                NodeLabel.Variable,
                {
                    "name": str(var_name),
                    "file_path": file_path,
                    "line_start": 0,
                    "semantic_type": "ansible_variable",
                },
            )
            store.create_edge(
                NodeLabel.Module,
                {"name": parent_name, "file_path": file_path},
                EdgeType.CONTAINS,
                NodeLabel.Variable,
                {"name": str(var_name), "file_path": file_path},
            )
            stats["edges"] += 1
