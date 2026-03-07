"""
mcp_relay.core.intercept - MCP server façade (the intercept engine).

This is the face the LLM sees.  It presents itself as a normal MCP server,
mirrors the upstream tool schemas exactly, and for every tool call it:

  1. Evaluates the PolicyEngine — BLOCK raises PolicyViolationError (logged, not forwarded)
  2. Emits a CALL_START event to the logger
  3. Forwards the call via the TransportManager
  4. Emits a CALL_END (or CALL_ERROR) event with full payload + latency
  5. Returns the response unmodified to the caller

Return value conventions
------------------------
_intercept_call()    → (CallToolResult, latency_ms)   — programmatic / test path
_handle_mcp_call()   → list[TextContent]               — MCP stdio server path
"""

from __future__ import annotations

import traceback
import uuid
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import (
    CallToolResult,
    ListToolsRequest,
    TextContent,
    Tool,
)

from mcp_relay.config import RelayConfig
from mcp_relay.core.logging import CallEvent, EventLogger, EventType, utc_now
from mcp_relay.policy.engine import PolicyConfig, PolicyEngine, PolicyViolationError
from mcp_relay.transport.manager import TransportManager


class InterceptEngine:
    """
    Wraps an MCP Server instance and wires it to the TransportManager.

    Every tool call passes through the PolicyEngine before hitting upstream.
    Blocked calls are logged and never forwarded.
    """

    def __init__(
        self,
        config: RelayConfig,
        transport: TransportManager,
        logger: EventLogger,
        session_id: str | None = None,
        policy: PolicyEngine | None = None,
    ) -> None:
        self._config = config
        self._transport = transport
        self._logger = logger
        self._session_id = session_id or str(uuid.uuid4())
        self._server = Server(config.name)
        self._tools: list[Tool] = []
        self._policy = policy or PolicyEngine.default()
        self._register_handlers()

    # ------------------------------------------------------------------
    # MCP server wiring (stdio server path)
    # ------------------------------------------------------------------

    def _register_handlers(self) -> None:
        """Register list_tools and call_tool handlers on the MCP server."""

        @self._server.list_tools()
        async def handle_list_tools() -> list[Tool]:
            self._tools = await self._transport.list_tools()
            return self._tools

        @self._server.call_tool()
        async def handle_call_tool(
            name: str,
            arguments: dict[str, Any] | None,
        ) -> list[TextContent]:
            try:
                result, _ = await self._intercept_call(name, arguments or {})
                return result.content  # type: ignore[return-value]
            except PolicyViolationError as exc:
                # Return a structured error to the model explaining the block
                from mcp.types import TextContent as TC
                return [TC(type="text", text=f"[BLOCKED] {exc}")]

    # ------------------------------------------------------------------
    # Core intercept — returns (CallToolResult, latency_ms)
    # ------------------------------------------------------------------

    async def _intercept_call(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> tuple[CallToolResult, float]:
        """
        Policy check → log → forward → log → return.

        Raises PolicyViolationError if the call is blocked by policy.
        """
        event_id = str(uuid.uuid4())
        mode = self._transport.mode.value

        # --- POLICY CHECK ---
        decision = self._policy.evaluate(tool_name, arguments)
        if decision.is_blocked:
            self._logger.log(
                CallEvent(
                    event_id=event_id,
                    event_type=EventType.CALL_BLOCKED,
                    timestamp=utc_now(),
                    session_id=self._session_id,
                    tool_name=tool_name,
                    transport_mode=mode,
                    payload=arguments,
                    error=decision.reason,
                    upstream_command=self._config.upstream.command,
                    extra={
                        "policy_rule": decision.rule_name,
                        "policy_action": decision.action.value,
                        "policy_detail": decision.detail,
                    },
                )
            )
            raise PolicyViolationError(decision)

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
                extra=(
                    {"policy_rule": decision.rule_name, "policy_action": decision.action.value}
                    if decision.action.value != "ALLOW"
                    else {}
                ),
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

            return result, latency_ms

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
