"""
mcp_relay.core.intercept - MCP server façade (the intercept engine).

This is the face the LLM sees.  It presents itself as a normal MCP server,
mirrors the upstream tool schemas exactly, and for every tool call it:

  1. Emits a CALL_START event to the logger
  2. Forwards the call via the TransportManager
  3. Emits a CALL_END (or CALL_ERROR) event with full payload + latency
  4. Returns the response unmodified to the caller

Nothing here modifies the call or response — that is the policy engine's job (v2).
"""

from __future__ import annotations

import traceback
import uuid
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import (
    CallToolRequest,
    CallToolResult,
    ListToolsRequest,
    TextContent,
    Tool,
)

from mcp_relay.config import RelayConfig
from mcp_relay.core.logging import CallEvent, EventLogger, EventType, utc_now
from mcp_relay.transport.manager import TransportManager


class InterceptEngine:
    """
    Wraps an MCP Server instance and wires it to the TransportManager.

    Every tool call passes through here before and after hitting upstream.
    """

    def __init__(
        self,
        config: RelayConfig,
        transport: TransportManager,
        logger: EventLogger,
        session_id: str | None = None,
    ) -> None:
        self._config = config
        self._transport = transport
        self._logger = logger
        self._session_id = session_id or str(uuid.uuid4())
        self._server = Server(config.name)
        self._tools: list[Tool] = []
        self._register_handlers()

    # ------------------------------------------------------------------
    # MCP server wiring
    # ------------------------------------------------------------------

    def _register_handlers(self) -> None:
        """Register list_tools and call_tool handlers on the MCP server."""

        @self._server.list_tools()
        async def handle_list_tools() -> list[Tool]:
            # Refresh from upstream on every list request so schema changes propagate
            self._tools = await self._transport.list_tools()
            return self._tools

        @self._server.call_tool()
        async def handle_call_tool(
            name: str,
            arguments: dict[str, Any] | None,
        ) -> list[TextContent]:
            return await self._intercept_call(name, arguments or {})

    async def _intercept_call(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> list[TextContent]:
        """Core intercept logic — log, forward, log, return."""
        event_id = str(uuid.uuid4())
        mode = self._transport.mode.value

        # --- CALL_START ---
        self._logger.log(
            CallEvent(
                event_id=event_id,
                event_type=EventType.CALL_START,
                timestamp=utc_now(),
                session_id=self._session_id,
                tool_name=tool_name,
                transport_mode=mode,
                payload=arguments,
                upstream_command=self._config.upstream.command,
            )
        )

        # --- Forward ---
        try:
            result, latency_ms = await self._transport.call_tool(tool_name, arguments)

            # --- CALL_END ---
            self._logger.log(
                CallEvent(
                    event_id=event_id,
                    event_type=EventType.CALL_END,
                    timestamp=utc_now(),
                    session_id=self._session_id,
                    tool_name=tool_name,
                    transport_mode=mode,
                    payload=arguments,
                    response=_result_to_dict(result),
                    latency_ms=round(latency_ms, 3),
                    upstream_command=self._config.upstream.command,
                )
            )

            # Return the upstream content unchanged
            return result.content  # type: ignore[return-value]

        except Exception as exc:
            # --- CALL_ERROR ---
            self._logger.log(
                CallEvent(
                    event_id=event_id,
                    event_type=EventType.CALL_ERROR,
                    timestamp=utc_now(),
                    session_id=self._session_id,
                    tool_name=tool_name,
                    transport_mode=mode,
                    payload=arguments,
                    error=f"{type(exc).__name__}: {exc}",
                    upstream_command=self._config.upstream.command,
                    extra={"traceback": traceback.format_exc()},
                )
            )
            raise

    # ------------------------------------------------------------------
    # Run
    # ------------------------------------------------------------------

    async def run_stdio(self) -> None:
        """Start the MCP server on stdio — blocks until the client disconnects."""
        async with stdio_server() as (read_stream, write_stream):
            await self._server.run(
                read_stream,
                write_stream,
                self._server.create_initialization_options(),
            )


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _result_to_dict(result: CallToolResult) -> dict[str, Any]:
    """Losslessly convert a CallToolResult to a plain dict for logging."""
    return {
        "isError": result.isError,
        "content": [
            {"type": c.type, "text": c.text}  # type: ignore[union-attr]
            if hasattr(c, "text")
            else {"type": c.type}
            for c in result.content
        ],
    }
