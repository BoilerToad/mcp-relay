"""
test_logging.py - Logging correctness tests.

Every intercepted call must produce well-formed, complete log entries.
These tests validate the schema, completeness, and JSON serializability
of every event type.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mcp_relay.core.logging import CallEvent, EventLogger, EventType, utc_now
from mcp_relay.core.intercept import InterceptEngine
from mcp_relay.transport import TransportMode
from mcp_relay.transport.manager import TransportManager


class TestCallEvent:

    def test_to_jsonl_is_single_line(self):
        event = CallEvent(
            event_id="abc",
            event_type=EventType.CALL_START,
            timestamp=utc_now(),
            session_id="sess-1",
            tool_name="fetch",
            transport_mode="live",
        )
        line = event.to_jsonl()
        assert "\n" not in line

    def test_to_jsonl_is_valid_json(self):
        event = CallEvent(
            event_id="abc",
            event_type=EventType.CALL_START,
            timestamp=utc_now(),
            session_id="sess-1",
            tool_name="fetch",
            transport_mode="live",
            payload={"url": "https://example.com"},
        )
        parsed = json.loads(event.to_jsonl())
        assert parsed["tool_name"] == "fetch"
        assert parsed["event_type"] == "call_start"
        assert parsed["payload"]["url"] == "https://example.com"

    def test_event_type_serialized_as_string(self):
        event = CallEvent(
            event_id="x",
            event_type=EventType.CALL_END,
            timestamp=utc_now(),
            session_id="s",
            tool_name="echo",
            transport_mode="live",
            latency_ms=42.1,
        )
        parsed = json.loads(event.to_jsonl())
        assert parsed["event_type"] == "call_end"
        assert isinstance(parsed["latency_ms"], float)

    def test_all_event_types_serialize(self):
        for et in EventType:
            event = CallEvent(
                event_id="x",
                event_type=et,
                timestamp=utc_now(),
                session_id="s",
                tool_name="tool",
                transport_mode="live",
            )
            parsed = json.loads(event.to_jsonl())
            assert parsed["event_type"] == et.value

    def test_to_event_record_round_trips_fields(self):
        event = CallEvent(
            event_id="rec-1",
            event_type=EventType.CALL_END,
            timestamp="2026-01-01T00:00:00+00:00",
            session_id="sess-x",
            tool_name="fetch",
            transport_mode="live",
            payload={"url": "https://example.com"},
            latency_ms=99.9,
            upstream_command="uvx mcp-server-fetch",
            extra={"retry": 1},
        )
        record = event.to_event_record()
        assert record.event_id == event.event_id
        assert record.event_type == "call_end"
        assert record.latency_ms == 99.9
        assert record.payload == {"url": "https://example.com"}
        assert record.extra == {"retry": 1}
        assert record.upstream_command == "uvx mcp-server-fetch"


class TestEventLogger:
    """
    Note: the event_logger fixture closes the file in teardown.
    Tests must NOT call event_logger.close() themselves — doing so
    causes a double-close and raises ValueError on the next write.
    Read the log file before the fixture tears down by reading within
    the test body (the file is flushed on every line write).
    """

    def test_log_writes_to_file(self, event_logger):
        event = CallEvent(
            event_id="e1",
            event_type=EventType.CALL_START,
            timestamp=utc_now(),
            session_id="s",
            tool_name="fetch",
            transport_mode="live",
        )
        event_logger.log(event)

        # File is line-buffered — flush to be safe before reading
        event_logger._file.flush()

        lines = Path(event_logger._path).read_text().strip().split("\n")
        assert len(lines) == 1
        parsed = json.loads(lines[0])
        assert parsed["event_id"] == "e1"

    def test_multiple_events_produce_multiple_lines(self, event_logger):
        for i in range(5):
            event_logger.log(
                CallEvent(
                    event_id=f"e{i}",
                    event_type=EventType.CALL_START,
                    timestamp=utc_now(),
                    session_id="s",
                    tool_name="fetch",
                    transport_mode="live",
                )
            )

        event_logger._file.flush()
        lines = Path(event_logger._path).read_text().strip().split("\n")
        assert len(lines) == 5

    def test_each_line_is_valid_json(self, event_logger):
        for i in range(3):
            event_logger.log(
                CallEvent(
                    event_id=f"e{i}",
                    event_type=EventType.CALL_END,
                    timestamp=utc_now(),
                    session_id="s",
                    tool_name="echo",
                    transport_mode="live",
                    latency_ms=float(i * 10),
                )
            )

        event_logger._file.flush()
        for line in Path(event_logger._path).read_text().strip().split("\n"):
            parsed = json.loads(line)
            assert "event_id" in parsed
            assert "timestamp" in parsed
            assert "latency_ms" in parsed


class TestEventLoggerEdgeCases:
    """
    Covers lines 103, 108-112, 121-122, 124-129, 132, 135 in core/logging.py:
      - echo_stderr path
      - storage dual-write path
      - storage error swallow (must never crash relay)
      - log rotation trigger
      - context manager (__enter__ / __exit__)
    """

    def _make_event(self, event_id: str = "e1") -> CallEvent:
        return CallEvent(
            event_id=event_id,
            event_type=EventType.CALL_START,
            timestamp=utc_now(),
            session_id="sess-echo",
            tool_name="fetch",
            transport_mode="live",
        )

    def test_echo_stderr_prints_to_stderr(self, tmp_path, capsys):
        """echo_stderr=True must print the JSON line to stderr."""
        logger = EventLogger(
            output_path=tmp_path / "echo.log",
            echo_stderr=True,
        )
        logger.log(self._make_event())
        logger.close()

        captured = capsys.readouterr()
        assert "[relay]" in captured.err
        assert "call_start" in captured.err

    def test_no_echo_by_default(self, tmp_path, capsys):
        """echo_stderr=False (default) must produce no stderr output."""
        logger = EventLogger(output_path=tmp_path / "noecho.log")
        logger.log(self._make_event())
        logger.close()

        captured = capsys.readouterr()
        assert captured.err == ""

    def test_storage_write_called_when_provided(self, tmp_path):
        """EventLogger must call storage.write_event() for each logged event."""
        mock_storage = MagicMock()
        logger = EventLogger(
            output_path=tmp_path / "dual.log",
            storage=mock_storage,
        )
        logger.log(self._make_event("e1"))
        logger.log(self._make_event("e2"))
        logger.close()

        assert mock_storage.write_event.call_count == 2

    def test_storage_error_does_not_crash_relay(self, tmp_path, capsys):
        """
        A failing storage backend must NEVER propagate an exception —
        the relay must keep logging to JSONL even if SQLite explodes.
        This is a critical audit/governance requirement.
        """
        mock_storage = MagicMock()
        mock_storage.write_event.side_effect = RuntimeError("disk full")

        logger = EventLogger(
            output_path=tmp_path / "resilient.log",
            storage=mock_storage,
        )
        # Must not raise
        logger.log(self._make_event())
        logger.close()

        # JSONL file must still have the event
        lines = (tmp_path / "resilient.log").read_text().strip().split("\n")
        assert len(lines) == 1
        assert json.loads(lines[0])["event_id"] == "e1"

        # Error must be reported to stderr
        captured = capsys.readouterr()
        assert "storage write error" in captured.err

    def test_storage_error_message_contains_exception(self, tmp_path, capsys):
        """The stderr message should include the original exception text."""
        mock_storage = MagicMock()
        mock_storage.write_event.side_effect = OSError("no space left on device")

        logger = EventLogger(
            output_path=tmp_path / "err_msg.log",
            storage=mock_storage,
        )
        logger.log(self._make_event())
        logger.close()

        captured = capsys.readouterr()
        assert "no space left on device" in captured.err

    def test_context_manager_enter_returns_logger(self, tmp_path):
        """__enter__ must return the logger instance itself."""
        logger = EventLogger(output_path=tmp_path / "ctx.log")
        with logger as l:
            assert l is logger

    def test_context_manager_exit_closes_file(self, tmp_path):
        """__exit__ must close the underlying file handle."""
        logger = EventLogger(output_path=tmp_path / "ctx_close.log")
        with logger:
            pass
        assert logger._file.closed

    def test_log_rotation_triggered_when_size_exceeded(self, tmp_path):
        """
        When the log file exceeds rotate_mb, the current file is renamed
        and a new one is opened. The new file should exist and be writable.
        """
        log_path = tmp_path / "rotating.log"
        # Use rotate_mb=0 so any write triggers rotation
        logger = EventLogger(
            output_path=log_path,
            rotate_mb=0,
        )
        logger.log(self._make_event("before"))
        # After rotation, a new file at the original path should exist
        assert log_path.exists()
        # At least one rotated backup should exist
        rotated = list(tmp_path.glob("rotating.*.log"))
        assert len(rotated) >= 1

        logger.log(self._make_event("after"))
        logger.close()

        # New file should have the "after" event
        new_lines = log_path.read_text().strip().split("\n")
        assert any("after" in l for l in new_lines)


class TestInterceptLogging:
    """Verify the intercept engine emits the correct events."""

    @pytest.fixture
    def mock_transport(self, mock_tools, mock_tool_result):
        transport = MagicMock(spec=TransportManager)
        transport.mode = TransportMode.LIVE
        transport.list_tools = AsyncMock(return_value=mock_tools)
        transport.call_tool = AsyncMock(return_value=(mock_tool_result, 55.0))
        return transport

    @pytest.mark.asyncio
    async def test_successful_call_emits_start_and_end(
        self, mock_transport, event_logger, live_config
    ):
        engine = InterceptEngine(
            config=live_config,
            transport=mock_transport,
            logger=event_logger,
            session_id="test-session",
        )
        await engine._intercept_call("fetch", {"url": "https://example.com"})

        event_logger._file.flush()
        lines = Path(event_logger._path).read_text().strip().split("\n")
        assert len(lines) == 2

        events = [json.loads(l) for l in lines]
        assert events[0]["event_type"] == "call_start"
        assert events[1]["event_type"] == "call_end"
        assert events[0]["event_id"] == events[1]["event_id"]

    @pytest.mark.asyncio
    async def test_failed_call_emits_start_and_error(
        self, mock_transport, event_logger, live_config
    ):
        mock_transport.call_tool = AsyncMock(
            side_effect=ConnectionError("timeout")
        )
        engine = InterceptEngine(
            config=live_config,
            transport=mock_transport,
            logger=event_logger,
            session_id="test-session",
        )
        with pytest.raises(ConnectionError):
            await engine._intercept_call("fetch", {"url": "https://example.com"})

        event_logger._file.flush()
        lines = Path(event_logger._path).read_text().strip().split("\n")
        assert len(lines) == 2
        events = [json.loads(l) for l in lines]
        assert events[0]["event_type"] == "call_start"
        assert events[1]["event_type"] == "call_error"
        assert "ConnectionError" in events[1]["error"]

    @pytest.mark.asyncio
    async def test_call_end_records_latency(
        self, mock_transport, event_logger, live_config
    ):
        engine = InterceptEngine(
            config=live_config,
            transport=mock_transport,
            logger=event_logger,
            session_id="test-session",
        )
        await engine._intercept_call("echo", {"message": "hello"})

        event_logger._file.flush()
        lines = Path(event_logger._path).read_text().strip().split("\n")
        end_event = json.loads(lines[1])
        assert end_event["event_type"] == "call_end"
        assert end_event["latency_ms"] == 55.0

    @pytest.mark.asyncio
    async def test_log_captures_payload(
        self, mock_transport, event_logger, live_config
    ):
        args = {"url": "https://secret.example.com/api?key=12345"}
        engine = InterceptEngine(
            config=live_config,
            transport=mock_transport,
            logger=event_logger,
            session_id="test-session",
        )
        await engine._intercept_call("fetch", args)

        event_logger._file.flush()
        start_event = json.loads(
            Path(event_logger._path).read_text().strip().split("\n")[0]
        )
        assert start_event["payload"]["url"] == args["url"]
