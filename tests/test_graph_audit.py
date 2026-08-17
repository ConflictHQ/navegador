"""
Graph audit classification (#181).

The audit decides what gets deleted from a shared server, so the risk is not
that it misses something — it is that it condemns a healthy graph. Every test
here is about the boundary between "provably wrong" and "merely unfamiliar".

The one bug found while building this is worth remembering: ``GRAPH.LIST``
returns bytes on an undecoded connection, and ``str()`` on bytes yields
``b'name'``, which matches no graph. Every lookup missed, every graph looked
empty, and the audit cheerfully reported 41 of 41 graphs as reclaimable —
including populated ones. It reported success while disagreeing with reality,
which is the exact failure class this project keeps running into.
"""

import pytest

from navegador.graph.audit import GraphAudit, mark_duplicates


def audit(name="g", nodes=100, files_total=0, files_resolved=0, root=None, **kw):
    return GraphAudit(
        name=name,
        nodes=nodes,
        files_total=files_total,
        files_resolved=files_resolved,
        root=root,
        **kw,
    )


class TestJunkNames:
    @pytest.mark.parametrize("name", ["1)", "9)", "telemetry{9)}", "", "  "])
    def test_shell_artifacts_are_junk(self, name):
        assert audit(name=name).verdict == "junk"

    @pytest.mark.parametrize(
        "name",
        [
            "navegador",
            "navegador_my-repo",
            "navegador_calliope-astrolift",
            "nav_dupe_probe",
            "repo.git",
        ],
    )
    def test_ordinary_names_are_not_junk(self, name):
        assert audit(name=name).verdict != "junk"

    def test_junk_beats_every_other_verdict(self, tmp_path):
        """A key that is not really a graph should not be probed further."""
        assert audit(name="1)", nodes=0).verdict == "junk"


class TestStaleness:
    def test_no_paths_resolving_is_stale(self, tmp_path):
        report = audit(files_total=2000, files_resolved=0, root=tmp_path)
        assert report.verdict == "stale"
        assert "0/2000" in report.explain()

    def test_a_little_drift_is_not_stale(self, tmp_path):
        """
        Files get deleted between ingests. Calling that stale would condemn
        healthy graphs, which is the expensive direction to be wrong in.
        """
        report = audit(files_total=1000, files_resolved=940, root=tmp_path)
        assert report.verdict == "healthy"

    def test_boundary(self, tmp_path):
        assert audit(files_total=1000, files_resolved=50, root=tmp_path).verdict == "stale"
        assert audit(files_total=1000, files_resolved=51, root=tmp_path).verdict == "healthy"

    def test_no_checkout_is_unknown_not_stale(self):
        """
        A graph we cannot check is not the same as a graph we know is wrong.
        Assuming stale here would delete data on the strength of the auditor
        not having looked in the right place.
        """
        report = audit(files_total=0, files_resolved=0, root=None)
        assert report.verdict == "unknown"
        assert not report.reclaimable

    def test_resolution_is_none_without_a_root(self):
        assert audit(files_total=10, files_resolved=5, root=None).resolution is None


class TestEmpty:
    def test_zero_nodes_is_empty(self):
        assert audit(nodes=0).verdict == "empty"

    def test_empty_is_reclaimable(self):
        assert audit(nodes=0).reclaimable


class TestDuplicates:
    def test_same_repository_set_is_flagged(self):
        big = audit(name="big", nodes=500, repositories=["repo-a"])
        small = audit(name="small", nodes=100, repositories=["repo-a"])
        mark_duplicates([big, small])

        assert small.duplicate_of == "big"
        assert big.duplicate_of is None
        assert small.verdict == "duplicate"

    def test_larger_graph_keeps_its_identity(self):
        small = audit(name="small", nodes=10, repositories=["r"])
        big = audit(name="big", nodes=999, repositories=["r"])
        mark_duplicates([small, big])
        assert small.duplicate_of == "big"

    def test_tie_is_broken_deterministically(self):
        """Same server, same answer — not whatever order the dict yielded."""
        first = audit(name="aaa", nodes=100, repositories=["r"])
        second = audit(name="bbb", nodes=100, repositories=["r"])
        mark_duplicates([second, first])
        assert first.duplicate_of is None
        assert second.duplicate_of == "aaa"

    def test_different_repositories_are_not_duplicates(self):
        a = audit(name="a", nodes=100, repositories=["repo-a"])
        b = audit(name="b", nodes=100, repositories=["repo-b"])
        mark_duplicates([a, b])
        assert a.duplicate_of is None and b.duplicate_of is None

    def test_duplicates_are_not_reclaimable(self):
        """
        Two graphs of one repository may differ by ingest settings or age.
        Which one wins is the operator's call, so this reports and stops.
        """
        big = audit(name="big", nodes=500, repositories=["r"])
        small = audit(name="small", nodes=100, repositories=["r"])
        mark_duplicates([big, small])
        assert not small.reclaimable

    def test_empty_graphs_do_not_claim_a_repository(self):
        empty = audit(name="empty", nodes=0, repositories=["r"])
        real = audit(name="real", nodes=100, repositories=["r"])
        mark_duplicates([empty, real])
        assert real.duplicate_of is None


class TestReclaimable:
    @pytest.mark.parametrize(
        "report,expected",
        [
            (GraphAudit(name="1)"), True),
            (GraphAudit(name="ok", nodes=0), True),
            (GraphAudit(name="ok", nodes=5), False),
        ],
    )
    def test_reclaimable(self, report, expected):
        assert report.reclaimable is expected

    def test_healthy_is_never_reclaimable(self, tmp_path):
        assert not audit(files_total=100, files_resolved=100, root=tmp_path).reclaimable


class TestSerialisation:
    def test_to_dict_carries_the_reasoning(self, tmp_path):
        payload = audit(files_total=10, files_resolved=0, root=tmp_path).to_dict()
        assert payload["verdict"] == "stale"
        assert payload["reclaimable"] is True
        assert payload["resolution"] == 0.0
        assert "explanation" in payload


# ── Against a real store ──────────────────────────────────────────────────────
#
# The classification tests above are pure. These exercise the code that talks
# to a server, which is where the one real bug lived: GRAPH.LIST returns bytes
# and str() on bytes yields "b'name'", so every lookup missed and every graph
# looked empty. An embedded store is a real Redis over a unix socket, so it
# exercises the same paths without needing a server on the machine.


@pytest.fixture
def live(tmp_path):
    """An embedded store with a populated graph and a checkout on disk."""
    from navegador.graph import GraphStore
    from navegador.ingestion import RepoIngester

    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    (root / "src" / "app.py").write_text("def alpha():\n    return 1\n")
    (root / "src" / "util.py").write_text("def beta():\n    return 2\n")

    store = GraphStore.sqlite(str(tmp_path / "g.db"))
    RepoIngester(store).ingest(root)
    return store, root


class TestAuditGraphAgainstAStore:
    def test_healthy_graph_with_resolving_paths(self, live):
        from navegador.graph.audit import audit_graph

        store, root = live
        report = audit_graph(store._client, store._client.connection, store.graph_name, root=root)
        assert report.nodes > 0
        assert report.files_total > 0
        assert report.files_resolved == report.files_total
        assert report.verdict == "healthy"

    def test_graph_whose_checkout_vanished_is_stale(self, live, tmp_path):
        """The 45 MB case found on the live server, in miniature."""
        from navegador.graph.audit import audit_graph

        store, _ = live
        report = audit_graph(
            store._client,
            store._client.connection,
            store.graph_name,
            root=tmp_path / "moved-away",
        )
        assert report.verdict == "stale"
        assert report.reclaimable

    def test_without_a_root_the_verdict_is_unknown(self, live):
        from navegador.graph.audit import audit_graph

        store, _ = live
        report = audit_graph(store._client, store._client.connection, store.graph_name)
        assert report.verdict == "unknown"
        assert not report.reclaimable

    def test_repositories_are_collected(self, live):
        from navegador.graph.audit import audit_graph

        store, root = live
        report = audit_graph(store._client, store._client.connection, store.graph_name, root=root)
        assert report.repositories

    def test_size_is_measured(self, live):
        """
        DUMP, because MEMORY USAGE returns a meaningless 48 bytes for a graph
        key — Redis cannot size a module type.
        """
        from navegador.graph.audit import audit_graph

        store, root = live
        report = audit_graph(store._client, store._client.connection, store.graph_name, root=root)
        assert report.size_bytes > 48

    def test_sampling_bounds_the_path_check(self, live):
        from navegador.graph.audit import audit_graph

        store, root = live
        report = audit_graph(
            store._client, store._client.connection, store.graph_name, root=root, sample=1
        )
        assert report.files_total == 1

    def test_junk_named_key_is_not_queried(self, live):
        """A junk key may not be a graph at all; probing it would raise."""
        from navegador.graph.audit import audit_graph

        store, _ = live
        report = audit_graph(store._client, store._client.connection, "1)")
        assert report.verdict == "junk"


class TestGraphSizeBytes:
    def test_missing_key_is_zero(self, live):
        from navegador.graph.audit import graph_size_bytes

        store, _ = live
        assert graph_size_bytes(store._client.connection, "no_such_graph_here") == 0


class TestPruneAgainstAStore:
    def test_empty_graph_is_deleted(self, live):
        from navegador.graph.audit import GraphAudit, prune

        store, _ = live
        connection = store._client.connection
        connection.set("nav_empty_probe", "x")
        report = GraphAudit(name="nav_empty_probe", nodes=0)

        assert prune(connection, [report]) == ["nav_empty_probe"]
        assert not connection.exists("nav_empty_probe")

    def test_healthy_graph_is_left_alone(self, live, tmp_path):
        from navegador.graph.audit import GraphAudit, prune

        store, _ = live
        report = GraphAudit(
            name=store.graph_name, nodes=10, files_total=5, files_resolved=5, root=tmp_path
        )
        assert prune(store._client.connection, [report]) == []
        assert store.node_count() > 0

    def test_stale_is_held_back_by_default(self, live, tmp_path):
        """
        A moved checkout and a deleted one look identical from here, and one
        of them is recoverable by re-ingesting.
        """
        from navegador.graph.audit import GraphAudit, prune

        store, _ = live
        report = GraphAudit(
            name=store.graph_name, nodes=10, files_total=5, files_resolved=0, root=tmp_path
        )
        assert prune(store._client.connection, [report]) == []
        assert store.node_count() > 0

    def test_stale_goes_when_asked_explicitly(self, live, tmp_path):
        from navegador.graph.audit import GraphAudit, prune

        store, _ = live
        report = GraphAudit(
            name=store.graph_name, nodes=10, files_total=5, files_resolved=0, root=tmp_path
        )
        assert prune(store._client.connection, [report], include_stale=True) == [store.graph_name]

    def test_url_form_still_accepted(self):
        """The CLI passes a URL; only tests pass a connection."""
        from navegador.graph.audit import prune

        assert prune("redis://127.0.0.1:1", []) == []
