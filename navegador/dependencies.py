"""
External dependency nodes — track npm/pip/cargo packages in the knowledge graph.

Issue: #58

Parses package manifests and creates ExternalDependency nodes with DEPENDS_ON
edges to the repository, enabling queries like "what packages does this repo
depend on?" and cross-repo dependency analysis.

Since ExternalDependency is not (yet) a first-class NodeLabel, we use
NodeLabel.Concept with domain="external_dependency" and a "package_manager"
property encoded in the description.

Usage::

    from navegador.dependencies import DependencyIngester

    ing = DependencyIngester(store)

    stats = ing.ingest_npm("package.json")
    stats = ing.ingest_pip("requirements.txt")   # or pyproject.toml
    stats = ing.ingest_cargo("Cargo.toml")
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from navegador.graph.schema import EdgeType, NodeLabel
from navegador.graph.store import GraphStore

logger = logging.getLogger(__name__)

# We represent external dependencies as Concept nodes with this domain tag
_DOMAIN = "external_dependency"


class DependencyIngester:
    """
    Parses package manifests and writes ExternalDependency nodes into the graph.

    Each dependency becomes a Concept node::

        name        — "<package>@<version>"  (e.g. "react@^18.0.0")
        description — "<package_manager>:<package>"
        domain      — "external_dependency"
        status      — version specifier

    A DEPENDS_ON edge is created from the source File node (the manifest path)
    to each dependency Concept node.
    """

    def __init__(self, store: GraphStore) -> None:
        self.store = store

    # ── npm / package.json ────────────────────────────────────────────────────

    def ingest_npm(self, package_json_path: str | Path) -> dict[str, Any]:
        """
        Parse a ``package.json`` and ingest all dependency entries.

        Reads ``dependencies``, ``devDependencies``, and
        ``peerDependencies``.

        Parameters
        ----------
        package_json_path:
            Absolute or relative path to ``package.json``.

        Returns
        -------
        dict with key ``packages`` (int count ingested)
        """
        import json

        p = Path(package_json_path).resolve()
        data = json.loads(p.read_text(encoding="utf-8"))

        dep_sections = ["dependencies", "devDependencies", "peerDependencies"]
        packages: dict[str, str] = {}
        for section in dep_sections:
            packages.update(data.get(section, {}) or {})

        count = 0
        for pkg_name, version in packages.items():
            self._upsert_dep("npm", pkg_name, version, str(p))
            count += 1

        logger.info("DependencyIngester.ingest_npm(%s): %d packages", p, count)
        return {"packages": count}

    # ── pip / requirements.txt / pyproject.toml ───────────────────────────────

    def ingest_pip(self, requirements_path: str | Path) -> dict[str, Any]:
        """
        Parse a ``requirements.txt`` or ``pyproject.toml`` and ingest all
        Python dependency entries.

        For ``pyproject.toml`` reads ``[project].dependencies`` and
        ``[project.optional-dependencies]``.

        Parameters
        ----------
        requirements_path:
            Absolute or relative path to ``requirements.txt`` or
            ``pyproject.toml``.

        Returns
        -------
        dict with key ``packages`` (int count ingested)
        """
        p = Path(requirements_path).resolve()
        name_lower = p.name.lower()

        if name_lower == "pyproject.toml":
            packages = self._parse_pyproject(p)
        else:
            packages = self._parse_requirements_txt(p)

        count = 0
        for pkg_name, version in packages.items():
            self._upsert_dep("pip", pkg_name, version, str(p))
            count += 1

        logger.info("DependencyIngester.ingest_pip(%s): %d packages", p, count)
        return {"packages": count}

    # ── cargo / Cargo.toml ────────────────────────────────────────────────────

    def ingest_cargo(self, cargo_toml_path: str | Path) -> dict[str, Any]:
        """
        Parse a ``Cargo.toml`` and ingest all Rust crate dependencies.

        Reads ``[dependencies]``, ``[dev-dependencies]``, and
        ``[build-dependencies]``.

        Parameters
        ----------
        cargo_toml_path:
            Absolute or relative path to ``Cargo.toml``.

        Returns
        -------
        dict with key ``packages`` (int count ingested)
        """
        p = Path(cargo_toml_path).resolve()
        packages = self._parse_cargo_toml(p)

        count = 0
        for pkg_name, version in packages.items():
            self._upsert_dep("cargo", pkg_name, version, str(p))
            count += 1

        logger.info("DependencyIngester.ingest_cargo(%s): %d packages", p, count)
        return {"packages": count}

    # ── Core helpers ──────────────────────────────────────────────────────────

    def _upsert_dep(
        self,
        package_manager: str,
        pkg_name: str,
        version: str,
        source_path: str,
    ) -> None:
        """Write a single dependency node and a DEPENDS_ON edge from the manifest."""
        node_name = f"{pkg_name}@{version}" if version else pkg_name
        self.store.create_node(
            NodeLabel.Concept,
            {
                "name": node_name,
                "description": f"{package_manager}:{pkg_name}",
                "domain": _DOMAIN,
                "status": version,
            },
        )
        # Ensure domain node exists
        self.store.create_node(
            NodeLabel.Domain, {"name": _DOMAIN, "description": "External package dependencies"}
        )
        # Ensure the manifest File node exists (minimal representation)
        self.store.create_node(
            NodeLabel.File,
            {
                "name": Path(source_path).name,
                "path": source_path,
                "language": package_manager,
                "size": 0,
                "line_count": 0,
                "content_hash": "",
            },
        )
        # File -DEPENDS_ON-> ExternalDependency concept
        self.store.create_edge(
            NodeLabel.File,
            {"name": Path(source_path).name},
            EdgeType.DEPENDS_ON,
            NodeLabel.Concept,
            {"name": node_name},
        )

    # ── Parsers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _parse_requirements_txt(path: Path) -> dict[str, str]:
        """Parse requirements.txt into {package_name: version_spec}."""
        packages: dict[str, str] = {}
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            # Skip blanks, comments, options
            if not line or line.startswith("#") or line.startswith("-"):
                continue
            # Strip inline comments
            line = line.split("#", 1)[0].strip()
            # Handle VCS / URL requirements
            if line.startswith(("git+", "http://", "https://")):
                # Best-effort: use the egg= fragment as name
                m = re.search(r"egg=([A-Za-z0-9_.-]+)", line)
                if m:
                    packages[m.group(1)] = "vcs"
                continue
            # Standard: package[extras]>=version
            m = re.match(r"([A-Za-z0-9_.-]+)(\[.*?\])?\s*([><=!~^].+)?", line)
            if m:
                name = m.group(1)
                version = (m.group(3) or "").strip()
                packages[name] = version
        return packages

    @staticmethod
    def _parse_pyproject(path: Path) -> dict[str, str]:
        """Parse pyproject.toml dependencies (best-effort without toml library)."""
        packages: dict[str, str] = {}
        in_deps = False
        in_optional = False
        text = path.read_text(encoding="utf-8")

        for line in text.splitlines():
            stripped = line.strip()

            # Section headers
            if stripped.startswith("["):
                in_deps = stripped in ("[project]", "[project.dependencies]")
                in_optional = stripped == "[project.optional-dependencies]"
                # Handle inline table start on same line
                if "dependencies" in stripped and "=" in stripped:
                    in_deps = True
                continue

            if not (in_deps or in_optional):
                continue

            # Try to extract quoted dependency strings like:
            # "requests>=2.28", 'flask[async]', etc.
            for match in re.finditer(r'"([^"]+)"|\'([^\']+)\'', line):
                raw = match.group(1) or match.group(2)
                m2 = re.match(r"([A-Za-z0-9_.-]+)(\[.*?\])?\s*([><=!~^].+)?", raw)
                if m2:
                    name = m2.group(1)
                    version = (m2.group(3) or "").strip()
                    packages[name] = version

        return packages

    @staticmethod
    def _parse_cargo_toml(path: Path) -> dict[str, str]:
        """Parse Cargo.toml dependencies (best-effort without toml library)."""
        packages: dict[str, str] = {}
        dep_sections = {"[dependencies]", "[dev-dependencies]", "[build-dependencies]"}
        in_dep_section = False
        text = path.read_text(encoding="utf-8")

        for line in text.splitlines():
            stripped = line.strip()

            if stripped.startswith("["):
                # Normalise: "[dependencies]" or "[target.'...'.dependencies]"
                in_dep_section = (
                    stripped in dep_sections
                    or re.match(r"\[.*dependencies\]", stripped) is not None
                )
                continue

            if not in_dep_section:
                continue

            if not stripped or stripped.startswith("#"):
                continue

            # Simple: serde = "1.0"
            m = re.match(r'^([A-Za-z0-9_-]+)\s*=\s*"([^"]*)"', stripped)
            if m:
                packages[m.group(1)] = m.group(2)
                continue

            # Inline table: serde = { version = "1.0", features = [...] }
            m2 = re.match(r'^([A-Za-z0-9_-]+)\s*=\s*\{.*version\s*=\s*"([^"]*)"', stripped)
            if m2:
                packages[m2.group(1)] = m2.group(2)

        return packages
