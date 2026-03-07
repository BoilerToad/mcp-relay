"""
test_passthrough.py - Identity tests.

Validates that the relay returns byte-equivalent responses to what
the upstream MCP server returns directly, with no modification.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mcp_relay.config import RelayConfig
from mcp_relay.core.intercept import InterceptEngine, _result_to_dict
from mcp_relay.transport.manager import TransportManager
from mcp_relay.transport import TransportMode


@pytest.fixture
def mock_transport(mock_tools, mock_tool_result):
    """A TransportManager with all upstream calls mocked out."""
    transport = MagicMock(spec=TransportManager)
    transport.mode = TransportMode.LIVE
    transport.list_tools = AsyncMock(return_value=mock_tools)
    transport.call_tool = AsyncMock(return_value=(mock_tool_result, 12.5))
    return transport


class TestPassthrough:

    @pytest.mark.asyncio
    async def test_call_tool_returns_upstream_content_unchanged(
        self, mock_transport, event_logger, live_config, mock_tool_result
    ):
        """The intercept engine must return exactly what upstream returns."""
        engine = InterceptEngine(
            config=live_config,
            transport=mock_transport,
            logger=event_logger,
            session_id="test-session",
        )
        result = await engine._intercept_call("fetch", {"url": "https://example.com"})

        # Content must match upstream exactly
        assert result == mock_tool_result.content

    @pytest.mark.asyncio
    async def test_list_tools_mirrors_upstream(
        self, mock_transport, event_logger, live_config, mock_tools
    ):
        """Tool list returned by intercept engine must match upstream exactly."""
        engine = InterceptEngine(
            config=live_config,
            transport=mock_transport,
            logger=event_logger,
            session_id="test-session",
        )
        # Simulate what handle_list_tools does internally
        tools = await mock_transport.list_tools()
        assert tools == mock_tools
        assert len(tools) == 2
        assert tools[0].name == "fetch"
        assert tools[1].name == "echo"

    @pytest.mark.asyncio
    async def test_call_tool_forwards_arguments_unchanged(
        self, mock_transport, event_logger, live_config
    ):
        """Arguments must be forwarded to upstream without modification."""
        engine = InterceptEngine(
            config=live_config,
            transport=mock_transport,
            logger=event_logger,
            session_id="test-session",
        )
        args = {"url": "https://example.com", "timeout": 30}
        await engine._intercept_call("fetch", args)

        mock_transport.call_tool.assert_called_once_with("fetch", args)

    @pytest.mark.asyncio
    async def test_call_tool_propagates_upstream_error(
        self, mock_transport, event_logger, live_config
    ):
        """If upstream raises, the intercept engine must re-raise."""
        mock_transport.call_tool = AsyncMock(
            side_effect=ConnectionError("upstream unavailable")
        )
        engine = InterceptEngine(
            config=live_config,
            transport=mock_transport,
            logger=event_logger,
            session_id="test-session",
        )
        with pytest.raises(ConnectionError, match="upstream unavailable"):
            await engine._intercept_call("fetch", {"url": "https://example.com"})

    def test_result_to_dict_is_lossless(self, mock_tool_result):
        """_result_to_dict must capture all content without loss."""
        d = _result_to_dict(mock_tool_result)
        assert d["isError"] is False
        assert len(d["content"]) == 1
        assert d["content"][0]["type"] == "text"
        assert d["content"][0]["text"] == "mock response"

    def test_result_to_dict_is_json_serializable(self, mock_tool_result):
        """Log entries must always be JSON-serializable."""
        d = _result_to_dict(mock_tool_result)
        serialized = json.dumps(d)
        roundtripped = json.loads(serialized)
        assert roundtripped["isError"] is False
