"""
mcp_relay.transport.manager - Transport manager.

Owns the current TransportMode and delegates call_tool() to the
appropriate transport implementation.  v1 only implements LIVE;
the other modes are stubbed and will raise NotImplementedError until
their modules are filled in.
"""

from __future__ import annotations

import logging
from typing import Any

from mcp.types import CallToolResult, Tool

from mcp_relay.config import RelayConfig
from mcp_relay.transport import TransportMode
from mcp_relay.transport.live import LiveTransport

logger = logging.getLogger("mcp_relay.transport")


class TransportManager:
    """
    Owns lifecycle and mode of the active transport.

    Usage:
        async with TransportManager(config) as mgr:
            tools = await mgr.list_tools()
            result, latency = await mgr.call_tool("fetch", {...})
    """

    def __init__(self, config: RelayConfig) -> None:
        self._config = config
        self._mode = config.transport.default_mode
        self._live: LiveTransport | None = None

    # ------------------------------------------------------------------
    # Async context manager
    # ------------------------------------------------------------------

    async def __aenter__(self) -> "TransportManager":
        if self._mode == TransportMode.LIVE:
            self._live = LiveTransport(self._config.upstream)
            await self._live.__aenter__()
        else:
            raise NotImplementedError(
                f"Transport mode {self._mode.value} is not yet implemented. "
                "Only LIVE is available in v0.1."
            )
        logger.info("Transport started in mode: %s", self._mode.value)
        return self

    async def __aexit__(self, *args: Any) -> None:
        if self._live:
            await self._live.__aexit__(*args)
            self._live = None
        logger.info("Transport stopped.")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def mode(self) -> TransportMode:
        return self._mode

    def set_mode(self, mode: TransportMode) -> None:
        """Hot-switch the transport mode (used by profile runner)."""
        logger.info("Transport mode switching: %s -> %s", self._mode.value, mode.value)
        self._mode = mode

    async def list_tools(self) -> list[Tool]:
        """Return tools available from the upstream server."""
        self._assert_live()
        assert self._live is not None
        return await self._live.list_tools()

    async def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> tuple[CallToolResult, float]:
        """
        Dispatch a tool call through the active transport.

        Returns:
            (CallToolResult, latency_ms)
        """
        if self._mode == TransportMode.LIVE:
            self._assert_live()
            assert self._live is not None
            return await self._live.call_tool(tool_name, arguments)
        elif self._mode == TransportMode.OFFLINE:
            raise ConnectionRefusedError(
                "Transport is OFFLINE — all calls are blocked."
            )
        else:
            raise NotImplementedError(
                f"Transport mode {self._mode.value} is not yet implemented."
            )

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _assert_live(self) -> None:
        if self._live is None:
            raise RuntimeError(
                "TransportManager is not started. Use 'async with TransportManager(...)'"
            )
