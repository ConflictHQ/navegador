"""
Tests for navegador v0.4 features:
  #16 — Multi-repo support         (MultiRepoManager)
  #26 — Coordinated rename         (SymbolRenamer)
  #39 — CODEOWNERS integration     (CodeownersIngester)
  #40 — ADR ingestion              (ADRIngester)
  #41 — OpenAPI / GraphQL schema   (APISchemaIngester)
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from navegador.cli.commands import main

# ── Shared helpers ────────────────────────────────────────────────────────────


def _mock_store():
    store = MagicMock()
    store.query.return_value = MagicMock(result_set=[])
    return store


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


# ═════════════════════════════════════════════════════════════════════════════
# #16 — MultiRepoManager
# ═════════════════════════════════════════════════════════════════════════════


def real_store(tmp_path):
    """An embedded store — real FalkorDB, milliseconds to build."""
    from navegador.graph import GraphStore

    return GraphStore.sqlite(str(tmp_path / "v04.db"))


def git_repo(path, remote=""):
    """A committed git repo, so repo identity resolves from the remote."""
    import subprocess

    (path / "src").mkdir(parents=True, exist_ok=True)
    (path / "src" / "mod.py").write_text(
        "def entry():\n    return helper()\n\ndef helper():\n    return 1\n"
    )

    def git(*args):
        subprocess.run(["git", *args], cwd=path, check=True, capture_output=True)

    git("init", "-q")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    if remote:
        git("remote", "add", "origin", remote)
    git("add", "-A")
    git("commit", "-qm", "initial")
    return path


class TestMultiRepoManagerAddRepo:
    """
    Registration against a real store.

    These used to assert against a MagicMock's call_args, which meant they
    described the arguments passed rather than the graph produced — and so
    they could not notice that registration and ingest were writing two
    different Repository nodes (#179).
    """

    def test_creates_one_repository_node(self, tmp_path):
        from navegador.multirepo import MultiRepoManager

        store = real_store(tmp_path)
        repo = git_repo(tmp_path / "backend", "git@github.com:acme/backend.git")
        MultiRepoManager(store).add_repo("backend", repo)

        rows = store.query("MATCH (r:Repository) RETURN r.name, r.path").result_set
        assert len(rows) == 1

    def test_keyed_by_portable_identity_not_the_checkout_path(self, tmp_path):
        """An absolute path is not an identity and leaks local layout (#145)."""
        from navegador.multirepo import MultiRepoManager

        store = real_store(tmp_path)
        repo = git_repo(tmp_path / "backend", "git@github.com:acme/backend.git")
        MultiRepoManager(store).add_repo("backend", repo)

        rows = store.query("MATCH (r:Repository) RETURN r.path").result_set
        assert rows[0][0] == "acme/backend"

    def test_checkout_location_is_still_recorded(self, tmp_path):
        """
        The registry is persisted and read back to know what to ingest, so the
        machine-local path has to live somewhere — just not in `path`.
        """
        from navegador.multirepo import MultiRepoManager

        store = real_store(tmp_path)
        repo = git_repo(tmp_path / "backend", "git@github.com:acme/backend.git")
        MultiRepoManager(store).add_repo("backend", repo)

        rows = store.query("MATCH (r:Repository) RETURN r.file_path").result_set
        assert Path(rows[0][0]).is_absolute()


class TestMultiRepoManagerListRepos:
    def test_returns_empty_list_when_no_repos(self, tmp_path):
        from navegador.multirepo import MultiRepoManager

        assert MultiRepoManager(real_store(tmp_path)).list_repos() == []

    def test_reports_location_and_identity_separately(self, tmp_path):
        from navegador.multirepo import MultiRepoManager

        store = real_store(tmp_path)
        repo = git_repo(tmp_path / "backend", "git@github.com:acme/backend.git")
        manager = MultiRepoManager(store)
        manager.add_repo("backend", repo)

        (entry,) = manager.list_repos()
        assert entry["identity"] == "acme/backend"
        assert Path(entry["path"]).is_absolute()

    def test_older_graphs_without_file_path_still_list(self, tmp_path):
        """A graph written before #179 has no file_path; identity stands in."""
        from navegador.multirepo import MultiRepoManager

        store = real_store(tmp_path)
        store.query("CREATE (:Repository {name:'old', path:'acme/old'})")

        (entry,) = MultiRepoManager(store).list_repos()
        assert entry["path"] == "acme/old"


class TestMultiRepoManagerIngestAll:
    def test_ingests_each_registered_repo(self, tmp_path):
        from navegador.multirepo import MultiRepoManager

        store = real_store(tmp_path)
        repo = git_repo(tmp_path / "svc", "git@github.com:acme/svc.git")
        manager = MultiRepoManager(store)
        manager.add_repo("svc", repo)

        summary = manager.ingest_all()
        assert summary and next(iter(summary.values()))["files"] > 0

    def test_files_attach_to_the_registered_repository(self, tmp_path):
        """The bug's teeth: the registration node used to have zero members."""
        from navegador.multirepo import MultiRepoManager

        store = real_store(tmp_path)
        repo = git_repo(tmp_path / "svc", "git@github.com:acme/svc.git")
        manager = MultiRepoManager(store)
        manager.add_repo("svc", repo)
        manager.ingest_all()

        rows = store.query(
            "MATCH (f)-[:BELONGS_TO]->(r:Repository {path:'acme/svc'}) RETURN count(f)"
        ).result_set
        assert int(rows[0][0]) > 0

    def test_returns_empty_when_no_repos(self, tmp_path):
        from navegador.multirepo import MultiRepoManager

        assert MultiRepoManager(real_store(tmp_path)).ingest_all() == {}

    def test_clear_empties_the_graph_first(self, tmp_path):
        from navegador.multirepo import MultiRepoManager

        store = real_store(tmp_path)
        store.query("CREATE (:Function {name:'stale_leftover', file_path:'gone.py'})")
        repo = git_repo(tmp_path / "svc", "git@github.com:acme/svc.git")
        manager = MultiRepoManager(store)
        manager.add_repo("svc", repo)
        manager.ingest_all(clear=True)

        rows = store.query("MATCH (n:Function {name:'stale_leftover'}) RETURN count(n)").result_set
        assert int(rows[0][0]) == 0


class TestMultiRepoManagerCrossRepoSearch:
    def test_returns_results(self):
        from navegador.multirepo import MultiRepoManager

        store = _mock_store()
        store.query.return_value = MagicMock(result_set=[["Function", "authenticate", "auth.py"]])
        mgr = MultiRepoManager(store)
        results = mgr.cross_repo_search("authenticate")
        assert len(results) == 1
        assert results[0]["name"] == "authenticate"

    def test_empty_when_no_match(self):
        from navegador.multirepo import MultiRepoManager

        store = _mock_store()
        store.query.return_value = MagicMock(result_set=[])
        mgr = MultiRepoManager(store)
        assert mgr.cross_repo_search("zzz_nonexistent") == []

    def test_limit_is_applied(self):
        from navegador.multirepo import MultiRepoManager

        store = _mock_store()
        store.query.return_value = MagicMock(result_set=[])
        mgr = MultiRepoManager(store)
        mgr.cross_repo_search("foo", limit=5)
        cypher = store.query.call_args[0][0]
        assert "LIMIT 5" in cypher


# ── CLI: repo ──────────────────────────────────────────────────────────────


class TestRepoCLI:
    def test_repo_add(self, tmp_path):
        runner = CliRunner()
        store = _mock_store()
        with patch("navegador.cli.commands._get_store", return_value=store):
            result = runner.invoke(main, ["repo", "add", "myapp", str(tmp_path)])
        assert result.exit_code == 0
        assert "myapp" in result.output

    def test_repo_list_empty(self):
        runner = CliRunner()
        store = _mock_store()
        store.query.return_value = MagicMock(result_set=[])
        with patch("navegador.cli.commands._get_store", return_value=store):
            result = runner.invoke(main, ["repo", "list"])
        assert result.exit_code == 0

    def test_repo_search(self):
        runner = CliRunner()
        store = _mock_store()
        store.query.return_value = MagicMock(result_set=[])
        with patch("navegador.cli.commands._get_store", return_value=store):
            result = runner.invoke(main, ["repo", "search", "foo"])
        assert result.exit_code == 0


# ═════════════════════════════════════════════════════════════════════════════
# #26 — SymbolRenamer
# ═════════════════════════════════════════════════════════════════════════════


class TestSymbolRenamerFindReferences:
    def test_returns_references(self):
        from navegador.refactor import SymbolRenamer

        store = _mock_store()
        store.query.return_value = MagicMock(result_set=[["Function", "foo", "a.py", 10]])
        renamer = SymbolRenamer(store)
        refs = renamer.find_references("foo")
        assert len(refs) == 1
        assert refs[0]["name"] == "foo"
        assert refs[0]["file_path"] == "a.py"

    def test_filters_by_file_path(self):
        from navegador.refactor import SymbolRenamer

        store = _mock_store()
        store.query.return_value = MagicMock(result_set=[])
        renamer = SymbolRenamer(store)
        renamer.find_references("foo", file_path="a.py")
        cypher = store.query.call_args[0][0]
        assert "file_path" in cypher

    def test_returns_empty_list_when_no_matches(self):
        from navegador.refactor import SymbolRenamer

        store = _mock_store()
        store.query.return_value = MagicMock(result_set=[])
        renamer = SymbolRenamer(store)
        assert renamer.find_references("nonexistent") == []


class TestSymbolRenamerPreview:
    def test_preview_does_not_update_graph(self):
        from navegador.refactor import SymbolRenamer

        store = _mock_store()
        store.query.return_value = MagicMock(result_set=[])
        renamer = SymbolRenamer(store)
        preview = renamer.preview_rename("old", "new")
        # No SET query should have been issued
        for c in store.query.call_args_list:
            assert "SET n.name" not in (c[0][0] if c[0] else "")

        assert preview.old_name == "old"
        assert preview.new_name == "new"

    def test_preview_collects_affected_files(self):
        from navegador.refactor import SymbolRenamer

        store = _mock_store()

        def _side(cypher, params=None):
            if "SET" not in cypher:
                return MagicMock(
                    result_set=[["Function", "old", "a.py", 1], ["Function", "old", "b.py", 5]]
                )
            return MagicMock(result_set=[])

        store.query.side_effect = _side
        renamer = SymbolRenamer(store)
        preview = renamer.preview_rename("old", "new")
        assert set(preview.affected_files) == {"a.py", "b.py"}


class TestSymbolRenamerApply:
    def test_apply_issues_set_query(self):
        from navegador.refactor import SymbolRenamer

        store = _mock_store()
        store.query.return_value = MagicMock(result_set=[])
        renamer = SymbolRenamer(store)
        renamer.apply_rename("old", "new")
        cypher_calls = [c[0][0] for c in store.query.call_args_list]
        assert any("SET n.name" in c for c in cypher_calls)

    def test_apply_returns_result_with_names(self):
        from navegador.refactor import SymbolRenamer

        store = _mock_store()
        store.query.return_value = MagicMock(result_set=[])
        renamer = SymbolRenamer(store)
        result = renamer.apply_rename("alpha", "beta")
        assert result.old_name == "alpha"
        assert result.new_name == "beta"


# ── CLI: rename ───────────────────────────────────────────────────────────────


class TestRenameCLI:
    def test_rename_preview(self):
        runner = CliRunner()
        store = _mock_store()
        store.query.return_value = MagicMock(result_set=[])
        with patch("navegador.cli.commands._get_store", return_value=store):
            result = runner.invoke(main, ["rename", "old_fn", "new_fn", "--preview"])
        assert result.exit_code == 0

    def test_rename_apply(self):
        runner = CliRunner()
        store = _mock_store()
        store.query.return_value = MagicMock(result_set=[])
        with patch("navegador.cli.commands._get_store", return_value=store):
            result = runner.invoke(main, ["rename", "old_fn", "new_fn"])
        assert result.exit_code == 0


# ═════════════════════════════════════════════════════════════════════════════
# #39 — CodeownersIngester
# ═════════════════════════════════════════════════════════════════════════════


class TestCodeownersIngesterParseFile:
    def test_parses_basic_entries(self, tmp_path):
        from navegador.codeowners import CodeownersIngester

        co = tmp_path / "CODEOWNERS"
        co.write_text("*.py @alice @bob\ndocs/ @carol\n")
        ingester = CodeownersIngester(_mock_store())
        entries = ingester._parse_codeowners(co)
        assert len(entries) == 2
        assert entries[0] == ("*.py", ["@alice", "@bob"])
        assert entries[1] == ("docs/", ["@carol"])

    def test_ignores_comments(self, tmp_path):
        from navegador.codeowners import CodeownersIngester

        co = tmp_path / "CODEOWNERS"
        co.write_text("# comment\n*.py @alice\n")
        ingester = CodeownersIngester(_mock_store())
        entries = ingester._parse_codeowners(co)
        assert len(entries) == 1

    def test_ignores_blank_lines(self, tmp_path):
        from navegador.codeowners import CodeownersIngester

        co = tmp_path / "CODEOWNERS"
        co.write_text("\n\n*.py @alice\n\n")
        ingester = CodeownersIngester(_mock_store())
        entries = ingester._parse_codeowners(co)
        assert len(entries) == 1

    def test_handles_email_owner(self, tmp_path):
        from navegador.codeowners import CodeownersIngester

        co = tmp_path / "CODEOWNERS"
        co.write_text("*.go user@example.com\n")
        ingester = CodeownersIngester(_mock_store())
        entries = ingester._parse_codeowners(co)
        assert entries[0][1] == ["user@example.com"]


class TestCodeownersIngesterIngest:
    def test_creates_person_nodes(self, tmp_path):
        from navegador.codeowners import CodeownersIngester

        co = tmp_path / "CODEOWNERS"
        co.write_text("*.py @alice\n")
        store = _mock_store()
        ingester = CodeownersIngester(store)
        stats = ingester.ingest(str(tmp_path))
        assert stats["owners"] == 1
        assert stats["patterns"] == 1
        assert stats["edges"] == 1

    def test_deduplicates_owners(self, tmp_path):
        from navegador.codeowners import CodeownersIngester

        co = tmp_path / "CODEOWNERS"
        co.write_text("*.py @alice\ndocs/ @alice\n")
        store = _mock_store()
        ingester = CodeownersIngester(store)
        stats = ingester.ingest(str(tmp_path))
        # alice appears in both patterns but should only be created once
        assert stats["owners"] == 1
        assert stats["patterns"] == 2

    def test_returns_zeros_when_no_codeowners(self, tmp_path):
        from navegador.codeowners import CodeownersIngester

        store = _mock_store()
        stats = CodeownersIngester(store).ingest(str(tmp_path))
        assert stats == {"owners": 0, "patterns": 0, "edges": 0}

    def test_finds_github_codeowners(self, tmp_path):
        from navegador.codeowners import CodeownersIngester

        gh = tmp_path / ".github"
        gh.mkdir()
        (gh / "CODEOWNERS").write_text("* @team\n")
        store = _mock_store()
        stats = CodeownersIngester(store).ingest(str(tmp_path))
        assert stats["owners"] == 1


# ── CLI: codeowners ───────────────────────────────────────────────────────────


class TestCodeownersCLI:
    def test_cli_codeowners(self, tmp_path):
        runner = CliRunner()
        (tmp_path / "CODEOWNERS").write_text("*.py @alice\n")
        store = _mock_store()
        with patch("navegador.cli.commands._get_store", return_value=store):
            result = runner.invoke(main, ["codeowners", str(tmp_path)])
        assert result.exit_code == 0
        assert "owner" in result.output


# ═════════════════════════════════════════════════════════════════════════════
# #40 — ADRIngester
# ═════════════════════════════════════════════════════════════════════════════


_SAMPLE_ADR = """\
# Use FalkorDB as the graph database

## Status

Accepted

## Context

We need a property graph DB.

## Decision

We will use FalkorDB.

## Rationale

Best performance for our use case. Supports Cypher.

## Date

2024-01-15
"""


class TestADRIngesterParse:
    def test_parses_title(self, tmp_path):
        from navegador.adr import ADRIngester

        f = tmp_path / "0001-use-falkordb.md"
        f.write_text(_SAMPLE_ADR)
        ingester = ADRIngester(_mock_store())
        parsed = ingester._parse_adr(f)
        assert parsed is not None
        assert "FalkorDB" in parsed["description"]

    def test_parses_status(self, tmp_path):
        from navegador.adr import ADRIngester

        f = tmp_path / "0001-test.md"
        f.write_text(_SAMPLE_ADR)
        ingester = ADRIngester(_mock_store())
        parsed = ingester._parse_adr(f)
        assert parsed["status"] == "accepted"

    def test_parses_rationale(self, tmp_path):
        from navegador.adr import ADRIngester

        f = tmp_path / "0001-test.md"
        f.write_text(_SAMPLE_ADR)
        ingester = ADRIngester(_mock_store())
        parsed = ingester._parse_adr(f)
        assert "performance" in parsed["rationale"].lower()

    def test_parses_date(self, tmp_path):
        from navegador.adr import ADRIngester

        f = tmp_path / "0001-test.md"
        f.write_text(_SAMPLE_ADR)
        ingester = ADRIngester(_mock_store())
        parsed = ingester._parse_adr(f)
        assert parsed["date"] == "2024-01-15"

    def test_uses_stem_as_name(self, tmp_path):
        from navegador.adr import ADRIngester

        f = tmp_path / "0042-my-decision.md"
        f.write_text(_SAMPLE_ADR)
        ingester = ADRIngester(_mock_store())
        parsed = ingester._parse_adr(f)
        assert parsed["name"] == "0042-my-decision"

    def test_returns_none_for_non_adr(self, tmp_path):
        from navegador.adr import ADRIngester

        f = tmp_path / "readme.md"
        f.write_text("No heading here.")
        ingester = ADRIngester(_mock_store())
        assert ingester._parse_adr(f) is None


class TestADRIngesterIngest:
    def test_creates_decision_nodes(self, tmp_path):
        from navegador.adr import ADRIngester

        (tmp_path / "0001-first.md").write_text(_SAMPLE_ADR)
        (tmp_path / "0002-second.md").write_text(_SAMPLE_ADR)
        store = _mock_store()
        stats = ADRIngester(store).ingest(str(tmp_path))
        assert stats["decisions"] == 2
        assert stats["skipped"] == 0

    def test_skips_files_without_h1(self, tmp_path):
        from navegador.adr import ADRIngester

        (tmp_path / "empty.md").write_text("no heading\n")
        store = _mock_store()
        stats = ADRIngester(store).ingest(str(tmp_path))
        assert stats["skipped"] == 1

    def test_returns_zeros_for_empty_dir(self, tmp_path):
        from navegador.adr import ADRIngester

        store = _mock_store()
        stats = ADRIngester(store).ingest(str(tmp_path))
        assert stats == {"decisions": 0, "skipped": 0}

    def test_nonexistent_dir_returns_zeros(self, tmp_path):
        from navegador.adr import ADRIngester

        store = _mock_store()
        stats = ADRIngester(store).ingest(str(tmp_path / "no_such_dir"))
        assert stats == {"decisions": 0, "skipped": 0}


# ── CLI: adr ─────────────────────────────────────────────────────────────────


class TestADRCLI:
    def test_adr_ingest(self, tmp_path):
        runner = CliRunner()
        (tmp_path / "0001-test.md").write_text(_SAMPLE_ADR)
        store = _mock_store()
        with patch("navegador.cli.commands._get_store", return_value=store):
            result = runner.invoke(main, ["adr", "ingest", str(tmp_path)])
        assert result.exit_code == 0
        assert "decision" in result.output.lower()


# ═════════════════════════════════════════════════════════════════════════════
# #41 — APISchemaIngester
# ═════════════════════════════════════════════════════════════════════════════


_OPENAPI_YAML = """\
openapi: "3.0.0"
info:
  title: Test API
  version: "1.0"
paths:
  /users:
    get:
      operationId: listUsers
      summary: List all users
      tags:
        - users
    post:
      operationId: createUser
      summary: Create a user
components:
  schemas:
    User:
      description: A user object
      type: object
"""

_OPENAPI_JSON = {
    "openapi": "3.0.0",
    "info": {"title": "Test API", "version": "1.0"},
    "paths": {
        "/items": {
            "get": {"operationId": "listItems", "summary": "List items"},
            "post": {"summary": "Create item"},
        }
    },
    "components": {"schemas": {"Item": {"description": "An item", "type": "object"}}},
}

_GRAPHQL_SCHEMA = """\
type Query {
  users: [User]
  user(id: ID!): User
}

type Mutation {
  createUser(name: String!): User
}

type User {
  id: ID!
  name: String!
  email: String
}

input CreateUserInput {
  name: String!
  email: String
}
"""


class TestAPISchemaIngesterOpenAPI:
    def test_ingest_openapi_json(self, tmp_path):
        from navegador.api_schema import APISchemaIngester

        p = tmp_path / "api.json"
        p.write_text(json.dumps(_OPENAPI_JSON))
        store = _mock_store()
        stats = APISchemaIngester(store).ingest_openapi(str(p))
        assert stats["endpoints"] >= 2
        assert stats["schemas"] >= 1

    def test_ingest_creates_function_nodes(self, tmp_path):
        from navegador.api_schema import APISchemaIngester

        p = tmp_path / "api.json"
        p.write_text(json.dumps(_OPENAPI_JSON))
        store = _mock_store()
        APISchemaIngester(store).ingest_openapi(str(p))
        labels = [c[0][0] for c in store.create_node.call_args_list]
        assert "Function" in labels

    def test_ingest_creates_class_nodes_for_schemas(self, tmp_path):
        from navegador.api_schema import APISchemaIngester

        p = tmp_path / "api.json"
        p.write_text(json.dumps(_OPENAPI_JSON))
        store = _mock_store()
        APISchemaIngester(store).ingest_openapi(str(p))
        labels = [c[0][0] for c in store.create_node.call_args_list]
        assert "Class" in labels

    def test_missing_file_returns_zeros(self, tmp_path):
        from navegador.api_schema import APISchemaIngester

        store = _mock_store()
        stats = APISchemaIngester(store).ingest_openapi(str(tmp_path / "no.yaml"))
        assert stats == {"endpoints": 0, "schemas": 0}

    def test_empty_paths_returns_zeros(self, tmp_path):
        from navegador.api_schema import APISchemaIngester

        p = tmp_path / "empty.json"
        p.write_text(json.dumps({"openapi": "3.0.0", "info": {}}))
        store = _mock_store()
        stats = APISchemaIngester(store).ingest_openapi(str(p))
        assert stats == {"endpoints": 0, "schemas": 0}


class TestAPISchemaIngesterGraphQL:
    def test_ingest_graphql_types(self, tmp_path):
        from navegador.api_schema import APISchemaIngester

        p = tmp_path / "schema.graphql"
        p.write_text(_GRAPHQL_SCHEMA)
        store = _mock_store()
        stats = APISchemaIngester(store).ingest_graphql(str(p))
        # User + CreateUserInput → type nodes
        assert stats["types"] >= 1

    def test_ingest_graphql_query_fields(self, tmp_path):
        from navegador.api_schema import APISchemaIngester

        p = tmp_path / "schema.graphql"
        p.write_text(_GRAPHQL_SCHEMA)
        store = _mock_store()
        stats = APISchemaIngester(store).ingest_graphql(str(p))
        # Query.users, Query.user, Mutation.createUser
        assert stats["fields"] >= 2

    def test_missing_file_returns_zeros(self, tmp_path):
        from navegador.api_schema import APISchemaIngester

        store = _mock_store()
        stats = APISchemaIngester(store).ingest_graphql(str(tmp_path / "no.graphql"))
        assert stats == {"types": 0, "fields": 0}


# ── CLI: api ──────────────────────────────────────────────────────────────────


class TestAPICLI:
    def test_api_ingest_openapi_json(self, tmp_path):
        runner = CliRunner()
        p = tmp_path / "api.json"
        p.write_text(json.dumps(_OPENAPI_JSON))
        store = _mock_store()
        with patch("navegador.cli.commands._get_store", return_value=store):
            result = runner.invoke(main, ["api", "ingest", str(p), "--type", "openapi"])
        assert result.exit_code == 0

    def test_api_ingest_graphql(self, tmp_path):
        runner = CliRunner()
        p = tmp_path / "schema.graphql"
        p.write_text(_GRAPHQL_SCHEMA)
        store = _mock_store()
        with patch("navegador.cli.commands._get_store", return_value=store):
            result = runner.invoke(main, ["api", "ingest", str(p), "--type", "graphql"])
        assert result.exit_code == 0

    def test_api_ingest_auto_detects_graphql(self, tmp_path):
        runner = CliRunner()
        p = tmp_path / "schema.graphql"
        p.write_text(_GRAPHQL_SCHEMA)
        store = _mock_store()
        with patch("navegador.cli.commands._get_store", return_value=store):
            result = runner.invoke(main, ["api", "ingest", str(p)])
        assert result.exit_code == 0

    def test_api_ingest_json_output(self, tmp_path):
        runner = CliRunner()
        p = tmp_path / "api.json"
        p.write_text(json.dumps(_OPENAPI_JSON))
        store = _mock_store()
        with patch("navegador.cli.commands._get_store", return_value=store):
            result = runner.invoke(main, ["api", "ingest", str(p), "--type", "openapi", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "endpoints" in data
