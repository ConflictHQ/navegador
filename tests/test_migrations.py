"""Tests for navegador.graph.migrations — schema versioning and migration."""

from unittest.mock import MagicMock, call

import pytest

from navegador.graph.migrations import (
    CURRENT_SCHEMA_VERSION,
    _migrations,
    get_schema_version,
    migrate,
    needs_migration,
    set_schema_version,
)


def _mock_store(version=None):
    store = MagicMock()
    if version is None:
        store.query.return_value = MagicMock(result_set=[])
    else:
        store.query.return_value = MagicMock(result_set=[[version]])
    return store


# ── get_schema_version ───────────────────────────────────────────────────────

class TestGetSchemaVersion:
    def test_returns_zero_for_empty_graph(self):
        store = _mock_store(version=None)
        assert get_schema_version(store) == 0

    def test_returns_zero_for_null_version(self):
        store = MagicMock()
        store.query.return_value = MagicMock(result_set=[[None]])
        assert get_schema_version(store) == 0

    def test_returns_stored_version(self):
        store = _mock_store(version=2)
        assert get_schema_version(store) == 2


# ── set_schema_version ──────────────────────────────────────────────────────

class TestSetSchemaVersion:
    def test_calls_query_with_merge(self):
        store = MagicMock()
        set_schema_version(store, 3)
        store.query.assert_called_once()
        cypher = store.query.call_args[0][0]
        assert "MERGE" in cypher
        assert store.query.call_args[0][1]["version"] == 3


# ── needs_migration ──────────────────────────────────────────────────────────

class TestNeedsMigration:
    def test_true_when_behind(self):
        store = _mock_store(version=0)
        assert needs_migration(store) is True

    def test_false_when_current(self):
        store = _mock_store(version=CURRENT_SCHEMA_VERSION)
        assert needs_migration(store) is False


# ── migrate ──────────────────────────────────────────────────────────────────

class TestMigrate:
    def test_applies_all_migrations_from_zero(self):
        call_log = []

        def track_query(cypher, params=None):
            call_log.append(cypher)
            result = MagicMock()
            # get_schema_version query returns no rows initially
            if "Meta" in cypher and "RETURN" in cypher:
                result.result_set = []
            else:
                result.result_set = []
            return result

        store = MagicMock()
        store.query.side_effect = track_query

        applied = migrate(store)
        assert applied == list(range(1, CURRENT_SCHEMA_VERSION + 1))

    def test_no_op_when_already_current(self):
        store = _mock_store(version=CURRENT_SCHEMA_VERSION)
        applied = migrate(store)
        assert applied == []

    def test_raises_on_missing_migration(self):
        # Temporarily remove a migration to trigger the RuntimeError
        saved = _migrations.pop(0)
        try:
            store = _mock_store(version=None)
            with pytest.raises(RuntimeError, match="No migration registered"):
                migrate(store)
        finally:
            _migrations[0] = saved


# ── migrations registry ─────────────────────────────────────────────────────

class TestMigrationsRegistry:
    def test_has_migration_for_each_version(self):
        for v in range(CURRENT_SCHEMA_VERSION):
            assert v in _migrations, f"Missing migration for version {v} -> {v + 1}"

    def test_current_version_is_positive(self):
        assert CURRENT_SCHEMA_VERSION > 0

    def test_migration_0_to_1_runs(self):
        store = MagicMock()
        store.query.return_value = MagicMock(result_set=[])
        _migrations[0](store)

    def test_migration_1_to_2_sets_content_hash(self):
        store = MagicMock()
        store.query.return_value = MagicMock(result_set=[])
        _migrations[1](store)
        store.query.assert_called_once()
        cypher = store.query.call_args[0][0]
        assert "content_hash" in cypher
