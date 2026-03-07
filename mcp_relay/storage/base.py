"""
mcp_relay.storage.base - Abstract storage adapter interface.

All storage backends (SQLite, PostgreSQL, Chroma) implement this interface.
The relay engine only depends on this ABC — never on a concrete backend.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class SessionRecord:
    """Represents one relay session (one model run)."""
    session_id: str
    started_at: str                         # ISO-8601 UTC
    model_name: str | None = None           # e.g. "qwen2.5:latest"
    transport_profile: str | None = None    # e.g. "degraded_recovery"
    upstream_command: str | None = None     # e.g. "uvx mcp-server-fetch"
    ended_at: str | None = None
    notes: str | None = None


@dataclass
class EventRecord:
    """Represents one intercepted call event."""
    event_id: str                           # UUID — links start/end pairs
    event_type: str                         # call_start | call_end | call_error | ...
    session_id: str
    timestamp: str                          # ISO-8601 UTC
    tool_name: str
    transport_mode: str
    payload: dict[str, Any] = field(default_factory=dict)
    response: dict[str, Any] | None = None
    error: str | None = None
    latency_ms: float | None = None
    upstream_command: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


class StorageBackend(ABC):
    """
    Abstract base class for all mcp-relay storage backends.

    Implementations must be safe to call from async contexts
    (use run_in_executor or native async drivers as appropriate).
    """

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    @abstractmethod
    def initialize(self) -> None:
        """Create schema / run migrations. Idempotent."""
        ...

    @abstractmethod
    def close(self) -> None:
        """Flush and release resources."""
        ...

    # ------------------------------------------------------------------
    # Session operations
    # ------------------------------------------------------------------

    @abstractmethod
    def create_session(self, session: SessionRecord) -> None:
        """Insert a new session row."""
        ...

    @abstractmethod
    def end_session(self, session_id: str, ended_at: str) -> None:
        """Mark a session as ended."""
        ...

    @abstractmethod
    def get_session(self, session_id: str) -> SessionRecord | None:
        """Retrieve a session by ID."""
        ...

    @abstractmethod
    def list_sessions(
        self,
        model_name: str | None = None,
        limit: int = 100,
    ) -> list[SessionRecord]:
        """List sessions, optionally filtered by model."""
        ...

    # ------------------------------------------------------------------
    # Event operations
    # ------------------------------------------------------------------

    @abstractmethod
    def write_event(self, event: EventRecord) -> None:
        """Append one event. Must be fast — called on every tool call."""
        ...

    @abstractmethod
    def get_events(
        self,
        session_id: str,
        event_type: str | None = None,
    ) -> list[EventRecord]:
        """Retrieve events for a session, optionally filtered by type."""
        ...

    # ------------------------------------------------------------------
    # Research / comparison queries
    # ------------------------------------------------------------------

    @abstractmethod
    def latency_stats(
        self,
        model_name: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Return per-model latency statistics.

        Columns: model_name, avg_latency_ms, max_latency_ms,
                 min_latency_ms, stddev_latency_ms, total_calls
        """
        ...

    @abstractmethod
    def call_counts(
        self,
        model_name: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Return call counts grouped by model and event_type.

        Columns: model_name, event_type, count
        """
        ...

    @abstractmethod
    def error_rates(self) -> list[dict[str, Any]]:
        """
        Return error rate per model and transport profile.

        Columns: model_name, transport_profile, total, errors, error_pct
        """
        ...

    # ------------------------------------------------------------------
    # Context manager support
    # ------------------------------------------------------------------

    def __enter__(self) -> "StorageBackend":
        self.initialize()
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()
