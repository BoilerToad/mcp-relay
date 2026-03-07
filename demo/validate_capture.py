"""
validate_capture.py - Fire real tool calls through the relay and
query SQLite to confirm meaningful data is being captured.

Run from project root:
    source .venv/bin/activate
    python demo/validate_capture.py
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from mcp_relay.config import RelayConfig
from mcp_relay.relay import Relay
from mcp_relay.storage.sqlite import SQLiteStorage
from mcp_relay.transport import TransportMode

DB_PATH  = Path("~/.mcp-relay/validate.db").expanduser()
LOG_PATH = Path("~/.mcp-relay/validate.log").expanduser()

CALLS = [
    ("fetch", {"url": "https://httpbin.org/json"}),
    ("fetch", {"url": "https://httpbin.org/uuid"}),
    ("fetch", {"url": "https://httpbin.org/get"}),
]

async def run() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    config = RelayConfig.defaults()
    config.logging.output   = str(LOG_PATH)
    config.storage.path     = str(DB_PATH)
    config.transport.default_mode = TransportMode.LIVE
    config.upstream.command = "uvx"
    config.upstream.args    = ["mcp-server-fetch"]

    relay = Relay(config=config)

    print("=" * 60)
    print("mcp-relay capture validation")
    print("=" * 60)

    async with relay.session(
        model_name="validate-script",
        transport_profile="clean",
        notes="manual validation run",
    ) as session:
        tools = await session.list_tools()
        print(f"\n✓ Upstream tools available: {[t.name for t in tools]}")

        for tool_name, args in CALLS:
            result, latency = await session.call_tool(tool_name, args)
            status = "ERROR" if result.isError else "OK"
            print(f"  [{status}] {tool_name}({args['url']})  {latency:.1f}ms")

    print("\n--- SQLite Validation ---")
    storage = SQLiteStorage(DB_PATH)
    storage.initialize()

    # Sessions
    sessions = storage.list_sessions()
    print(f"\nSessions captured: {len(sessions)}")
    for s in sessions:
        print(f"  session_id : {s.session_id}")
        print(f"  model      : {s.model_name}")
        print(f"  profile    : {s.transport_profile}")
        print(f"  started    : {s.started_at}")
        print(f"  ended      : {s.ended_at}")
        print(f"  notes      : {s.notes}")

        # Events for this session
        events = storage.get_events(s.session_id)
        print(f"\n  Events ({len(events)} total):")
        for e in events:
            lat = f"{e.latency_ms:.1f}ms" if e.latency_ms else "-"
            err = f"  ERROR: {e.error}" if e.error else ""
            print(f"    [{e.event_type:<12}] {e.tool_name:<10} {lat}{err}")

    # Research queries
    print("\n--- Research Queries ---")

    stats = storage.latency_stats()
    print(f"\nLatency stats ({len(stats)} model(s)):")
    for r in stats:
        print(f"  model       : {r['model_name']}")
        print(f"  total_calls : {r['total_calls']}")
        print(f"  avg_ms      : {r['avg_latency_ms']}")
        print(f"  min_ms      : {r['min_latency_ms']}")
        print(f"  max_ms      : {r['max_latency_ms']}")
        print(f"  stddev_ms   : {r['stddev_latency_ms']}")

    counts = storage.call_counts()
    print(f"\nCall counts:")
    for r in counts:
        print(f"  {r['model_name']:<25} {r['event_type']:<15} {r['count']}")

    rates = storage.error_rates()
    print(f"\nError rates:")
    for r in rates:
        print(f"  {r['model_name']:<25} errors={r['errors']}/{r['total']}  ({r['error_pct']}%)")

    # JSONL log
    print(f"\n--- JSONL Log ({LOG_PATH}) ---")
    if LOG_PATH.exists():
        lines = LOG_PATH.read_text().strip().split("\n")
        print(f"  {len(lines)} lines written")
        print(f"\n  First event (pretty):")
        first = json.loads(lines[0])
        for k, v in first.items():
            if v is not None and v != {} and v != []:
                print(f"    {k:<20} {v}")
    else:
        print("  (log file not found)")

    storage.close()
    print("\n✓ Validation complete.")

if __name__ == "__main__":
    asyncio.run(run())
