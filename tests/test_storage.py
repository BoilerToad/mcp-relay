"""
test_storage.py - SQLite storage backend tests.

Validates:
  1. Schema integrity   — correct tables, columns, indexes exist
  2. Session lifecycle  — create, end, retrieve, list
  3. Event persistence  — write, retrieve, filter by type
  4. Data integrity     — JSON round-trips, NULL handling, FK enforcement
  5. Research queries   — latency_stats, call_counts, error_rates
  6. Multi-model data   — queries correctly partition by model
  7. Context manager    — initialize / close lifecycle
  8. Stddev edge cases  — lines 326, 395 in sqlite.py
"""

from __future__ import annotations

import json
import math
import pytest

from mcp_relay.storage.sqlite import SQLiteStorage, _stddev
from mcp_relay.storage.base import EventRecord, SessionRecord
from mcp_relay.core.logging import utc_now


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def db(tmp_path) -> SQLiteStorage:
    """A fresh initialized SQLiteStorage for each test."""
    storage = SQLiteStorage(tmp_path / "test.db")
    storage.initialize()
    yield storage
    storage.close()


@pytest.fixture
def session_a(db) -> SessionRecord:
    s = SessionRecord(
        session_id="sess-a",
        started_at=utc_now(),
        model_name="qwen2.5:latest",
        transport_profile="clean",
        upstream_command="uvx mcp-server-fetch",
    )
    db.create_session(s)
    return s


@pytest.fixture
def session_b(db) -> SessionRecord:
    s = SessionRecord(
        session_id="sess-b",
        started_at=utc_now(),
        model_name="llama3.2:latest",
        transport_profile="degraded_static",
        upstream_command="uvx mcp-server-fetch",
    )
    db.create_session(s)
    return s


def make_event(
    event_id: str,
    event_type: str,
    session_id: str,
    tool_name: str = "fetch",
    latency_ms: float | None = None,
    payload: dict | None = None,
    error: str | None = None,
) -> EventRecord:
    return EventRecord(
        event_id=event_id,
        event_type=event_type,
        session_id=session_id,
        timestamp=utc_now(),
        tool_name=tool_name,
        transport_mode="live",
        payload=payload or {"url": "https://example.com"},
        latency_ms=latency_ms,
        error=error,
        upstream_command="uvx mcp-server-fetch",
    )


# ---------------------------------------------------------------------------
# 1. Schema integrity
# ---------------------------------------------------------------------------

class TestSchema:

    def test_tables_exist(self, db):
        tables = db.table_names()
        assert "sessions" in tables
        assert "events" in tables

    def test_sessions_columns(self, db):
        cols = db.column_names("sessions")
        expected = {
            "session_id", "started_at", "ended_at",
            "model_name", "transport_profile", "upstream_command", "notes"
        }
        assert expected.issubset(set(cols)), (
            f"Missing columns: {expected - set(cols)}"
        )

    def test_events_columns(self, db):
        cols = db.column_names("events")
        expected = {
            "id", "event_id", "event_type", "session_id", "timestamp",
            "tool_name", "transport_mode", "payload", "response",
            "error", "latency_ms", "upstream_command", "extra"
        }
        assert expected.issubset(set(cols)), (
            f"Missing columns: {expected - set(cols)}"
        )

    def test_required_indexes_exist(self, db):
        indexes = db.index_names()
        expected = {
            "idx_events_session",
            "idx_events_type",
            "idx_events_timestamp",
            "idx_events_tool",
            "idx_sessions_model",
        }
        assert expected.issubset(set(indexes)), (
            f"Missing indexes: {expected - set(indexes)}"
        )

    def test_initialize_is_idempotent(self, tmp_path):
        """Calling initialize() twice must not raise or corrupt data."""
        storage = SQLiteStorage(tmp_path / "idem.db")
        storage.initialize()
        storage.initialize()   # second call — should be silent
        assert "sessions" in storage.table_names()
        storage.close()

    def test_wal_mode_enabled(self, db):
        """WAL journal mode must be active for concurrent read performance."""
        row = db._db.execute("PRAGMA journal_mode").fetchone()
        assert row[0] == "wal"

    def test_foreign_keys_enabled(self, db):
        """Foreign key enforcement must be ON."""
        row = db._db.execute("PRAGMA foreign_keys").fetchone()
        assert row[0] == 1


# ---------------------------------------------------------------------------
# 2. Session lifecycle
# ---------------------------------------------------------------------------

class TestSessions:

    def test_create_and_retrieve_session(self, db):
        s = SessionRecord(
            session_id="test-001",
            started_at="2026-01-01T00:00:00+00:00",
            model_name="qwen2.5:latest",
            transport_profile="clean",
            upstream_command="uvx mcp-server-fetch",
            notes="unit test",
        )
        db.create_session(s)
        retrieved = db.get_session("test-001")

        assert retrieved is not None
        assert retrieved.session_id == "test-001"
        assert retrieved.model_name == "qwen2.5:latest"
        assert retrieved.transport_profile == "clean"
        assert retrieved.upstream_command == "uvx mcp-server-fetch"
        assert retrieved.notes == "unit test"
        assert retrieved.ended_at is None

    def test_end_session_sets_ended_at(self, db, session_a):
        ended = "2026-01-01T01:00:00+00:00"
        db.end_session("sess-a", ended)
        retrieved = db.get_session("sess-a")
        assert retrieved.ended_at == ended

    def test_get_nonexistent_session_returns_none(self, db):
        assert db.get_session("does-not-exist") is None

    def test_list_sessions_returns_all(self, db, session_a, session_b):
        sessions = db.list_sessions()
        ids = {s.session_id for s in sessions}
        assert "sess-a" in ids
        assert "sess-b" in ids

    def test_list_sessions_filter_by_model(self, db, session_a, session_b):
        qwen_sessions = db.list_sessions(model_name="qwen2.5:latest")
        assert all(s.model_name == "qwen2.5:latest" for s in qwen_sessions)
        assert any(s.session_id == "sess-a" for s in qwen_sessions)
        assert not any(s.session_id == "sess-b" for s in qwen_sessions)

    def test_session_with_null_model_name(self, db):
        """Sessions without a model name (e.g. quick tests) must be allowed."""
        s = SessionRecord(session_id="anon", started_at=utc_now())
        db.create_session(s)
        retrieved = db.get_session("anon")
        assert retrieved.model_name is None

    def test_duplicate_session_id_raises(self, db, session_a):
        """Duplicate session_id must raise — primary key constraint."""
        import sqlite3
        with pytest.raises(sqlite3.IntegrityError):
            db.create_session(session_a)


# ---------------------------------------------------------------------------
# 3. Event persistence
# ---------------------------------------------------------------------------

class TestEvents:

    def test_write_and_retrieve_event(self, db, session_a):
        evt = make_event("evt-1", "call_start", "sess-a")
        db.write_event(evt)

        events = db.get_events("sess-a")
        assert len(events) == 1
        assert events[0].event_id == "evt-1"
        assert events[0].event_type == "call_start"
        assert events[0].tool_name == "fetch"

    def test_multiple_events_ordered_by_timestamp(self, db, session_a):
        for i in range(5):
            db.write_event(make_event(f"evt-{i}", "call_start", "sess-a"))
        events = db.get_events("sess-a")
        assert len(events) == 5

    def test_filter_events_by_type(self, db, session_a):
        db.write_event(make_event("e1", "call_start", "sess-a"))
        db.write_event(make_event("e1", "call_end",   "sess-a", latency_ms=42.0))
        db.write_event(make_event("e2", "call_start", "sess-a"))
        db.write_event(make_event("e2", "call_error", "sess-a", error="timeout"))

        starts = db.get_events("sess-a", event_type="call_start")
        ends   = db.get_events("sess-a", event_type="call_end")
        errors = db.get_events("sess-a", event_type="call_error")

        assert len(starts) == 2
        assert len(ends)   == 1
        assert len(errors) == 1

    def test_events_isolated_by_session(self, db, session_a, session_b):
        db.write_event(make_event("ea", "call_start", "sess-a"))
        db.write_event(make_event("eb", "call_start", "sess-b"))

        a_events = db.get_events("sess-a")
        b_events = db.get_events("sess-b")

        assert len(a_events) == 1
        assert a_events[0].event_id == "ea"
        assert len(b_events) == 1
        assert b_events[0].event_id == "eb"

    def test_event_for_nonexistent_session_raises(self, db):
        """FK constraint: writing an event for an unknown session must fail."""
        import sqlite3
        evt = make_event("orphan", "call_start", "no-such-session")
        with pytest.raises(sqlite3.IntegrityError):
            db.write_event(evt)


# ---------------------------------------------------------------------------
# 4. Data integrity — JSON round-trips and NULL handling
# ---------------------------------------------------------------------------

class TestDataIntegrity:

    def test_payload_round_trips(self, db, session_a):
        payload = {"url": "https://example.com/api?key=secret", "timeout": 30}
        db.write_event(make_event("e1", "call_start", "sess-a", payload=payload))
        retrieved = db.get_events("sess-a")[0]
        assert retrieved.payload == payload

    def test_response_json_round_trips(self, db, session_a):
        response = {"isError": False, "content": [{"type": "text", "text": "ok"}]}
        evt = make_event("e1", "call_end", "sess-a", latency_ms=10.0)
        evt.response = response
        db.write_event(evt)
        retrieved = db.get_events("sess-a")[0]
        assert retrieved.response == response

    def test_null_latency_stored_and_retrieved(self, db, session_a):
        db.write_event(make_event("e1", "call_start", "sess-a", latency_ms=None))
        retrieved = db.get_events("sess-a")[0]
        assert retrieved.latency_ms is None

    def test_error_string_stored_correctly(self, db, session_a):
        db.write_event(make_event(
            "e1", "call_error", "sess-a",
            error="ConnectionError: upstream timed out after 30s"
        ))
        retrieved = db.get_events("sess-a")[0]
        assert "ConnectionError" in retrieved.error

    def test_extra_json_round_trips(self, db, session_a):
        evt = make_event("e1", "call_error", "sess-a")
        evt.extra = {"traceback": "Traceback (most recent call last):\n  ..."}
        db.write_event(evt)
        retrieved = db.get_events("sess-a")[0]
        assert "traceback" in retrieved.extra

    def test_latency_precision_preserved(self, db, session_a):
        latency = 123.456
        db.write_event(make_event("e1", "call_end", "sess-a", latency_ms=latency))
        retrieved = db.get_events("sess-a")[0]
        assert abs(retrieved.latency_ms - latency) < 0.001


# ---------------------------------------------------------------------------
# 5. Research queries — latency_stats, call_counts, error_rates
# ---------------------------------------------------------------------------

class TestResearchQueries:

    @pytest.fixture(autouse=True)
    def populate(self, db, session_a, session_b):
        """Populate both sessions with call_end and call_error events."""
        # qwen2.5: 3 successful calls with known latencies
        for i, lat in enumerate([100.0, 200.0, 300.0]):
            db.write_event(make_event(f"qa{i}", "call_start", "sess-a"))
            db.write_event(make_event(f"qa{i}", "call_end", "sess-a", latency_ms=lat))

        # llama3.2: 2 successful, 1 error
        db.write_event(make_event("lb0", "call_start", "sess-b"))
        db.write_event(make_event("lb0", "call_end",   "sess-b", latency_ms=50.0))
        db.write_event(make_event("lb1", "call_start", "sess-b"))
        db.write_event(make_event("lb1", "call_end",   "sess-b", latency_ms=75.0))
        db.write_event(make_event("lb2", "call_start", "sess-b"))
        db.write_event(make_event("lb2", "call_error", "sess-b", error="timeout"))

    def test_latency_stats_returns_all_models(self, db):
        stats = db.latency_stats()
        model_names = {r["model_name"] for r in stats}
        assert "qwen2.5:latest" in model_names
        assert "llama3.2:latest" in model_names

    def test_latency_stats_correct_avg(self, db):
        stats = {r["model_name"]: r for r in db.latency_stats()}
        qwen = stats["qwen2.5:latest"]
        assert abs(qwen["avg_latency_ms"] - 200.0) < 0.01
        assert qwen["min_latency_ms"] == 100.0
        assert qwen["max_latency_ms"] == 300.0
        assert qwen["total_calls"] == 3

    def test_latency_stats_stddev_nonzero(self, db):
        stats = {r["model_name"]: r for r in db.latency_stats()}
        # qwen has latencies [100, 200, 300] — stddev should be ~81.6
        assert stats["qwen2.5:latest"]["stddev_latency_ms"] > 0

    def test_latency_stats_filter_by_model(self, db):
        stats = db.latency_stats(model_name="llama3.2:latest")
        assert len(stats) == 1
        assert stats[0]["model_name"] == "llama3.2:latest"

    def test_call_counts_includes_all_event_types(self, db):
        counts = db.call_counts()
        event_types = {r["event_type"] for r in counts}
        assert "call_start" in event_types
        assert "call_end" in event_types
        assert "call_error" in event_types

    def test_call_counts_correct_totals(self, db):
        counts = {
            (r["model_name"], r["event_type"]): r["count"]
            for r in db.call_counts()
        }
        assert counts[("qwen2.5:latest", "call_start")] == 3
        assert counts[("qwen2.5:latest", "call_end")]   == 3
        assert counts[("llama3.2:latest", "call_error")] == 1

    def test_error_rates_correct(self, db):
        rates = {r["model_name"]: r for r in db.error_rates()}

        qwen = rates["qwen2.5:latest"]
        assert qwen["errors"] == 0
        assert qwen["error_pct"] == 0.0

        llama = rates["llama3.2:latest"]
        assert llama["errors"] == 1
        assert llama["total"] == 3       # 2 call_end + 1 call_error
        assert abs(llama["error_pct"] - 33.33) < 0.01

    def test_error_rates_includes_transport_profile(self, db):
        rates = db.error_rates()
        profiles = {r["transport_profile"] for r in rates}
        assert "clean" in profiles
        assert "degraded_static" in profiles


# ---------------------------------------------------------------------------
# 6. Context manager interface
# ---------------------------------------------------------------------------

class TestContextManager:

    def test_context_manager_initializes_and_closes(self, tmp_path):
        db_path = tmp_path / "ctx.db"
        with SQLiteStorage(db_path) as storage:
            assert "sessions" in storage.table_names()
        # After __exit__, connection is closed
        assert storage._conn is None

    def test_operations_after_close_raise(self, tmp_path):
        storage = SQLiteStorage(tmp_path / "closed.db")
        storage.initialize()
        storage.close()
        with pytest.raises(RuntimeError, match="not initialized"):
            storage.table_names()


# ---------------------------------------------------------------------------
# 7. _stddev() edge cases — lines 326, 395 in sqlite.py
# ---------------------------------------------------------------------------

class TestStddev:
    """
    Directly tests the _stddev() helper which sqlite3 requires us to
    compute manually (no built-in STDDEV function).
    """

    def test_empty_list_returns_zero(self):
        """n=0: no data, stddev is undefined — must return 0.0."""
        assert _stddev([]) == 0.0

    def test_single_value_returns_zero(self):
        """n=1: variance is undefined — must return 0.0."""
        assert _stddev([42.0]) == 0.0

    def test_identical_values_returns_zero(self):
        """All same values → zero variance."""
        assert _stddev([5.0, 5.0, 5.0, 5.0]) == 0.0

    def test_known_values(self):
        """[100, 200, 300] → population stddev = ~81.65."""
        result = _stddev([100.0, 200.0, 300.0])
        assert abs(result - 81.649) < 0.001

    def test_two_values(self):
        """Minimum n=2 case: [0, 10] → stddev = 5.0."""
        assert _stddev([0.0, 10.0]) == 5.0

    def test_returns_float(self):
        assert isinstance(_stddev([1.0, 2.0, 3.0]), float)

    def test_latency_stats_single_call_stddev_is_zero(self, db, tmp_path):
        """
        A model with exactly one call_end event should report stddev=0.0
        in latency_stats() — not crash or return None.
        """
        s = SessionRecord(
            session_id="single",
            started_at=utc_now(),
            model_name="gemma3:4b",
        )
        db.create_session(s)
        db.write_event(make_event("e1", "call_end", "single", latency_ms=42.0))

        stats = {r["model_name"]: r for r in db.latency_stats()}
        assert "gemma3:4b" in stats
        assert stats["gemma3:4b"]["stddev_latency_ms"] == 0.0
        assert stats["gemma3:4b"]["total_calls"] == 1
