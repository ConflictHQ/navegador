"""
OpenAPI and GraphQL schema ingestion — API contracts as graph nodes.

Parses OpenAPI/Swagger YAML or JSON files and GraphQL schema files, then
creates API endpoint nodes in the navegador graph.

Usage:
    from navegador.api_schema import APISchemaIngester

    ingester = APISchemaIngester(store)
    stats = ingester.ingest_openapi("/path/to/openapi.yaml")
    stats = ingester.ingest_graphql("/path/to/schema.graphql")
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from navegador.graph.schema import EdgeType, NodeLabel
from navegador.graph.store import GraphStore

logger = logging.getLogger(__name__)

# ── New node label for API endpoints ─────────────────────────────────────────
#
# We store API endpoints as Function nodes with a synthetic label convention
# so they appear in search results alongside regular code symbols.  A dedicated
# label would require schema migration; using Function keeps things simple and
# compatible with the existing graph.
#
# Alternatively callers can use the raw create_node with a custom label string.

_API_NODE_LABEL = "Function"  # reuse for discoverability


class APISchemaIngester:
    """
    Ingest API schema files (OpenAPI YAML/JSON, GraphQL SDL) as graph nodes.

    Each endpoint / type becomes a Function-labelled node with a distinctive
    file_path prefix so they can be queried separately.
    """

    def __init__(self, store: GraphStore) -> None:
        self.store = store

    # ── OpenAPI ───────────────────────────────────────────────────────────────

    def ingest_openapi(self, path: str | Path) -> dict[str, Any]:
        """
        Parse an OpenAPI 2.x / 3.x YAML or JSON file.

        Each path+method combination becomes a node.  Returns stats dict with
        keys: endpoints, schemas.
        """
        path = Path(path)
        spec = self._load_yaml_or_json(path)
        if spec is None:
            return {"endpoints": 0, "schemas": 0}

        endpoints = 0
        schemas = 0
        base_url = str(path)

        # ── Paths / endpoints ─────────────────────────────────────────────────
        for api_path, path_item in (spec.get("paths") or {}).items():
            if not isinstance(path_item, dict):
                continue
            for method in ("get", "post", "put", "patch", "delete", "head", "options"):
                operation = path_item.get(method)
                if not isinstance(operation, dict):
                    continue

                op_id = operation.get("operationId") or f"{method.upper()} {api_path}"
                summary = operation.get("summary") or operation.get("description") or ""
                tags = ", ".join(operation.get("tags") or [])

                self.store.create_node(
                    _API_NODE_LABEL,
                    {
                        "name": op_id,
                        "file_path": base_url,
                        "line_start": 0,
                        "line_end": 0,
                        "docstring": summary,
                        "source": "",
                        "signature": f"{method.upper()} {api_path}",
                        "domain": tags,
                    },
                )
                endpoints += 1

        # ── Component schemas / definitions ───────────────────────────────────
        component_schemas = (
            (spec.get("components") or {}).get("schemas")
            or spec.get("definitions")
            or {}
        )
        for schema_name, schema_body in component_schemas.items():
            if not isinstance(schema_body, dict):
                continue
            description = schema_body.get("description") or ""
            self.store.create_node(
                "Class",
                {
                    "name": schema_name,
                    "file_path": base_url,
                    "line_start": 0,
                    "line_end": 0,
                    "docstring": description,
                    "source": "",
                },
            )
            schemas += 1

        stats = {"endpoints": endpoints, "schemas": schemas}
        logger.info("APISchemaIngester (OpenAPI): %s", stats)
        return stats

    # ── GraphQL ───────────────────────────────────────────────────────────────

    def ingest_graphql(self, path: str | Path) -> dict[str, Any]:
        """
        Parse a GraphQL SDL schema file using regex-based extraction.

        Types (type, input, interface, enum, union) become Class nodes.
        Query / Mutation / Subscription fields become Function nodes.
        Returns stats dict with keys: types, fields.
        """
        path = Path(path)
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            logger.warning("APISchemaIngester: cannot read %s: %s", path, exc)
            return {"types": 0, "fields": 0}

        base_url = str(path)
        types_created = 0
        fields_created = 0

        # ── Type definitions ──────────────────────────────────────────────────
        # Matches: type Foo { ... }  /  input Bar { ... }  /  interface X { ... }
        type_pattern = re.compile(
            r"(?:^|\n)\s*(?:type|input|interface|enum|union)\s+(\w+)"
            r"(?:[^{]*)?\{([^}]*)\}",
            re.MULTILINE | re.DOTALL,
        )

        root_types = {"Query", "Mutation", "Subscription"}

        for m in type_pattern.finditer(text):
            type_name = m.group(1)
            body = m.group(2)

            if type_name in root_types:
                # Fields on Query / Mutation / Subscription → Function nodes
                field_pattern = re.compile(
                    r"^\s*(\w+)\s*(?:\([^)]*\))?\s*:\s*([^\n!]+)", re.MULTILINE
                )
                for fm in field_pattern.finditer(body):
                    field_name = fm.group(1).strip()
                    return_type = fm.group(2).strip().rstrip(",")
                    self.store.create_node(
                        _API_NODE_LABEL,
                        {
                            "name": field_name,
                            "file_path": base_url,
                            "line_start": 0,
                            "line_end": 0,
                            "docstring": "",
                            "source": "",
                            "signature": f"{type_name}.{field_name}: {return_type}",
                            "domain": type_name,
                        },
                    )
                    fields_created += 1
            else:
                # Regular type → Class node
                self.store.create_node(
                    "Class",
                    {
                        "name": type_name,
                        "file_path": base_url,
                        "line_start": 0,
                        "line_end": 0,
                        "docstring": "",
                        "source": "",
                    },
                )
                types_created += 1

        stats = {"types": types_created, "fields": fields_created}
        logger.info("APISchemaIngester (GraphQL): %s", stats)
        return stats

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _load_yaml_or_json(self, path: Path) -> dict[str, Any] | None:
        """Load a YAML or JSON file using stdlib only."""
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            logger.warning("APISchemaIngester: cannot read %s: %s", path, exc)
            return None

        suffix = path.suffix.lower()

        if suffix in (".yaml", ".yml"):
            return self._parse_yaml(text)
        elif suffix == ".json":
            try:
                return json.loads(text)
            except json.JSONDecodeError as exc:
                logger.warning("APISchemaIngester: JSON parse error in %s: %s", path, exc)
                return None
        else:
            # Try JSON first, then YAML
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return self._parse_yaml(text)

    def _parse_yaml(self, text: str) -> dict[str, Any] | None:
        """
        Minimal YAML parser using stdlib only (no PyYAML dependency).

        Sufficient for the simple flat/nested structure of OpenAPI specs.
        Falls back to PyYAML if available.
        """
        try:
            import yaml  # type: ignore[import]
            return yaml.safe_load(text)
        except ImportError:
            pass

        # Minimal hand-rolled YAML → dict for simple key: value structures
        return _minimal_yaml_load(text)


# ── Minimal YAML loader (stdlib only) ─────────────────────────────────────────


def _minimal_yaml_load(text: str) -> dict[str, Any]:
    """
    Extremely simplified YAML loader for flat/shallow OpenAPI specs.

    Handles:  key: value, key: 'string', key: "string", nested dicts via
    indentation, lists via '- item'.  Does NOT handle anchors, multi-line
    values, or complex YAML features.
    """
    lines = text.splitlines()
    result: dict[str, Any] = {}
    stack: list[tuple[int, dict | list]] = [(0, result)]

    for raw_line in lines:
        if not raw_line.strip() or raw_line.strip().startswith("#"):
            continue

        indent = len(raw_line) - len(raw_line.lstrip())
        stripped = raw_line.strip()

        # Pop stack to current indent level
        while len(stack) > 1 and stack[-1][0] >= indent:
            # Only pop if the indent is strictly less
            if stack[-1][0] > indent:
                stack.pop()
            else:
                break

        current = stack[-1][1]

        if stripped.startswith("- "):
            # List item
            value = stripped[2:].strip()
            if isinstance(current, list):
                current.append(_yaml_scalar(value))
        elif ":" in stripped:
            key, _, val = stripped.partition(":")
            key = key.strip()
            val = val.strip()
            if isinstance(current, dict):
                if val == "" or val == "|" or val == ">":
                    # Nested mapping or block scalar → placeholder dict
                    child: dict[str, Any] = {}
                    current[key] = child
                    stack.append((indent + 2, child))
                else:
                    current[key] = _yaml_scalar(val)

    return result


def _yaml_scalar(value: str) -> Any:
    """Convert a raw YAML scalar string to a Python value."""
    if value in ("true", "True", "yes"):
        return True
    if value in ("false", "False", "no"):
        return False
    if value in ("null", "~", ""):
        return None
    # Strip quotes
    if (value.startswith('"') and value.endswith('"')) or (
        value.startswith("'") and value.endswith("'")
    ):
        return value[1:-1]
    # Try int / float
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    return value
