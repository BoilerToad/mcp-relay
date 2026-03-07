"""
mcp_relay.transport.live - LIVE transport.

Forwards tool calls to the real upstream MCP server and returns the
response unmodified.  This is the v1 primary implementation — every
other transport mode wraps or replaces this one.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import CallToolResult, Tool

from mcp_relay.config import UpstreamConfig


class LiveTransport:
    """
    Transparent pass-through to an upstream MCP server via stdio.

    Lifecycle:
        async with LiveTransport(upstream_config) as transport:
            tools = await transport.list_tools()
            result = await transport.call_tool("fetch", {"url": "..."})
    """

    def __init__(self, upstream: UpstreamConfig) -> None:
        if not upstream.command:
            raise ValueError(
                "LiveTransport requires upstream.command to be set in relay.yaml"
            )
        self._upstream = upstream
        self._session: ClientSession | None = None
        self._cm: Any = None          # async context manager stack
        self._tools: list[Tool] = []

    # ------------------------------------------------------------------
    # Async context manager
    # ------------------------------------------------------------------

    async def __aenter__(self) -> "LiveTransport":
        params = StdioServerParameters(
            command=self._upstream.command,
            args=self._upstream.args,
            env=self._upstream.env or None,
        )
        self._cm = stdio_client(params)
        read, write = await self._cm.__aenter__()
        self._session = ClientSession(read, write)
        await self._session.__aenter__()
        await self._session.initialize()
        # Cache tool list on connect
        tools_response = await self._session.list_tools()
        self._tools = tools_response.tools
        return self

    async def __aexit__(self, *args: Any) -> None:
        if self._session:
            await self._session.__aexit__(*args)
        if self._cm:
            await self._cm.__aexit__(*args)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def tools(self) -> list[Tool]:
        """Cached list of tools advertised by the upstream server."""
        return self._tools

    async def list_tools(self) -> list[Tool]:
        """Refresh and return the upstream tool list."""
        assert self._session is not None, "Transport not started"
        response = await self._session.list_tools()
        self._tools = response.tools
        return self._tools

    async def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> tuple[CallToolResult, float]:
        """
        Forward a tool call to the upstream server.

        Returns:
            (result, latency_ms) — the raw CallToolResult and wall-clock
            latency in milliseconds.
        """
        assert self._session is not None, "Transport not started"
        t0 = time.perf_counter()
        result = await self._session.call_tool(tool_name, arguments)
        latency_ms = (time.perf_counter() - t0) * 1000
        return result, latency_ms
