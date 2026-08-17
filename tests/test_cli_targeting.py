"""
CLI surface for targeting and lexical search (#184, #186).

These commands are the thing a person actually touches, and none of them were
covered by a CLI-level test — the modules underneath were. That gap is worth
closing on its own terms: a working `TrigramIndex` behind a command that
crashes on a flag is still a broken feature, and the two connection bugs found
in this milestone only appeared at the boundary between layers.

Every test drives a real embedded store through a real ingest.
"""

import json

import pytest
from click.testing import CliRunner

from navegador.cli.commands import main


def flat(result) -> str:
    """Rich wraps output at the terminal width; join it before matching."""
    return " ".join(result.output.split())


@pytest.fixture
def project(tmp_path):
    """An ingested repo, with its db path, ready for --db."""
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    (root / "src" / "auth.py").write_text(
        "def validate_token(token):\n"
        "    return check_signature(token)\n"
        "\n"
        "def check_signature(token):\n"
        "    raise ValueError('token signature invalid')\n"
    )
    (root / "src" / "api.py").write_text(
        "from src.auth import validate_token\n"
        "\n"
        "def handle_request(request):\n"
        "    return validate_token(request.token)\n"
    )
    db = str(tmp_path / "g.db")
    result = CliRunner().invoke(main, ["ingest", str(root), "--db", db])
    assert result.exit_code == 0, result.output
    return db, root


class TestGrep:
    def test_finds_a_literal(self, project):
        db, _ = project
        result = CliRunner().invoke(
            main, ["grep", "token signature invalid", "--db", db, "--reindex"]
        )
        assert result.exit_code == 0
        assert "auth.py" in flat(result)

    def test_reports_line_numbers(self, project):
        db, _ = project
        result = CliRunner().invoke(main, ["grep", "check_signature", "--db", db, "--reindex"])
        assert ":2:" in flat(result) or ":4:" in flat(result)

    def test_regex(self, project):
        db, _ = project
        result = CliRunner().invoke(
            main, ["grep", r"def \w+_token", "--db", db, "--regex", "--reindex"]
        )
        assert result.exit_code == 0
        assert "auth.py" in flat(result)

    def test_json_is_parseable(self, project):
        db, _ = project
        CliRunner().invoke(main, ["grep", "token", "--db", db, "--reindex"])
        result = CliRunner().invoke(main, ["grep", "token", "--db", db, "--json"])
        payload = json.loads(result.output)
        assert payload and {"path", "line", "text", "sha"} <= set(payload[0])

    def test_no_match_says_so_rather_than_failing(self, project):
        db, _ = project
        result = CliRunner().invoke(main, ["grep", "zzz_absent_zzz", "--db", db, "--reindex"])
        assert result.exit_code == 0
        assert "No matches" in flat(result)

    def test_ignore_case(self, project):
        db, _ = project
        CliRunner().invoke(main, ["grep", "x", "--db", db, "--reindex"])
        result = CliRunner().invoke(main, ["grep", "VALUEERROR", "--db", db, "-i"])
        assert "auth.py" in flat(result)


class TestScope:
    def test_lists_reachable_files(self, project):
        db, _ = project
        result = CliRunner().invoke(main, ["scope", "validate_token", "--db", db])
        assert result.exit_code == 0
        output = flat(result)
        assert "auth.py" in output and "api.py" in output

    def test_json_shape(self, project):
        db, _ = project
        result = CliRunner().invoke(main, ["scope", "validate_token", "--db", db, "--json"])
        payload = json.loads(result.output)
        assert payload["symbol"] == "validate_token"
        assert any("auth.py" in f for f in payload["files"])

    def test_pattern_searches_within_scope(self, project):
        db, _ = project
        CliRunner().invoke(main, ["grep", "x", "--db", db, "--reindex"])
        result = CliRunner().invoke(
            main, ["scope", "validate_token", "--db", db, "--pattern", "token"]
        )
        assert result.exit_code == 0
        assert "in scope" in flat(result)

    def test_unknown_symbol_is_reported_not_crashed(self, project):
        db, _ = project
        result = CliRunner().invoke(main, ["scope", "no_such_symbol", "--db", db])
        assert result.exit_code == 0
        assert "No scope found" in flat(result)


class TestLocate:
    def test_ranks_candidates(self, project):
        db, _ = project
        CliRunner().invoke(main, ["grep", "x", "--db", db, "--reindex"])
        result = CliRunner().invoke(main, ["locate", "validate token", "--db", db])
        assert result.exit_code == 0
        assert "auth.py" in flat(result)

    def test_json_carries_reasons(self, project):
        db, _ = project
        CliRunner().invoke(main, ["grep", "x", "--db", db, "--reindex"])
        result = CliRunner().invoke(main, ["locate", "validate_token", "--db", db, "--json"])
        payload = json.loads(result.output)
        assert payload
        assert all(c["reasons"] for c in payload), "a ranking without a reason is untrustworthy"

    def test_nothing_found_is_not_an_error(self, project):
        db, _ = project
        result = CliRunner().invoke(main, ["locate", "zzz_no_such_concept", "--db", db])
        assert result.exit_code == 0

    def test_limit(self, project):
        db, _ = project
        CliRunner().invoke(main, ["grep", "x", "--db", db, "--reindex"])
        result = CliRunner().invoke(main, ["locate", "token", "--db", db, "-n", "2", "--json"])
        assert len(json.loads(result.output)) <= 2


class TestStorageAuditRequiresAServer:
    def test_audit_explains_itself_on_an_embedded_backend(self, project):
        """
        These inspect a shared server. Pointed at an embedded file they should
        say so rather than failing obscurely.
        """
        db, _ = project
        result = CliRunner().invoke(main, ["storage", "audit", "--db", db])
        assert result.exit_code != 0
        assert "shared server" in flat(result)

    def test_prune_explains_itself_too(self, project):
        db, _ = project
        result = CliRunner().invoke(main, ["storage", "prune", "--db", db])
        assert result.exit_code != 0
        assert "shared server" in flat(result)
