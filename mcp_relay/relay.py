"""
mcp_relay.relay - Top-level Relay class.

Public entry point for embedding mcp-relay in any application:

    from mcp_relay import Relay, RelayConfig

    relay = Relay(config=RelayConfig.from_file("relay.yaml"))

    # Programmatic use (tests, demo harness):
    async with relay.session(model_name="qwen2.5:latest") as session:
        tools  = await session.list_tools()
        result, latency = await session.call_tool("fetch", {"url": "..."})

    # Stdio server (production / Ollama integration):
    await relay.run(model_name="llama3.2:latest")
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator

from mcp.types import CallToolResult, Tool

from mcp_relay.config import RelayConfig
from mcp_relay.core.intercept import InterceptEngine
from mcp_relay.core.logging import EventLogger, utc_now
from mcp_relay.storage import SQLiteStorage
from mcp_relay.storage.base import SessionRecord
from mcp_relay.transport.manager import TransportManager

log = logging.getLogger("mcp_relay")


class RelaySession:
    """
    A live relay session — transport connected, logger open, storage active.
    Returned by Relay.session() for programmatic use.
    """

    def __init__(
        self,
        transport: TransportManager,
        logger: EventLogger,
        storage: SQLiteStorage,
        session_id: str,
        model_name: str | None,
    ) -> None:
        self._transport = transport
        self._logger = logger
        self._storage = storage
        self.session_id = session_id
        self.model_name = model_name

    async def list_tools(self) -> list[Tool]:
        return await self._transport.list_tools()

    async def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> tuple[CallToolResult, float]:
        return await self._transport.call_tool(tool_name, arguments)


class Relay:
    """
    Main mcp-relay entry point.

    Two usage patterns:

    1. Programmatic session (tests, demo harness):
           async with relay.session(model_name="qwen2.5:latest") as s:
               result, latency = await s.call_tool(...)

    2. Stdio server (production / Ollama integration):
           await relay.run(model_name="llama3.2:latest")
    """

    def __init__(self, config: RelayConfig | None = None) -> None:
        self._config = config or RelayConfig.defaults()
        self._configure_logging()

    def _configure_logging(self) -> None:
        level = getattr(logging, self._config.log_level.upper(), logging.INFO)
        logging.basicConfig(
            level=level,
            format="%(asctime)s %(name)s %(levelname)s %(message)s",
        )

    def _make_storage(self) -> SQLiteStorage:
        storage = SQLiteStorage(self._config.storage.path)
        storage.initialize()
        return storage

    # ------------------------------------------------------------------
    # Programmatic session context manager
    # ------------------------------------------------------------------

    @asynccontextmanager
    async def session(
        self,
        model_name: str | None = None,
        transport_profile: str | None = None,
        notes: str | None = None,
    ) -> AsyncGenerator[RelaySession, None]:
        """
        Open a relay session. Transport connects, logger opens, session row
        is written to SQLite. Cleans up and closes the session on exit.
        """
        session_id = str(uuid.uuid4())
        started_at = utc_now()

        storage = self._make_storage()
        storage.create_session(SessionRecord(
            session_id=session_id,
            started_at=started_at,
            model_name=model_name,
            transport_profile=transport_profile or self._config.transport.profile,
            upstream_command=self._config.upstream.command,
            notes=notes,
        ))

        event_logger = EventLogger(
            output_path=self._config.logging.output,
            format=self._config.logging.format,
            rotate_mb=self._config.logging.rotate_mb,
            echo_stderr=(self._config.log_level.upper() == "DEBUG"),
            storage=storage,
        )

        async with TransportManager(self._config) as transport:
            try:
                yield RelaySession(
                    transport=transport,
                    logger=event_logger,
                    storage=storage,
                    session_id=session_id,
                    model_name=model_name,
                )
            finally:
                storage.end_session(session_id, utc_now())
                event_logger.close()
                storage.close()

    # ------------------------------------------------------------------
    # Stdio server (blocking)
    # ------------------------------------------------------------------

    async def run(
        self,
        model_name: str | None = None,
        transport_profile: str | None = None,
        notes: str | None = None,
    ) -> None:
        """
        Start the relay as an MCP stdio server. Blocks until client disconnects.
        """
        session_id = str(uuid.uuid4())
        started_at = utc_now()

        storage = self._make_storage()
        storage.create_session(SessionRecord(
            session_id=session_id,
            started_at=started_at,
            model_name=model_name,
            transport_profile=transport_profile or self._config.transport.profile,
            upstream_command=self._config.upstream.command,
            notes=notes,
        ))

        event_logger = EventLogger(
            output_path=self._config.logging.output,
            format=self._config.logging.format,
            rotate_mb=self._config.logging.rotate_mb,
            storage=storage,
        )

        async with TransportManager(self._config) as transport:
            engine = InterceptEngine(
                config=self._config,
                transport=transport,
                logger=event_logger,
                session_id=session_id,
            )
            log.info(
                "mcp-relay v0.1.0 starting | session=%s model=%s",
                session_id, model_name or "unknown",
            )
            try:
                await engine.run_stdio()
            finally:
                storage.end_session(session_id, utc_now())
                event_logger.close()
                storage.close()
                log.info("mcp-relay session %s ended.", session_id)
