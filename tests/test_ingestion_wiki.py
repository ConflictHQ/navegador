"""Tests for navegador.ingestion.wiki — WikiIngester."""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from navegador.graph.schema import NodeLabel
from navegador.ingestion.wiki import WikiIngester, _extract_terms

# ── Unit: _extract_terms ──────────────────────────────────────────────────────

class TestExtractTerms:
    def test_extracts_headings(self):
        md = "# Introduction\n## Getting Started\n### Deep Dive\n"
        terms = _extract_terms(md)
        assert "Introduction" in terms
        assert "Getting Started" in terms
        assert "Deep Dive" in terms

    def test_extracts_bold_asterisk(self):
        md = "Use **GraphStore** for all persistence."
        terms = _extract_terms(md)
        assert "GraphStore" in terms

    def test_extracts_bold_underscore(self):
        md = "The __FalkorDB__ module is required."
        terms = _extract_terms(md)
        assert "FalkorDB" in terms

    def test_deduplicates(self):
        md = "# GraphStore\nUse **GraphStore** here too."
        terms = _extract_terms(md)
        assert terms.count("GraphStore") == 1

    def test_empty_markdown(self):
        assert _extract_terms("") == []

    def test_no_headings_no_bold(self):
        terms = _extract_terms("plain text with no markup")
        assert terms == []

    def test_preserves_order(self):
        md = "# Alpha\n# Beta\n**Gamma**"
        terms = _extract_terms(md)
        assert terms == ["Alpha", "Beta", "Gamma"]


# ── Unit: ingest_local ────────────────────────────────────────────────────────

class TestIngestLocal:
    def _make_store(self):
        store = MagicMock()
        store.query.return_value = MagicMock(result_set=[])
        return store

    def test_ingests_markdown_files(self):
        store = self._make_store()
        ingester = WikiIngester(store)
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "home.md").write_text("# Welcome\nThis is home.")
            (Path(tmpdir) / "guide.md").write_text("## Usage\nSome guide.")
            stats = ingester.ingest_local(tmpdir)
            assert stats["pages"] == 2

    def test_skips_non_markdown(self):
        store = self._make_store()
        ingester = WikiIngester(store)
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "readme.md").write_text("# Readme")
            (Path(tmpdir) / "image.png").write_bytes(b"\x89PNG")
            stats = ingester.ingest_local(tmpdir)
            assert stats["pages"] == 1

    def test_raises_if_dir_missing(self):
        store = self._make_store()
        ingester = WikiIngester(store)
        with pytest.raises(FileNotFoundError):
            ingester.ingest_local("/nonexistent/path")

    def test_creates_wiki_page_node(self):
        store = self._make_store()
        ingester = WikiIngester(store)
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "arch.md").write_text("# Architecture")
            ingester.ingest_local(tmpdir)
            store.create_node.assert_called_once()
            call_args = store.create_node.call_args
            assert call_args[0][0] == NodeLabel.WikiPage
            props = call_args[0][1]
            assert props["name"] == "arch"
            assert props["source"] == "local"

    def test_page_name_normalisation(self):
        store = self._make_store()
        ingester = WikiIngester(store)
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "getting-started.md").write_text("# Hi")
            ingester.ingest_local(tmpdir)
            props = store.create_node.call_args[0][1]
            assert props["name"] == "getting started"

    def test_creates_documents_edge_when_term_matches(self):
        store = MagicMock()
        store.query.return_value = MagicMock(result_set=[["Concept", "GraphStore"]])
        ingester = WikiIngester(store)
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "page.md").write_text("# GraphStore\nSome text.")
            stats = ingester.ingest_local(tmpdir)
            assert stats["links"] >= 1
            store.create_edge.assert_called()

    def test_no_links_when_no_term_match(self):
        store = self._make_store()  # query returns []
        ingester = WikiIngester(store)
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "page.md").write_text("# UnknownTerm\nText.")
            stats = ingester.ingest_local(tmpdir)
            assert stats["links"] == 0
            store.create_edge.assert_not_called()

    def test_content_capped_at_4000_chars(self):
        store = self._make_store()
        ingester = WikiIngester(store)
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "long.md").write_text("x" * 10000)
            ingester.ingest_local(tmpdir)
            props = store.create_node.call_args[0][1]
            assert len(props["content"]) <= 4000

    def test_returns_stats_dict(self):
        store = self._make_store()
        ingester = WikiIngester(store)
        with tempfile.TemporaryDirectory() as tmpdir:
            stats = ingester.ingest_local(tmpdir)
            assert "pages" in stats
            assert "links" in stats
            assert stats["pages"] == 0

    def test_recursive_glob(self):
        store = self._make_store()
        ingester = WikiIngester(store)
        with tempfile.TemporaryDirectory() as tmpdir:
            subdir = Path(tmpdir) / "sub"
            subdir.mkdir()
            (subdir / "nested.md").write_text("# Nested")
            stats = ingester.ingest_local(tmpdir)
            assert stats["pages"] == 1


# ── Unit: _try_link edge-type handling ────────────────────────────────────────

class TestTryLink:
    def test_handles_invalid_label_gracefully(self):
        store = MagicMock()
        store.query.return_value = MagicMock(result_set=[["InvalidLabel", "foo"]])
        ingester = WikiIngester(store)
        result = ingester._try_link("page", "foo")
        assert result == 0

    def test_creates_edge_for_valid_label(self):
        store = MagicMock()
        store.query.return_value = MagicMock(result_set=[["Concept", "MyService"]])
        ingester = WikiIngester(store)
        result = ingester._try_link("wiki page", "MyService")
        assert result == 1
        store.create_edge.assert_called_once()

    def test_returns_zero_on_unknown_label(self):
        store = MagicMock()
        store.query.return_value = MagicMock(result_set=[["UnknownLabel", "node"]])
        ingester = WikiIngester(store)
        result = ingester._try_link("page", "node")
        assert result == 0

    def test_propagates_store_error(self):
        store = MagicMock()
        store.query.return_value = MagicMock(result_set=[["Concept", "node"]])
        store.create_edge.side_effect = Exception("DB error")
        ingester = WikiIngester(store)
        with pytest.raises(Exception, match="DB error"):
            ingester._try_link("page", "node")


# ── GitHub clone (ingest_github) ──────────────────────────────────────────────

class TestIngestGithub:
    def _make_store(self):
        store = MagicMock()
        store.query.return_value = MagicMock(result_set=[])
        return store

    def test_clones_wiki_and_ingests_local(self):
        store = self._make_store()
        ingester = WikiIngester(store)

        with tempfile.TemporaryDirectory() as tmpdir:
            wiki_dir = Path(tmpdir)
            (wiki_dir / "home.md").write_text("# Home\nWelcome.")

            mock_result = MagicMock()
            mock_result.returncode = 0

            with patch("subprocess.run", return_value=mock_result) as mock_run, \
                 patch("tempfile.mkdtemp", return_value=str(tmpdir)):
                stats = ingester.ingest_github("owner/repo")
                mock_run.assert_called_once()
                cmd = mock_run.call_args[0][0]
                assert "git" in cmd
                assert "clone" in cmd
                assert "https://github.com/owner/repo.wiki.git" in cmd
                assert stats["pages"] == 1

    def test_returns_empty_on_clone_failure(self):
        store = self._make_store()
        ingester = WikiIngester(store)

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "fatal: repository not found"

        with patch("subprocess.run", return_value=mock_result):
            stats = ingester.ingest_github("owner/empty-repo")
            assert stats == {"pages": 0, "links": 0}

    def test_uses_token_in_url(self):
        store = self._make_store()
        ingester = WikiIngester(store)

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "auth error"

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            ingester.ingest_github("owner/repo", token="mytoken")
            cmd = mock_run.call_args[0][0]
            assert "mytoken@github.com" in cmd[3]

    def test_uses_explicit_clone_dir(self):
        store = self._make_store()
        ingester = WikiIngester(store)

        with tempfile.TemporaryDirectory() as tmpdir:
            mock_result = MagicMock()
            mock_result.returncode = 0

            with patch("subprocess.run", return_value=mock_result):
                ingester.ingest_github("owner/repo", clone_dir=tmpdir)
                # Should not crash


# ── GitHub API (ingest_github_api) ────────────────────────────────────────────

class TestIngestGithubApi:
    def _make_store(self):
        store = MagicMock()
        store.query.return_value = MagicMock(result_set=[])
        return store

    def test_fetches_readme_and_ingests(self):
        store = self._make_store()
        ingester = WikiIngester(store)

        import base64
        import json as _json
        readme_content = base64.b64encode(b"# README\nSome content").decode()
        mock_response_data = {
            "content": readme_content,
            "html_url": "https://github.com/owner/repo/blob/main/README.md",
        }

        mock_resp = MagicMock()
        mock_resp.read.return_value = _json.dumps(mock_response_data).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            stats = ingester.ingest_github_api("owner/repo")
            assert stats["pages"] >= 1

    def test_skips_missing_files_gracefully(self):
        store = self._make_store()
        ingester = WikiIngester(store)

        with patch("urllib.request.urlopen", side_effect=Exception("404")):
            stats = ingester.ingest_github_api("owner/repo")
            assert stats == {"pages": 0, "links": 0}

    def test_uses_auth_header_with_token(self):
        store = self._make_store()
        ingester = WikiIngester(store)

        with patch("urllib.request.urlopen", side_effect=Exception("skip")), \
             patch("urllib.request.Request") as mock_req:
            ingester.ingest_github_api("owner/repo", token="mytoken")
            # Just verify no crash and token path was exercised
            assert mock_req.called
