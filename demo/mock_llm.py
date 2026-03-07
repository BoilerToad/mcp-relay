"""
demo/mock_llm.py - Simulates an LLM client making tool calls through mcp-relay.

Useful for testing the relay without needing Ollama running.
Mimics the call patterns a real Ollama model would make.
"""

from __future__ import annotations

import asyncio
import random
from typing import Any

from mcp_relay.relay import Relay, RelaySession


class MockLLM:
    """
    Simulates a local LLM making tool calls via a relay session.

    Supports different behavioral patterns to exercise relay detection:
      - normal:  spaced calls at a realistic cadence
      - burst:   rapid-fire calls to trigger burst detection
      - repeat:  same destination called repeatedly
    """

    def __init__(self, session: RelaySession, model_name: str = "mock-llm") -> None:
        self._session = session
        self.model_name = model_name
        self.calls_made: list[dict[str, Any]] = []

    async def run_normal(self, n_calls: int = 5) -> None:
        """Normal usage: spaced calls to different tools."""
        print(f"[{self.model_name}] Starting normal call pattern ({n_calls} calls)")
        urls = [
            "https://httpbin.org/get",
            "https://httpbin.org/ip",
            "https://httpbin.org/uuid",
            "https://httpbin.org/headers",
            "https://httpbin.org/user-agent",
        ]
        for i in range(n_calls):
            url = urls[i % len(urls)]
            print(f"[{self.model_name}] Calling fetch({url})")
            try:
                result, latency = await self._session.call_tool("fetch", {"url": url})
                self.calls_made.append({"url": url, "latency_ms": latency, "error": False})
                print(f"[{self.model_name}]   → {latency:.1f}ms OK")
            except Exception as e:
                self.calls_made.append({"url": url, "latency_ms": 0, "error": str(e)})
                print(f"[{self.model_name}]   → ERROR: {e}")
            await asyncio.sleep(random.uniform(0.5, 1.5))

    async def run_burst(self, n_calls: int = 10) -> None:
        """Burst pattern: rapid calls to the same URL — should trigger burst detection."""
        url = "https://httpbin.org/get"
        print(f"[{self.model_name}] Starting BURST pattern ({n_calls} calls to {url})")
        tasks = [
            self._session.call_tool("fetch", {"url": url})
            for _ in range(n_calls)
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for r in results:
            if isinstance(r, Exception):
                self.calls_made.append({"url": url, "error": str(r)})
            else:
                result, latency = r
                self.calls_made.append({"url": url, "latency_ms": latency, "error": False})

    def summary(self) -> None:
        total = len(self.calls_made)
        errors = sum(1 for c in self.calls_made if c.get("error"))
        avg_latency = (
            sum(c.get("latency_ms", 0) for c in self.calls_made if not c.get("error"))
            / max(total - errors, 1)
        )
        print(f"\n[{self.model_name}] Summary:")
        print(f"  Total calls : {total}")
        print(f"  Errors      : {errors}")
        print(f"  Avg latency : {avg_latency:.1f}ms")
