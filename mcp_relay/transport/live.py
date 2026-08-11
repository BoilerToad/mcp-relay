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

from mcp import Client, StdioServerParameters
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
        self._client: Client | None = None
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
        # mode="auto" probes `server/discover` (2026-07-28+ servers) and falls
        # back to the legacy initialize/initialized handshake for servers that
        # don't support it yet. Replaces the v1 code's direct
        # ClientSession.initialize() call — that method is still present in
        # the 2.0.0 SDK (it's what "auto" falls back to internally), but the
        # high-level Client now owns choosing between the two.
        self._client = Client(stdio_client(params), mode="auto")
        await self._client.__aenter__()
        # Cache tool list on connect
        tools_response = await self._client.list_tools()
        self._tools = tools_response.tools
        return self

    async def __aexit__(self, *args: Any) -> None:
        if self._client:
            await self._client.__aexit__(*args)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def tools(self) -> list[Tool]:
        """Cached list of tools advertised by the upstream server."""
        return self._tools

    async def list_tools(self) -> list[Tool]:
        """Refresh and return the upstream tool list."""
        assert self._client is not None, "Transport not started"
        response = await self._client.list_tools()
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
        assert self._client is not None, "Transport not started"
        t0 = time.perf_counter()
        result = await self._client.call_tool(tool_name, arguments)
        latency_ms = (time.perf_counter() - t0) * 1000
        return result, latency_ms
