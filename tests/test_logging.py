"""
test_logging.py - Logging correctness tests.

Every intercepted call must produce well-formed, complete log entries.
These tests validate the schema, completeness, and JSON serializability
of every event type.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

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
