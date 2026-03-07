"""
test_transport.py - Transport mode and manager tests.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mcp_relay.config import RelayConfig
from mcp_relay.transport import TransportMode
from mcp_relay.transport.manager import TransportManager


class TestTransportManager:

    def test_default_mode_is_live(self, live_config):
        mgr = TransportManager(live_config)
        assert mgr.mode == TransportMode.LIVE

    def test_set_mode_changes_mode(self, live_config):
        mgr = TransportManager(live_config)
        mgr.set_mode(TransportMode.OFFLINE)
        assert mgr.mode == TransportMode.OFFLINE

    @pytest.mark.asyncio
    async def test_offline_mode_raises_connection_refused(self, live_config):
        """OFFLINE mode must block all calls."""
        mgr = TransportManager(live_config)
        mgr.set_mode(TransportMode.OFFLINE)
        # Don't use __aenter__ since it would try to connect — test dispatch only
        mgr._mode = TransportMode.OFFLINE
        with pytest.raises(ConnectionRefusedError, match="OFFLINE"):
            await mgr.call_tool("fetch", {"url": "https://example.com"})

    @pytest.mark.asyncio
    async def test_unimplemented_mode_raises(self, live_config):
        """Unimplemented modes must raise NotImplementedError, not silently fail."""
        mgr = TransportManager(live_config)
        mgr._mode = TransportMode.DEGRADED
        with pytest.raises(NotImplementedError):
            await mgr.call_tool("fetch", {"url": "https://example.com"})

    @pytest.mark.asyncio
    async def test_call_tool_without_start_raises(self, live_config):
        """Calling tool without starting the manager must raise RuntimeError."""
        mgr = TransportManager(live_config)
        with pytest.raises(RuntimeError, match="not started"):
            await mgr.call_tool("fetch", {"url": "https://example.com"})

    @pytest.mark.asyncio
    async def test_live_mode_delegates_to_live_transport(
        self, live_config, mock_tool_result, mock_tools
    ):
        """In LIVE mode, call_tool must delegate to LiveTransport."""
        from mcp_relay.transport.live import LiveTransport

        with patch(
            "mcp_relay.transport.manager.LiveTransport", autospec=True
        ) as MockLive:
            mock_instance = AsyncMock()
            mock_instance.list_tools = AsyncMock(return_value=mock_tools)
            mock_instance.call_tool = AsyncMock(
                return_value=(mock_tool_result, 10.0)
            )
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            MockLive.return_value = mock_instance

            async with TransportManager(live_config) as mgr:
                result, latency = await mgr.call_tool(
                    "fetch", {"url": "https://example.com"}
                )

            mock_instance.call_tool.assert_called_once_with(
                "fetch", {"url": "https://example.com"}
            )
            assert result == mock_tool_result
            assert latency == 10.0


class TestTransportMode:

    def test_all_modes_defined(self):
        modes = {m.value for m in TransportMode}
        assert modes == {"live", "record", "replay", "degraded", "offline"}

    def test_mode_from_string(self):
        assert TransportMode("live") == TransportMode.LIVE
        assert TransportMode("offline") == TransportMode.OFFLINE
