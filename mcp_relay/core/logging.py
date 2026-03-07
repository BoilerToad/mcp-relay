"""
mcp_relay.core.logging - Structured event logger.

Every tool call intercepted by the relay produces a CallEvent which is:
  - Written as a JSON line to the configured log file (streaming/debug)
  - Written as a row to the SQLite events table (queryable research store)

The SQLiteStorage backend is optional — if not provided, only JSONL is written.
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from mcp_relay.storage.base import EventRecord


class EventType(str, Enum):
    CALL_START   = "call_start"
    CALL_END     = "call_end"
    CALL_ERROR   = "call_error"
    CALL_BLOCKED = "call_blocked"
    MODE_CHANGE  = "mode_change"


@dataclass
class CallEvent:
    """Immutable record of a single tool-call lifecycle event."""
    event_id: str
    event_type: EventType
    timestamp: str
    session_id: str
    tool_name: str
    transport_mode: str
    payload: dict[str, Any] = field(default_factory=dict)
    response: dict[str, Any] | None = None
    error: str | None = None
    latency_ms: float | None = None
    upstream_command: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_jsonl(self) -> str:
        d = asdict(self)
        d["event_type"] = self.event_type.value
        return json.dumps(d, separators=(",", ":"), default=str)

    def to_event_record(self) -> EventRecord:
        """Convert to a storage EventRecord for SQLite persistence."""
        return EventRecord(
            event_id=self.event_id,
            event_type=self.event_type.value,
            session_id=self.session_id,
            timestamp=self.timestamp,
            tool_name=self.tool_name,
            transport_mode=self.transport_mode,
            payload=self.payload,
            response=self.response,
            error=self.error,
            latency_ms=self.latency_ms,
            upstream_command=self.upstream_command,
            extra=self.extra,
        )


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class EventLogger:
    """
    Writes CallEvents to:
      1. A JSONL file  (always — good for streaming and tail -f)
      2. SQLite storage (when storage backend is provided)
    """

    def __init__(
        self,
        output_path: str | Path,
        format: str = "jsonl",
        rotate_mb: int = 50,
        echo_stderr: bool = False,
        storage: Any | None = None,   # StorageBackend | None
    ) -> None:
        self._path = Path(output_path).expanduser()
        self._format = format
        self._rotate_bytes = rotate_mb * 1024 * 1024
        self._echo = echo_stderr
        self._storage = storage
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._file = open(self._path, "a", buffering=1)

    def log(self, event: CallEvent) -> None:
        # 1. JSONL file
        line = event.to_jsonl()
        self._file.write(line + "\n")
        if self._echo:
            print(f"[relay] {line}", file=sys.stderr)
        self._maybe_rotate()

        # 2. SQLite (if wired up)
        if self._storage is not None:
            try:
                self._storage.write_event(event.to_event_record())
            except Exception as exc:
                # Storage errors must never crash the relay
                print(f"[relay] storage write error: {exc}", file=sys.stderr)

    def close(self) -> None:
        self._file.flush()
        self._file.close()

    def _maybe_rotate(self) -> None:
        try:
            size = self._path.stat().st_size
        except OSError:
            return
        if size >= self._rotate_bytes:
            self._file.close()
            rotated = self._path.with_suffix(
                f".{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}.log"
            )
            self._path.rename(rotated)
            self._file = open(self._path, "a", buffering=1)

    def __enter__(self) -> "EventLogger":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()
