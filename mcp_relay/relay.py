"""
mcp_relay.relay - Top-level Relay class.
"""

from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator

from mcp.types import CallToolResult, Tool

from mcp_relay.config import RelayConfig
from mcp_relay.core.intercept import InterceptEngine
from mcp_relay.core.logging import EventLogger, utc_now
from mcp_relay.policy.engine import PolicyConfig, PolicyEngine, PolicyViolationError
from mcp_relay.storage import SQLiteStorage
from mcp_relay.storage.base import SessionRecord
from mcp_relay.transport.manager import TransportManager

log = logging.getLogger("mcp_relay")


class RelaySession:
    """
    A live relay session — transport connected, logger open, storage active.
    All tool calls are routed through InterceptEngine so every call is
    logged and stored regardless of usage pattern.
    """

    def __init__(
        self,
        engine: InterceptEngine,
        transport: TransportManager,
        logger: EventLogger,
        storage: SQLiteStorage,
        session_id: str,
        model_name: str | None,
    ) -> None:
        self._engine = engine
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
        # Route through InterceptEngine — policy check + logs call_start + call_end/blocked/error
        return await self._engine._intercept_call(tool_name, arguments)


def _build_policy_engine(config: RelayConfig) -> PolicyEngine:
    """Construct a PolicyEngine from the relay config's policy section."""
    p = config.policy
    policy_cfg = PolicyConfig(
        enabled=p.enabled,
        dry_run=p.dry_run,
        ssrf_protection=p.ssrf_protection,
        url_allowlist=p.url_allowlist,
        url_blocklist=p.url_blocklist,
        extra_blocked_hosts=p.extra_blocked_hosts,
    )
    return PolicyEngine.from_config(policy_cfg)


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

        policy = _build_policy_engine(self._config)

        async with TransportManager(self._config) as transport:
            engine = InterceptEngine(
                config=self._config,
                transport=transport,
                logger=event_logger,
                session_id=session_id,
                policy=policy,
            )
            try:
                yield RelaySession(
                    engine=engine,
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

        policy = _build_policy_engine(self._config)

        async with TransportManager(self._config) as transport:
            engine = InterceptEngine(
                config=self._config,
                transport=transport,
                logger=event_logger,
                session_id=session_id,
                policy=policy,
            )
            log.info(
                "mcp-relay v0.2.0 starting | session=%s model=%s policy=%s",
                session_id, model_name or "unknown",
                "enabled" if self._config.policy.enabled else "disabled",
            )
            try:
                await engine.run_stdio()
            finally:
                storage.end_session(session_id, utc_now())
                event_logger.close()
                storage.close()
                log.info("mcp-relay session %s ended.", session_id)
