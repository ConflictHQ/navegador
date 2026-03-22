"""Tests for context bundle serialization (no graph required)."""

from navegador.context.loader import ContextBundle, ContextNode


def _make_bundle():
    target = ContextNode(
        type="Function",
        name="get_user",
        file_path="src/auth.py",
        line_start=42,
        docstring="Return a user by ID.",
        signature="def get_user(user_id: int) -> User:",
    )
    nodes = [
        ContextNode(type="Function", name="validate_token", file_path="src/auth.py", line_start=10),
        ContextNode(type="Class", name="User", file_path="src/models.py", line_start=5),
    ]
    edges = [
        {"from": "get_user", "type": "CALLS", "to": "validate_token"},
    ]
    return ContextBundle(target=target, nodes=nodes, edges=edges)


def test_bundle_to_dict():
    bundle = _make_bundle()
    d = bundle.to_dict()
    assert d["target"]["name"] == "get_user"
    assert len(d["nodes"]) == 2
    assert len(d["edges"]) == 1


def test_bundle_to_json():
    import json
    bundle = _make_bundle()
    data = json.loads(bundle.to_json())
    assert data["target"]["type"] == "Function"


def test_bundle_to_markdown():
    bundle = _make_bundle()
    md = bundle.to_markdown()
    assert "get_user" in md
    assert "CALLS" in md
    assert "validate_token" in md
