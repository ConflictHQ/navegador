"""
Monorepo support — workspace-aware ingestion for Turborepo, Nx, Yarn/npm/pnpm
workspaces, Cargo workspaces, and Go workspaces.

Usage:
    from navegador.monorepo import WorkspaceDetector, MonorepoIngester

    config = WorkspaceDetector().detect("/path/to/monorepo")
    if config:
        ingester = MonorepoIngester(store)
        stats = ingester.ingest("/path/to/monorepo")
"""

from __future__ import annotations

import fnmatch
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from navegador.graph.schema import EdgeType, NodeLabel
from navegador.graph.store import GraphStore
from navegador.ingestion.parser import RepoIngester

logger = logging.getLogger(__name__)


# ── Data model ────────────────────────────────────────────────────────────────


@dataclass
class WorkspaceConfig:
    """Configuration for a detected workspace."""

    type: str  # turborepo | nx | yarn | pnpm | cargo | go
    root: Path
    packages: list[Path]
    name: str = ""

    def __post_init__(self) -> None:
        if not self.name:
            self.name = self.root.name


# ── Detector ──────────────────────────────────────────────────────────────────


class WorkspaceDetector:
    """Detects monorepo workspace configuration from a repository root."""

    def detect(self, repo_path: str | Path) -> WorkspaceConfig | None:
        """
        Auto-detect workspace type and package locations.

        Checks (in priority order):
          turbo.json          → Turborepo
          nx.json             → Nx
          pnpm-workspace.yaml → pnpm workspaces
          package.json        → Yarn/npm workspaces (if "workspaces" key present)
          Cargo.toml          → Rust workspace (if [workspace] section present)
          go.work             → Go workspace

        Returns None when no known workspace configuration is found.
        """
        root = Path(repo_path).resolve()

        # Turborepo
        if (root / "turbo.json").exists():
            packages = self._js_workspace_packages(root)
            return WorkspaceConfig(type="turborepo", root=root, packages=packages)

        # Nx
        if (root / "nx.json").exists():
            packages = self._nx_packages(root)
            return WorkspaceConfig(type="nx", root=root, packages=packages)

        # pnpm workspaces
        if (root / "pnpm-workspace.yaml").exists():
            packages = self._pnpm_packages(root)
            return WorkspaceConfig(type="pnpm", root=root, packages=packages)

        # Yarn / npm workspaces
        pkg_json = root / "package.json"
        if pkg_json.exists():
            try:
                data = json.loads(pkg_json.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                data = {}
            if "workspaces" in data:
                patterns = data["workspaces"]
                # Yarn Berry stores them under workspaces.packages
                if isinstance(patterns, dict):
                    patterns = patterns.get("packages", [])
                packages = self._glob_packages(root, patterns)
                return WorkspaceConfig(type="yarn", root=root, packages=packages)

        # Cargo workspace
        cargo_toml = root / "Cargo.toml"
        if cargo_toml.exists():
            packages = self._cargo_packages(root, cargo_toml)
            if packages is not None:
                return WorkspaceConfig(type="cargo", root=root, packages=packages)

        # Go workspace
        if (root / "go.work").exists():
            packages = self._go_packages(root)
            return WorkspaceConfig(type="go", root=root, packages=packages)

        # Bare monorepo — no tooling, just a directory of apps/services
        packages = self._bare_packages(root)
        if len(packages) >= 2:
            return WorkspaceConfig(type="bare", root=root, packages=packages)

        return None

    # ── JS-family helpers ─────────────────────────────────────────────────────

    def _js_workspace_packages(self, root: Path) -> list[Path]:
        """
        Resolve workspace package paths from package.json workspaces field.
        Falls back to scanning for package.json files one level down.
        """
        pkg_json = root / "package.json"
        if pkg_json.exists():
            try:
                data = json.loads(pkg_json.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                data = {}
            patterns = data.get("workspaces", [])
            if isinstance(patterns, dict):
                patterns = patterns.get("packages", [])
            if patterns:
                return self._glob_packages(root, patterns)
        return self._fallback_packages(root)

    def _nx_packages(self, root: Path) -> list[Path]:
        """
        Nx workspaces store packages in apps/ and libs/ by convention,
        or declare them in nx.json under "projects".
        """
        # Try reading nx.json for explicit projects
        nx_json = root / "nx.json"
        try:
            json.loads(nx_json.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass

        # Nx 16+ uses workspaceLayout or projects in project.json files
        packages: list[Path] = []
        for subdir in ("apps", "libs", "packages"):
            base = root / subdir
            if base.is_dir():
                for child in sorted(base.iterdir()):
                    if child.is_dir() and not child.name.startswith("."):
                        packages.append(child)

        if packages:
            return packages

        # Fall through to package.json-based resolution
        return self._js_workspace_packages(root)

    def _pnpm_packages(self, root: Path) -> list[Path]:
        """Parse pnpm-workspace.yaml for package glob patterns."""
        yaml_path = root / "pnpm-workspace.yaml"
        try:
            text = yaml_path.read_text(encoding="utf-8")
        except OSError:
            return self._fallback_packages(root)

        # Minimal YAML list parser — avoids a PyYAML dependency
        patterns: list[str] = []
        in_packages = False
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("packages:"):
                in_packages = True
                continue
            if in_packages:
                if stripped.startswith("-"):
                    value = stripped.lstrip("- ").strip().strip("'\"")
                    patterns.append(value)
                elif stripped and not stripped.startswith("#"):
                    in_packages = False

        if patterns:
            return self._glob_packages(root, patterns)
        return self._fallback_packages(root)

    def _glob_packages(self, root: Path, patterns: list[str]) -> list[Path]:
        """Expand workspace glob patterns (e.g. 'packages/*') to absolute Paths."""
        packages: list[Path] = []
        for pattern in patterns:
            # Skip negation patterns
            if pattern.startswith("!"):
                continue
            # Simple glob expansion using fnmatch against existing dirs
            if "*" in pattern or "?" in pattern:
                # Split at the first wildcard component
                parts = Path(pattern).parts
                base_parts: list[str] = []
                for p in parts:
                    if "*" in p or "?" in p:
                        break
                    base_parts.append(p)
                base = root / Path(*base_parts) if base_parts else root
                if base.is_dir():
                    # The wildcard component
                    wildcard_idx = len(base_parts)
                    wild = parts[wildcard_idx] if wildcard_idx < len(parts) else "*"
                    for child in sorted(base.iterdir()):
                        if child.is_dir() and fnmatch.fnmatch(child.name, wild):
                            packages.append(child)
            else:
                resolved = (root / pattern).resolve()
                if resolved.is_dir():
                    packages.append(resolved)
        return packages

    def _fallback_packages(self, root: Path) -> list[Path]:
        """Scan one level down for directories containing a package.json."""
        packages: list[Path] = []
        for child in sorted(root.iterdir()):
            if child.is_dir() and not child.name.startswith("."):
                if (child / "package.json").exists():
                    packages.append(child)
        return packages

    # ── Bare monorepo helpers ─────────────────────────────────────────────────

    # Manifests expected directly inside the package root
    _PROJECT_MANIFESTS = (
        "package.json",
        "pyproject.toml",
        "setup.py",
        "setup.cfg",
        "Cargo.toml",
        "go.mod",
        "pom.xml",
        "build.gradle",
        "build.gradle.kts",
        "Gemfile",
        "composer.json",
        "mix.exs",
    )

    # Manifests that may live one subdirectory deeper (e.g. Django's manage.py
    # inside an inner package dir: myapp/myapp/manage.py)
    _NESTED_MANIFESTS = (
        "manage.py",
        "wsgi.py",
        "asgi.py",
    )

    def _bare_packages(self, root: Path) -> list[Path]:
        """
        Detect a bare monorepo: a directory whose immediate children are
        independent apps/services with no shared workspace tooling.

        A child directory qualifies if it contains at least one recognised
        project manifest directly, or a Django/WSGI manifest one level deeper
        (e.g. myapp/myapp/manage.py).
        Non-project dirs (docs, scripts, config-only folders) are skipped.
        """
        packages: list[Path] = []
        for child in sorted(root.iterdir()):
            if not child.is_dir() or child.name.startswith("."):
                continue
            # Check top-level manifests first
            for manifest in self._PROJECT_MANIFESTS:
                if (child / manifest).exists():
                    packages.append(child)
                    break
            else:
                # Fall back: look one level deeper for Django/WSGI markers
                if self._has_nested_manifest(child):
                    packages.append(child)
        return packages

    def _has_nested_manifest(self, pkg_root: Path) -> bool:
        """Return True if any immediate subdirectory contains a nested manifest."""
        for subdir in pkg_root.iterdir():
            if not subdir.is_dir() or subdir.name.startswith("."):
                continue
            for manifest in self._NESTED_MANIFESTS:
                if (subdir / manifest).exists():
                    return True
        return False

    # ── Cargo helpers ─────────────────────────────────────────────────────────

    def _cargo_packages(self, root: Path, cargo_toml: Path) -> list[Path] | None:
        """
        Parse Cargo.toml for a [workspace] section and return member paths.
        Returns None if this is not a workspace Cargo.toml.
        """
        try:
            text = cargo_toml.read_text(encoding="utf-8")
        except OSError:
            return None

        if "[workspace]" not in text:
            return None

        # Minimal TOML parser for the members list
        members: list[str] = []
        in_workspace = False
        in_members = False
        for line in text.splitlines():
            stripped = line.strip()
            if stripped == "[workspace]":
                in_workspace = True
                in_members = False
                continue
            if in_workspace:
                if stripped.startswith("[") and stripped != "[workspace]":
                    in_workspace = False
                    in_members = False
                    continue
                if stripped.startswith("members"):
                    in_members = True
                if in_members:
                    # Collect quoted strings from this line and continuation lines
                    for token in stripped.split('"'):
                        candidate = token.strip().strip(",").strip()
                        if candidate and candidate not in ("=", "[", "]", "members"):
                            members.append(candidate)
                    if stripped.endswith("]"):
                        in_members = False

        packages: list[Path] = []
        for member in members:
            if "*" in member:
                base = root / member.split("*")[0].rstrip("/")
                if base.is_dir():
                    for child in sorted(base.iterdir()):
                        if child.is_dir():
                            packages.append(child)
            else:
                resolved = (root / member).resolve()
                if resolved.is_dir():
                    packages.append(resolved)
        return packages

    # ── Go helpers ────────────────────────────────────────────────────────────

    def _go_packages(self, root: Path) -> list[Path]:
        """Parse go.work for module paths (use directives)."""
        go_work = root / "go.work"
        try:
            text = go_work.read_text(encoding="utf-8")
        except OSError:
            return self._fallback_packages(root)

        packages: list[Path] = []
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("use "):
                path_str = stripped[4:].strip().strip("()")
                if path_str:
                    resolved = (root / path_str).resolve()
                    if resolved.is_dir():
                        packages.append(resolved)
        return packages


# ── Ingester ──────────────────────────────────────────────────────────────────


class MonorepoIngester:
    """Ingest a monorepo respecting workspace boundaries."""

    def __init__(self, store: GraphStore) -> None:
        self.store = store

    def ingest(
        self,
        repo_path: str | Path,
        clear: bool = False,
    ) -> dict[str, Any]:
        """
        Detect workspace, ingest each package, and create DEPENDS_ON edges
        between packages that reference each other.

        Steps:
          1. Detect workspace configuration.
          2. Optionally clear the graph.
          3. Create a root Repository node for the monorepo.
          4. For each package: create a Repository node, then ingest files.
          5. Parse inter-package dependency declarations and create DEPENDS_ON edges.

        Returns aggregated stats plus a "packages" count.
        """
        repo_path = Path(repo_path).resolve()
        if not repo_path.exists():
            raise FileNotFoundError(f"Repository not found: {repo_path}")

        detector = WorkspaceDetector()
        config = detector.detect(repo_path)

        if config is None:
            logger.warning(
                "No workspace configuration found at %s; falling back to single-repo ingest",
                repo_path,
            )
            ingester = RepoIngester(self.store)
            stats = ingester.ingest(repo_path, clear=clear)
            stats["packages"] = 0
            stats["workspace_type"] = "none"
            return stats

        if clear:
            self.store.clear()

        # Root node for the whole monorepo
        self.store.create_node(
            NodeLabel.Repository,
            {
                "name": config.name,
                "path": str(repo_path),
                "file_path": "",
            },
        )

        aggregate: dict[str, int] = {
            "files": 0,
            "functions": 0,
            "classes": 0,
            "edges": 0,
            "skipped": 0,
        }

        ingested_packages: list[tuple[str, Path]] = []

        for pkg_path in config.packages:
            if not pkg_path.is_dir():
                continue

            pkg_name = pkg_path.name
            logger.info("Ingesting package: %s (%s)", pkg_name, pkg_path)

            # Package-level Repository node
            self.store.create_node(
                NodeLabel.Repository,
                {
                    "name": pkg_name,
                    "path": str(pkg_path),
                    "file_path": "",
                },
            )

            # Link package to monorepo root
            self.store.create_edge(
                from_label=NodeLabel.Repository,
                from_key={"name": config.name, "path": str(repo_path)},
                edge_type=EdgeType.CONTAINS,
                to_label=NodeLabel.Repository,
                to_key={"name": pkg_name, "path": str(pkg_path)},
            )

            # Ingest files in this package
            pkg_ingester = RepoIngester(self.store)
            try:
                pkg_stats = pkg_ingester.ingest(pkg_path, clear=False)
                for key in ("files", "functions", "classes", "edges", "skipped"):
                    aggregate[key] = aggregate.get(key, 0) + pkg_stats.get(key, 0)
                ingested_packages.append((pkg_name, pkg_path))
            except Exception:
                logger.exception("Failed to ingest package %s", pkg_path)

        # Create inter-package DEPENDS_ON edges
        dep_edges = self._create_dependency_edges(config, ingested_packages)
        aggregate["edges"] += dep_edges
        aggregate["packages"] = len(ingested_packages)
        aggregate["workspace_type"] = config.type

        logger.info(
            "Monorepo ingest complete (%s): %d packages, %d files",
            config.type,
            len(ingested_packages),
            aggregate["files"],
        )
        return aggregate

    def _create_dependency_edges(
        self,
        config: WorkspaceConfig,
        packages: list[tuple[str, Path]],
    ) -> int:
        """
        Parse each package's manifest (package.json / Cargo.toml / go.mod)
        and create DEPENDS_ON edges for references to sibling packages.

        Returns the number of edges created.
        """
        pkg_names = {name for name, _ in packages}
        edges_created = 0

        for pkg_name, pkg_path in packages:
            deps = self._read_package_deps(config.type, pkg_path)
            for dep_name in deps:
                # Normalise: strip org scope (@scope/name → name)
                bare = dep_name.lstrip("@").split("/")[-1] if "/" in dep_name else dep_name
                if dep_name in pkg_names or bare in pkg_names:
                    target = dep_name if dep_name in pkg_names else bare
                    try:
                        self.store.create_edge(
                            from_label=NodeLabel.Repository,
                            from_key={"name": pkg_name},
                            edge_type=EdgeType.DEPENDS_ON,
                            to_label=NodeLabel.Repository,
                            to_key={"name": target},
                        )
                        edges_created += 1
                    except Exception:
                        logger.debug("Could not create DEPENDS_ON edge %s → %s", pkg_name, target)

        return edges_created

    def _read_package_deps(self, workspace_type: str, pkg_path: Path) -> list[str]:
        """Return a flat list of declared dependency names for a package."""
        if workspace_type in ("turborepo", "nx", "yarn", "pnpm"):
            return self._js_deps(pkg_path)
        if workspace_type == "cargo":
            return self._cargo_deps(pkg_path)
        if workspace_type == "go":
            return self._go_deps(pkg_path)
        if workspace_type == "bare":
            # Try all known manifest parsers and merge results
            deps: list[str] = []
            deps.extend(self._js_deps(pkg_path))
            deps.extend(self._cargo_deps(pkg_path))
            deps.extend(self._go_deps(pkg_path))
            return deps
        return []

    def _js_deps(self, pkg_path: Path) -> list[str]:
        pkg_json = pkg_path / "package.json"
        if not pkg_json.exists():
            return []
        try:
            data = json.loads(pkg_json.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        all_deps: dict[str, str] = {}
        for key in ("dependencies", "devDependencies", "peerDependencies"):
            all_deps.update(data.get(key, {}))
        return list(all_deps.keys())

    def _cargo_deps(self, pkg_path: Path) -> list[str]:
        cargo_toml = pkg_path / "Cargo.toml"
        if not cargo_toml.exists():
            return []
        try:
            text = cargo_toml.read_text(encoding="utf-8")
        except OSError:
            return []
        deps: list[str] = []
        in_deps = False
        for line in text.splitlines():
            stripped = line.strip()
            if stripped in ("[dependencies]", "[dev-dependencies]", "[build-dependencies]"):
                in_deps = True
                continue
            if stripped.startswith("[") and in_deps:
                in_deps = False
                continue
            if in_deps and "=" in stripped and not stripped.startswith("#"):
                name = stripped.split("=")[0].strip()
                if name:
                    deps.append(name)
        return deps

    def _go_deps(self, pkg_path: Path) -> list[str]:
        go_mod = pkg_path / "go.mod"
        if not go_mod.exists():
            return []
        try:
            text = go_mod.read_text(encoding="utf-8")
        except OSError:
            return []
        deps: list[str] = []
        in_require = False
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("require ("):
                in_require = True
                continue
            if stripped == ")":
                in_require = False
                continue
            if in_require and stripped and not stripped.startswith("//"):
                parts = stripped.split()
                if parts:
                    deps.append(parts[0])
            elif stripped.startswith("require ") and not stripped.startswith("require ("):
                parts = stripped.split()
                if len(parts) >= 2:
                    deps.append(parts[1])
        return deps
