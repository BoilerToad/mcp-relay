"""
Simple integration test: run a MockLLM through a programmatic Relay
and assert the relay recorded the external `fetch` calls in storage.

This test intentionally makes real HTTP requests (httpbin.org) so mark
it as integration if you want to gate it behind network availability.
"""

from __future__ import annotations

import pytest

from demo.mock_llm import MockLLM

from mcp_relay.config import RelayConfig
from mcp_relay.relay import Relay
from mcp_relay.transport import TransportMode


@pytest.mark.asyncio
async def test_mockllm_fetch_calls_are_logged(tmp_path):
    """Create a Relay using a temporary SQLite DB, run MockLLM burst,
    and assert the storage contains `fetch` call events for the session.
    """
    db_path = tmp_path / "relay_test.db"
    log_path = tmp_path / "relay_test.log"

    cfg = RelayConfig.defaults()
    cfg.storage.path = str(db_path)
    cfg.logging.output = str(log_path)
    cfg.logging.rotate_mb = 0
    # Use OFFLINE mode for tests so we don't need a real upstream MCP server.
    cfg.transport.default_mode = TransportMode.OFFLINE

    relay = Relay(config=cfg)

    async with relay.session(model_name="mock-llm") as session:
        mock = MockLLM(session)
        # burst pattern triggers multiple rapid external calls
        await mock.run_burst(n_calls=3)

        # Inspect stored events for this session
        events = session._storage.get_events(session.session_id)

        assert len(events) >= 1, "Expected at least one logged event"
        assert any(e.tool_name == "fetch" for e in events), (
            "Expected at least one 'fetch' tool event recorded in storage"
        )
        assert any(e.event_type in ("call_end", "call_start") for e in events)
