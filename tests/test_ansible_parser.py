"""Tests for navegador.ingestion.ansible — AnsibleParser."""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

yaml = pytest.importorskip("yaml", reason="pyyaml not installed")

from navegador.graph.schema import EdgeType, NodeLabel  # noqa: E402
from navegador.ingestion.ansible import AnsibleParser  # noqa: E402


def _make_store():
    store = MagicMock()
    store.query.return_value = MagicMock(result_set=[])
    return store


class TestIsAnsibleFile:
    """Tests for AnsibleParser.is_ansible_file() path detection."""

    def test_role_tasks_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "roles" / "webserver" / "tasks" / "main.yml"
            p.parent.mkdir(parents=True)
            p.write_text("---\n- name: test\n  debug:\n")
            assert AnsibleParser.is_ansible_file(p, Path(tmp)) is True

    def test_role_handlers_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "roles" / "webserver" / "handlers" / "main.yml"
            p.parent.mkdir(parents=True)
            p.write_text("---\n- name: restart nginx\n  service:\n")
            assert AnsibleParser.is_ansible_file(p, Path(tmp)) is True

    def test_playbooks_dir_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "playbooks" / "deploy.yml"
            p.parent.mkdir(parents=True)
            p.write_text("---\n- hosts: all\n  tasks: []\n")
            assert AnsibleParser.is_ansible_file(p, Path(tmp)) is True

    def test_group_vars_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "group_vars" / "all.yml"
            p.parent.mkdir(parents=True)
            p.write_text("---\nhttp_port: 80\n")
            assert AnsibleParser.is_ansible_file(p, Path(tmp)) is True

    def test_random_yaml_not_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "random" / "config.yml"
            p.parent.mkdir(parents=True)
            p.write_text("---\nkey: value\n")
            assert AnsibleParser.is_ansible_file(p, Path(tmp)) is False

    def test_non_yaml_not_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "some_file.py"
            p.write_text("print('hello')\n")
            assert AnsibleParser.is_ansible_file(p, Path(tmp)) is False


class TestParsePlaybook:
    """Tests for parse_file() with a full playbook (list with hosts)."""

    def test_creates_module_class_and_function_nodes(self):
        store = _make_store()
        parser = AnsibleParser()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            playbook = tmp_path / "playbooks" / "deploy.yml"
            playbook.parent.mkdir(parents=True)
            playbook.write_text(
                "---\n"
                "- name: Deploy web app\n"
                "  hosts: webservers\n"
                "  tasks:\n"
                "    - name: Install nginx\n"
                "      apt:\n"
                "        name: nginx\n"
                "        state: present\n"
                "    - name: Start nginx\n"
                "      service:\n"
                "        name: nginx\n"
                "        state: started\n"
            )
            stats = parser.parse_file(playbook, tmp_path, store)

        assert stats["functions"] >= 2
        assert stats["classes"] >= 1

        # Verify Module node created for playbook
        create_calls = store.create_node.call_args_list
        labels = [c[0][0] for c in create_calls]
        assert NodeLabel.Module in labels
        assert NodeLabel.Class in labels
        assert NodeLabel.Function in labels

    def test_edges_created_for_containment(self):
        store = _make_store()
        parser = AnsibleParser()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            playbook = tmp_path / "playbooks" / "site.yml"
            playbook.parent.mkdir(parents=True)
            playbook.write_text(
                "---\n- name: Main play\n  hosts: all\n  tasks:\n    - name: Ping\n      ping:\n"
            )
            stats = parser.parse_file(playbook, tmp_path, store)

        assert stats["edges"] >= 3  # File->Module, Module->Class, Class->Func


class TestParseTaskFile:
    """Tests for parse_file() with a standalone task file."""

    def test_task_file_creates_class_and_functions(self):
        store = _make_store()
        parser = AnsibleParser()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            task_file = tmp_path / "roles" / "web" / "tasks" / "main.yml"
            task_file.parent.mkdir(parents=True)
            task_file.write_text(
                "---\n"
                "- name: Install packages\n"
                "  apt:\n"
                "    name: curl\n"
                "- name: Copy config\n"
                "  copy:\n"
                "    src: app.conf\n"
                "    dest: /etc/app.conf\n"
            )
            stats = parser.parse_file(task_file, tmp_path, store)

        assert stats["classes"] == 1  # synthetic parent
        assert stats["functions"] == 2


class TestParseVariableFile:
    """Tests for parse_file() with a variable file."""

    def test_variable_file_creates_variables(self):
        store = _make_store()
        parser = AnsibleParser()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            var_file = tmp_path / "roles" / "web" / "defaults" / "main.yml"
            var_file.parent.mkdir(parents=True)
            var_file.write_text("---\nhttp_port: 80\nmax_clients: 200\napp_env: production\n")
            stats = parser.parse_file(var_file, tmp_path, store)

        # Each variable creates a CONTAINS edge
        assert stats["edges"] >= 3
        create_calls = store.create_node.call_args_list
        labels = [c[0][0] for c in create_calls]
        assert labels.count(NodeLabel.Variable) == 3


class TestHandlerAndNotify:
    """Tests for handler detection and CALLS edges from notify."""

    def test_notify_creates_calls_edge(self):
        store = _make_store()
        parser = AnsibleParser()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            playbook = tmp_path / "playbooks" / "handlers.yml"
            playbook.parent.mkdir(parents=True)
            playbook.write_text(
                "---\n"
                "- name: Handler play\n"
                "  hosts: all\n"
                "  tasks:\n"
                "    - name: Update config\n"
                "      copy:\n"
                "        src: app.conf\n"
                "        dest: /etc/app.conf\n"
                "      notify: Restart app\n"
                "  handlers:\n"
                "    - name: Restart app\n"
                "      service:\n"
                "        name: app\n"
                "        state: restarted\n"
            )
            parser.parse_file(playbook, tmp_path, store)

        # Should have a CALLS edge from task to handler
        edge_calls = store.create_edge.call_args_list
        calls_edges = [c for c in edge_calls if c[0][2] == EdgeType.CALLS]
        assert len(calls_edges) >= 1
        # The CALLS edge target should be the handler name
        target_props = calls_edges[0][0][4]
        assert target_props["name"] == "Restart app"

    def test_handler_file_creates_handler_functions(self):
        store = _make_store()
        parser = AnsibleParser()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            handler_file = tmp_path / "roles" / "web" / "handlers" / "main.yml"
            handler_file.parent.mkdir(parents=True)
            handler_file.write_text(
                "---\n"
                "- name: Restart nginx\n"
                "  service:\n"
                "    name: nginx\n"
                "    state: restarted\n"
                "- name: Reload nginx\n"
                "  service:\n"
                "    name: nginx\n"
                "    state: reloaded\n"
            )
            stats = parser.parse_file(handler_file, tmp_path, store)

        assert stats["functions"] == 2
        assert stats["classes"] == 1


class TestRoleImport:
    """Tests for role import extraction."""

    def test_role_references_create_import_nodes(self):
        store = _make_store()
        parser = AnsibleParser()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            playbook = tmp_path / "playbooks" / "roles.yml"
            playbook.parent.mkdir(parents=True)
            playbook.write_text(
                "---\n"
                "- name: Apply roles\n"
                "  hosts: all\n"
                "  roles:\n"
                "    - common\n"
                "    - role: webserver\n"
                "    - { role: database, tags: db }\n"
            )
            parser.parse_file(playbook, tmp_path, store)

        create_calls = store.create_node.call_args_list
        import_nodes = [c for c in create_calls if c[0][0] == NodeLabel.Import]
        assert len(import_nodes) == 3
        names = {c[0][1]["name"] for c in import_nodes}
        assert "common" in names
        assert "webserver" in names
        assert "database" in names

        edge_calls = store.create_edge.call_args_list
        import_edges = [c for c in edge_calls if c[0][2] == EdgeType.IMPORTS]
        assert len(import_edges) == 3


class TestEmptyAndInvalidFiles:
    """Edge cases: empty files, invalid YAML, None data."""

    def test_empty_file_returns_zero_stats(self):
        store = _make_store()
        parser = AnsibleParser()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            empty = tmp_path / "roles" / "x" / "tasks" / "main.yml"
            empty.parent.mkdir(parents=True)
            empty.write_text("")
            stats = parser.parse_file(empty, tmp_path, store)

        assert stats["functions"] == 0
        assert stats["classes"] == 0

    def test_invalid_yaml_returns_zero_stats(self):
        store = _make_store()
        parser = AnsibleParser()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            bad = tmp_path / "playbooks" / "bad.yml"
            bad.parent.mkdir(parents=True)
            bad.write_text("---\n: [invalid yaml\n  {{{\n")
            stats = parser.parse_file(bad, tmp_path, store)

        assert stats["functions"] == 0
        assert stats["classes"] == 0
