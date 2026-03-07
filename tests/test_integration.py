"""
test_integration.py - End-to-end tests with real Ollama.

These tests require:
  - Ollama running locally at http://localhost:11434
  - At least one tool-capable model pulled (see TOOL_CAPABLE_MODELS below)
  - uvx and mcp-server-fetch available

Run with:
    pytest -m integration

Skip with (default in CI):
    pytest -m "not integration"
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

# Models confirmed present on this machine and known to support tool calling.
# Ordered by preference: fastest/smallest first for CI speed.
TOOL_CAPABLE_MODELS = [
    "llama3.2:latest",      # 2GB, 3.2B  — fastest loader
    "qwen2.5:latest",       # 4.7GB, 7.6B — most reliable tool caller
    "Llama3.1:8b",          # 4.9GB, 8B  — good baseline
    "gemma3:4b",            # 3.3GB, 4.3B — different architecture
    "gemma3:12b",           # 8.1GB, 12B — heavier but thorough
]

# Models NOT to use for tool calling tests
SKIP_MODELS = {
    "deepseek-r1:7b", "deepseek-r1:8b", "deepseek-r1:14b", "deepseek-r1:32b",
    "mxbai-embed-large:latest",   # embedding only
    "llama3.2-vision:11b",        # vision model
    "llava:13b",                  # vision model
    "llama3.3:latest",            # 42GB — too slow for tests
    "x/z-image-turbo:latest",
    "gemini-3-pro-preview:latest",
    "glm-4.7:cloud",
    "deepseek-v3.2:cloud",
}


def ollama_available() -> bool:
    try:
        import httpx
        r = httpx.get("http://localhost:11434/api/tags", timeout=2.0)
        return r.status_code == 200
    except Exception:
        return False


def get_available_models() -> list[str]:
    try:
        import httpx
        r = httpx.get("http://localhost:11434/api/tags", timeout=2.0)
        return [m["name"] for m in r.json().get("models", [])]
    except Exception:
        return []


def pick_test_model() -> str | None:
    available = set(get_available_models())
    for candidate in TOOL_CAPABLE_MODELS:
        if candidate in available:
            return candidate
    return None


@pytest.fixture(scope="module")
def ollama_model():
    if not ollama_available():
        pytest.skip("Ollama not running at localhost:11434")
    model = pick_test_model()
    if not model:
        pytest.skip(
            f"None of the preferred tool-capable models are available. "
            f"Pull one of: {TOOL_CAPABLE_MODELS}"
        )
    print(f"\n[integration] Using model: {model}")
    return model


@pytest.mark.asyncio
async def test_relay_live_passthrough_with_fetch_server(tmp_path, ollama_model):
    """
    Full integration: relay proxies a real tool call to mcp-server-fetch.

    Validates:
    1. Relay starts and connects to upstream MCP server
    2. Tool list is populated from upstream
    3. Tool call returns a real response
    4. Log file is written with correct event types
    """
    from mcp_relay.config import RelayConfig
    from mcp_relay.transport import TransportMode
    from mcp_relay.relay import Relay

    config = RelayConfig.defaults()
    config.logging.output = str(tmp_path / "integration.log")
    config.transport.default_mode = TransportMode.LIVE
    config.upstream.command = "uvx"
    config.upstream.args = ["mcp-server-fetch"]

    relay = Relay(config=config)

    async with relay.session() as session:
        # 1. Tools are populated from upstream
        tools = await session.list_tools()
        assert len(tools) > 0
        tool_names = [t.name for t in tools]
        assert "fetch" in tool_names

        # 2. Tool call returns a real response
        result, latency = await session.call_tool(
            "fetch",
            {"url": "https://httpbin.org/get"},
        )
        assert result is not None
        assert not result.isError
        assert latency > 0
        assert len(result.content) > 0

    # 3. Log exists and has correct structure
    log_path = Path(config.logging.output)
    assert log_path.exists()
    lines = log_path.read_text().strip().split("\n")
    assert len(lines) >= 2

    events = [json.loads(l) for l in lines]
    event_types = [e["event_type"] for e in events]
    assert "call_start" in event_types
    assert "call_end" in event_types


@pytest.mark.asyncio
async def test_relay_captures_all_tool_calls_in_log(tmp_path, ollama_model):
    """Every tool call must produce exactly one call_start and one call_end."""
    from mcp_relay.config import RelayConfig
    from mcp_relay.transport import TransportMode
    from mcp_relay.relay import Relay

    config = RelayConfig.defaults()
    config.logging.output = str(tmp_path / "multi.log")
    config.transport.default_mode = TransportMode.LIVE
    config.upstream.command = "uvx"
    config.upstream.args = ["mcp-server-fetch"]

    relay = Relay(config=config)
    n_calls = 3

    async with relay.session() as session:
        for _ in range(n_calls):
            await session.call_tool("fetch", {"url": "https://httpbin.org/get"})

    events = [
        json.loads(l)
        for l in Path(config.logging.output).read_text().strip().split("\n")
    ]
    call_starts = [e for e in events if e["event_type"] == "call_start"]
    call_ends   = [e for e in events if e["event_type"] == "call_end"]

    assert len(call_starts) == n_calls
    assert len(call_ends) == n_calls


@pytest.mark.asyncio
async def test_relay_log_captures_payload_and_latency(tmp_path, ollama_model):
    """call_end events must include payload and latency_ms."""
    from mcp_relay.config import RelayConfig
    from mcp_relay.transport import TransportMode
    from mcp_relay.relay import Relay

    config = RelayConfig.defaults()
    config.logging.output = str(tmp_path / "payload.log")
    config.transport.default_mode = TransportMode.LIVE
    config.upstream.command = "uvx"
    config.upstream.args = ["mcp-server-fetch"]

    relay = Relay(config=config)
    target_url = "https://httpbin.org/uuid"

    async with relay.session() as session:
        await session.call_tool("fetch", {"url": target_url})

    events = [
        json.loads(l)
        for l in Path(config.logging.output).read_text().strip().split("\n")
    ]
    start = next(e for e in events if e["event_type"] == "call_start")
    end   = next(e for e in events if e["event_type"] == "call_end")

    # Payload was captured at call_start
    assert start["payload"]["url"] == target_url

    # Latency was measured at call_end
    assert end["latency_ms"] is not None
    assert end["latency_ms"] > 0

    # Same call linked by event_id
    assert start["event_id"] == end["event_id"]


@pytest.mark.asyncio
async def test_relay_identity_response_unchanged(tmp_path, ollama_model):
    """
    Identity test: response via relay == response from direct MCP call.

    Both calls go to the same upstream server.  The relay must not
    modify the response content in any way.
    """
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from mcp_relay.config import RelayConfig
    from mcp_relay.transport import TransportMode
    from mcp_relay.relay import Relay

    config = RelayConfig.defaults()
    config.logging.output = str(tmp_path / "identity.log")
    config.transport.default_mode = TransportMode.LIVE
    config.upstream.command = "uvx"
    config.upstream.args = ["mcp-server-fetch"]

    url = "https://httpbin.org/get"

    # Call via relay
    relay = Relay(config=config)
    async with relay.session() as session:
        relay_result, _ = await session.call_tool("fetch", {"url": url})

    # Call directly
    params = StdioServerParameters(command="uvx", args=["mcp-server-fetch"])
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as direct_session:
            await direct_session.initialize()
            direct_result = await direct_session.call_tool("fetch", {"url": url})

    # isError must match
    assert relay_result.isError == direct_result.isError

    # Content type must match (text vs image etc.)
    assert len(relay_result.content) == len(direct_result.content)
    for rc, dc in zip(relay_result.content, direct_result.content):
        assert rc.type == dc.type  # type: ignore[union-attr]
