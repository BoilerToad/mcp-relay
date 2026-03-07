"""
demo/run_demo.py - mcp-relay capability showcase.

Runs a mock LLM through the relay with mcp-server-fetch as the upstream.
Demonstrates:
  1. Clean pass-through with full logging
  2. Burst call pattern detection
  3. Log file inspection

Usage:
    cd /Users/toddfirsich/AI-Development/mcp-relay
    pip install -e ".[dev]"
    python demo/run_demo.py

Requires:
    uvx and mcp-server-fetch available (pip install uvx)
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

# Allow running from the project root
sys.path.insert(0, str(Path(__file__).parent.parent))

from mcp_relay.config import RelayConfig
from mcp_relay.relay import Relay
from mcp_relay.transport import TransportMode
from demo.mock_llm import MockLLM


LOG_PATH = Path("/tmp/mcp-relay-demo.log")


def print_header(title: str) -> None:
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


def print_log_summary(log_path: Path) -> None:
    if not log_path.exists():
        print("  (no log file found)")
        return
    lines = log_path.read_text().strip().split("\n")
    events = [json.loads(l) for l in lines if l.strip()]
    starts  = [e for e in events if e["event_type"] == "call_start"]
    ends    = [e for e in events if e["event_type"] == "call_end"]
    errors  = [e for e in events if e["event_type"] == "call_error"]
    latencies = [e["latency_ms"] for e in ends if e.get("latency_ms")]

    print(f"  Log path     : {log_path}")
    print(f"  Total events : {len(events)}")
    print(f"  call_start   : {len(starts)}")
    print(f"  call_end     : {len(ends)}")
    print(f"  call_error   : {len(errors)}")
    if latencies:
        print(f"  Avg latency  : {sum(latencies)/len(latencies):.1f}ms")
        print(f"  Max latency  : {max(latencies):.1f}ms")
    print(f"\n  Last 3 events:")
    for e in events[-3:]:
        print(f"    [{e['event_type']}] {e['tool_name']} "
              f"lat={e.get('latency_ms', '-')}ms")


async def run_demo() -> None:
    print_header("mcp-relay Demo")
    print("  Upstream: uvx mcp-server-fetch")
    print(f"  Log:      {LOG_PATH}")

    config = RelayConfig.defaults()
    config.logging.output = str(LOG_PATH)
    config.log_level = "DEBUG"
    config.transport.default_mode = TransportMode.LIVE
    config.upstream.command = "uvx"
    config.upstream.args = ["mcp-server-fetch"]

    relay = Relay(config=config)

    # --- Demo 1: Normal call pattern ---
    print_header("Demo 1: Normal Call Pattern")
    async with relay.session() as session:
        tools = await session.list_tools()
        print(f"  Upstream tools: {[t.name for t in tools]}")
        llm = MockLLM(session, model_name="mock-qwen2.5")
        await llm.run_normal(n_calls=3)
        llm.summary()

    # --- Demo 2: Burst call pattern ---
    print_header("Demo 2: Burst Call Pattern")
    async with relay.session() as session:
        llm = MockLLM(session, model_name="mock-llama3.1")
        await llm.run_burst(n_calls=6)
        llm.summary()

    # --- Log summary ---
    print_header("Log Summary")
    print_log_summary(LOG_PATH)
    print(f"\n  Full log: {LOG_PATH}")
    print("  View with: cat /tmp/mcp-relay-demo.log | python -m json.tool --no-ensure-ascii")


if __name__ == "__main__":
    asyncio.run(run_demo())
