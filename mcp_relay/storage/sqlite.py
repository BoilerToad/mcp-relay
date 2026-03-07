"""
mcp_relay.storage.sqlite - SQLite storage backend.

v1 default backend. Zero external dependencies — uses stdlib sqlite3.
DBeaver connects to the .db file directly for ad-hoc queries.

Schema
------
sessions  — one row per relay session (model, profile, timestamps)
events    — one row per intercepted call event (linked to session)

Research queries are implemented as methods so callers don't need raw SQL,
but the schema is intentionally simple so DBeaver / pandas can query it
directly without any ORM.
"""

from __future__ import annotations

import json
import math
import sqlite3
from pathlib import Path
from typing import Any

from mcp_relay.storage.base import EventRecord, SessionRecord, StorageBackend

# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------

_CREATE_SESSIONS = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id          TEXT PRIMARY KEY,
    started_at          TEXT NOT NULL,
    ended_at            TEXT,
    model_name          TEXT,
    transport_profile   TEXT,
    upstream_command    TEXT,
    notes               TEXT
);
"""

_CREATE_EVENTS = """
CREATE TABLE IF NOT EXISTS events (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id            TEXT NOT NULL,
    event_type          TEXT NOT NULL,
    session_id          TEXT NOT NULL REFERENCES sessions(session_id),
    timestamp           TEXT NOT NULL,
    tool_name           TEXT NOT NULL,
    transport_mode      TEXT NOT NULL,
    payload             TEXT,
    response            TEXT,
    error               TEXT,
    latency_ms          REAL,
    upstream_command    TEXT,
    extra               TEXT
);
"""

_CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_events_session   ON events(session_id);",
    "CREATE INDEX IF NOT EXISTS idx_events_type      ON events(event_type);",
    "CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp);",
    "CREATE INDEX IF NOT EXISTS idx_events_tool      ON events(tool_name);",
    "CREATE INDEX IF NOT EXISTS idx_sessions_model   ON sessions(model_name);",
]

# ---------------------------------------------------------------------------
# Backend
# ---------------------------------------------------------------------------

class SQLiteStorage(StorageBackend):
    """
    SQLite storage backend for mcp-relay.

    Thread-safety: SQLite in WAL mode handles concurrent readers well.
    For concurrent writers use PostgreSQL instead.
    """

    def __init__(self, db_path: str | Path) -> None:
        self._path = Path(db_path).expanduser()
        self._conn: sqlite3.Connection | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def initialize(self) -> None:
        """Create the database file, tables, and indexes. Idempotent."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            self._path,
            check_same_thread=False,
            isolation_level=None,   # autocommit
        )
        self._conn.row_factory = sqlite3.Row
        # Enable WAL for better concurrent read performance
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA foreign_keys=ON;")
        # Create schema
        self._conn.execute(_CREATE_SESSIONS)
        self._conn.execute(_CREATE_EVENTS)
        for idx in _CREATE_INDEXES:
            self._conn.execute(idx)

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @property
    def _db(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError(
                "SQLiteStorage not initialized. "
                "Call initialize() or use as context manager."
            )
        return self._conn

    def _row_to_session(self, row: sqlite3.Row) -> SessionRecord:
        return SessionRecord(
            session_id=row["session_id"],
            started_at=row["started_at"],
            ended_at=row["ended_at"],
            model_name=row["model_name"],
            transport_profile=row["transport_profile"],
            upstream_command=row["upstream_command"],
            notes=row["notes"],
        )

    def _row_to_event(self, row: sqlite3.Row) -> EventRecord:
        return EventRecord(
            event_id=row["event_id"],
            event_type=row["event_type"],
            session_id=row["session_id"],
            timestamp=row["timestamp"],
            tool_name=row["tool_name"],
            transport_mode=row["transport_mode"],
            payload=json.loads(row["payload"]) if row["payload"] else {},
            response=json.loads(row["response"]) if row["response"] else None,
            error=row["error"],
            latency_ms=row["latency_ms"],
            upstream_command=row["upstream_command"],
            extra=json.loads(row["extra"]) if row["extra"] else {},
        )

    # ------------------------------------------------------------------
    # Session operations
    # ------------------------------------------------------------------

    def create_session(self, session: SessionRecord) -> None:
        """Upsert a session row. Re-runs with the same session_id overwrite."""
        self._db.execute(
            """
            INSERT OR REPLACE INTO sessions
                (session_id, started_at, ended_at, model_name,
                 transport_profile, upstream_command, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session.session_id,
                session.started_at,
                session.ended_at,
                session.model_name,
                session.transport_profile,
                session.upstream_command,
                session.notes,
            ),
        )

    def delete_events_for_session(self, session_id: str) -> None:
        """Delete all events linked to a session — call before re-recording."""
        self._db.execute(
            "DELETE FROM events WHERE session_id = ?", (session_id,)
        )

    def end_session(self, session_id: str, ended_at: str) -> None:
        self._db.execute(
            "UPDATE sessions SET ended_at = ? WHERE session_id = ?",
            (ended_at, session_id),
        )

    def get_session(self, session_id: str) -> SessionRecord | None:
        row = self._db.execute(
            "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        return self._row_to_session(row) if row else None

    def list_sessions(
        self,
        model_name: str | None = None,
        limit: int = 100,
    ) -> list[SessionRecord]:
        if model_name:
            rows = self._db.execute(
                "SELECT * FROM sessions WHERE model_name = ? "
                "ORDER BY started_at DESC LIMIT ?",
                (model_name, limit),
            ).fetchall()
        else:
            rows = self._db.execute(
                "SELECT * FROM sessions ORDER BY started_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._row_to_session(r) for r in rows]

    # ------------------------------------------------------------------
    # Event operations
    # ------------------------------------------------------------------

    def write_event(self, event: EventRecord) -> None:
        self._db.execute(
            """
            INSERT INTO events
                (event_id, event_type, session_id, timestamp, tool_name,
                 transport_mode, payload, response, error, latency_ms,
                 upstream_command, extra)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.event_id,
                event.event_type,
                event.session_id,
                event.timestamp,
                event.tool_name,
                event.transport_mode,
                json.dumps(event.payload) if event.payload else None,
                json.dumps(event.response) if event.response else None,
                event.error,
                event.latency_ms,
                event.upstream_command,
                json.dumps(event.extra) if event.extra else None,
            ),
        )

    def get_events(
        self,
        session_id: str,
        event_type: str | None = None,
    ) -> list[EventRecord]:
        if event_type:
            rows = self._db.execute(
                "SELECT * FROM events WHERE session_id = ? AND event_type = ? "
                "ORDER BY timestamp",
                (session_id, event_type),
            ).fetchall()
        else:
            rows = self._db.execute(
                "SELECT * FROM events WHERE session_id = ? ORDER BY timestamp",
                (session_id,),
            ).fetchall()
        return [self._row_to_event(r) for r in rows]

    # ------------------------------------------------------------------
    # Research / comparison queries
    # ------------------------------------------------------------------

    def latency_stats(
        self,
        model_name: str | None = None,
    ) -> list[dict[str, Any]]:
        """Per-model latency stats across all call_end events."""
        sql = """
            SELECT
                s.model_name,
                COUNT(*)                    AS total_calls,
                AVG(e.latency_ms)           AS avg_latency_ms,
                MIN(e.latency_ms)           AS min_latency_ms,
                MAX(e.latency_ms)           AS max_latency_ms
            FROM events e
            JOIN sessions s ON e.session_id = s.session_id
            WHERE e.event_type = 'call_end'
            {where}
            GROUP BY s.model_name
            ORDER BY avg_latency_ms
        """
        if model_name:
            rows = self._db.execute(
                sql.format(where="AND s.model_name = ?"), (model_name,)
            ).fetchall()
        else:
            rows = self._db.execute(sql.format(where="")).fetchall()

        results = []
        for r in rows:
            latencies = [
                row[0]
                for row in self._db.execute(
                    """
                    SELECT e.latency_ms FROM events e
                    JOIN sessions s ON e.session_id = s.session_id
                    WHERE e.event_type = 'call_end'
                      AND e.latency_ms IS NOT NULL
                      AND s.model_name = ?
                    """,
                    (r["model_name"],),
                ).fetchall()
            ]
            stddev = _stddev(latencies)
            results.append({
                "model_name":        r["model_name"],
                "total_calls":       r["total_calls"],
                "avg_latency_ms":    round(r["avg_latency_ms"] or 0, 3),
                "min_latency_ms":    round(r["min_latency_ms"] or 0, 3),
                "max_latency_ms":    round(r["max_latency_ms"] or 0, 3),
                "stddev_latency_ms": round(stddev, 3),
            })
        return results

    def call_counts(
        self,
        model_name: str | None = None,
    ) -> list[dict[str, Any]]:
        """Call counts grouped by model and event_type."""
        sql = """
            SELECT
                s.model_name,
                e.event_type,
                COUNT(*) AS count
            FROM events e
            JOIN sessions s ON e.session_id = s.session_id
            {where}
            GROUP BY s.model_name, e.event_type
            ORDER BY s.model_name, e.event_type
        """
        if model_name:
            rows = self._db.execute(
                sql.format(where="WHERE s.model_name = ?"), (model_name,)
            ).fetchall()
        else:
            rows = self._db.execute(sql.format(where="")).fetchall()
        return [dict(r) for r in rows]

    def error_rates(self) -> list[dict[str, Any]]:
        """Error rate per model and transport profile."""
        rows = self._db.execute(
            """
            SELECT
                s.model_name,
                s.transport_profile,
                COUNT(*)  AS total,
                SUM(CASE WHEN e.event_type = 'call_error' THEN 1 ELSE 0 END)
                          AS errors
            FROM events e
            JOIN sessions s ON e.session_id = s.session_id
            WHERE e.event_type IN ('call_end', 'call_error')
            GROUP BY s.model_name, s.transport_profile
            ORDER BY s.model_name
            """
        ).fetchall()
        results = []
        for r in rows:
            total  = r["total"] or 1
            errors = r["errors"] or 0
            results.append({
                "model_name":        r["model_name"],
                "transport_profile": r["transport_profile"],
                "total":             total,
                "errors":            errors,
                "error_pct":         round(100.0 * errors / total, 2),
            })
        return results

    # ------------------------------------------------------------------
    # Introspection (for tests)
    # ------------------------------------------------------------------

    def table_names(self) -> list[str]:
        rows = self._db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        return [r[0] for r in rows]

    def index_names(self) -> list[str]:
        rows = self._db.execute(
            "SELECT name FROM sqlite_master WHERE type='index' ORDER BY name"
        ).fetchall()
        return [r[0] for r in rows]

    def column_names(self, table: str) -> list[str]:
        rows = self._db.execute(f"PRAGMA table_info({table})").fetchall()
        return [r[1] for r in rows]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _stddev(values: list[float]) -> float:
    """Population standard deviation (sqlite3 has no built-in STDDEV)."""
    n = len(values)
    if n < 2:
        return 0.0
    mean = sum(values) / n
    variance = sum((x - mean) ** 2 for x in values) / n
    return math.sqrt(variance)
