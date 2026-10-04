#!/usr/bin/env python3
"""
tier4_priming_probe.py — Side probe: does an explicit "use what you know"
instruction reduce spurious tool calls on Tier 4 (no-call discipline) cases?

NOT part of the regression sweep or the pytest corpus. Ad hoc, throwaway
experiment — results print to the terminal only. Nothing is written to the
research.db that the real corpus runs use, and test_cases.yaml is untouched.

Reuses the existing test harness (chat_completion, run_prompt_with_relay,
etc.) from tests/test_llm_tool_calls.py by importing it directly, rather
than duplicating that logic here.

Usage:
    python scripts/tier4_priming_probe.py --model qwen2.5:latest --runs 3 \\
        --cases t4_capital_city t4_historical_fact t4_definition t4_conversational

    # All Tier 4 cases, custom priming text:
    python scripts/tier4_priming_probe.py --model qwen2.5:latest --runs 3 \\
        --prime "Using only your own knowledge, with no lookups:"

    # Review the two prompt variants per case without spending any model calls:
    python scripts/tier4_priming_probe.py --model qwen2.5:latest --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import os
import sys
from pathlib import Path

import yaml

from mcp_relay.config import RelayConfig
from mcp_relay.relay import Relay
from mcp_relay.transport import TransportMode

ROOT = Path(__file__).resolve().parent.parent
CORPUS_PATH = ROOT / "tests" / "fixtures" / "test_cases.yaml"
MODERN_FETCH_SERVER = ROOT / "mock_servers" / "modern_fetch_server.py"
DEFAULT_LOG = Path("~/.mcp-relay/research.log").expanduser()
DEFAULT_DB = Path("~/.mcp-relay/research.db").expanduser()

DEFAULT_PRIME = "Using what you already know, without looking anything up:"


def _load_test_module():
    """Import tests/test_llm_tool_calls.py by path so this script can reuse
    its harness functions (chat_completion, run_prompt_with_relay, ...)
    instead of duplicating them."""
    spec = importlib.util.spec_from_file_location(
        "_corpus_test_module", ROOT / "tests" / "test_llm_tool_calls.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def build_relay_config(protocol_era: str) -> RelayConfig:
    """Mirrors the relay_config fixture in test_llm_tool_calls.py, minus the
    pytest request object (this script takes plain argparse args instead)."""
    DEFAULT_LOG.parent.mkdir(parents=True, exist_ok=True)
    config = RelayConfig.defaults()
    config.logging.output = str(DEFAULT_LOG)
    config.storage.path = str(DEFAULT_DB)
    config.transport.default_mode = TransportMode.LIVE

    if protocol_era == "modern":
        config.upstream.command = sys.executable
        config.upstream.args = [str(MODERN_FETCH_SERVER)]
    else:
        proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")
        config.upstream.command = "uvx"
        base_args = ["--with", "mcp<2.0", "mcp-server-fetch"]
        config.upstream.args = base_args + ["--proxy", proxy] if proxy else base_args

    config.upstream.env = dict(os.environ)
    return config


async def probe_case(mod, relay, model, backend, case, variant_label, prompt, runs) -> int:
    """Run one prompt variant `runs` times, return how many runs made a tool call."""
    calls = 0
    for i in range(runs):
        session_id = f"probe::{variant_label}::{case['id']}::{i}"
        _, tool_calls = await mod.run_prompt_with_relay(
            prompt=prompt, model=model, relay=relay, session_id=session_id, backend=backend,
        )
        # Tier 4 cases store `tool: null` explicitly (expect_call: false), so
        # dict.get(..., "fetch") never falls back — the key is present, just
        # None. Use `or` to actually fall back when the value is falsy.
        if mod._tool_was_called(tool_calls, case.get("tool") or "fetch"):
            calls += 1
    return calls


async def main_async(args: argparse.Namespace) -> None:
    cases = [c for c in yaml.safe_load(CORPUS_PATH.read_text()) if c["tier"] == 4]
    if args.cases is not None:
        wanted = set(args.cases)
        cases = [c for c in cases if c["id"] in wanted]
        missing = wanted - {c["id"] for c in cases}
        if missing:
            print(
                f"[error] Unknown Tier 4 case id(s): {', '.join(sorted(missing))}",
                file=sys.stderr,
            )
            sys.exit(1)
    if not cases:
        print("[error] No Tier 4 cases selected.", file=sys.stderr)
        sys.exit(1)

    print(f"\n{'='*70}")
    print("  Tier 4 priming probe — side test, not part of the regression sweep")
    print(f"  Model:   {args.model} ({args.backend})")
    print(f"  Runs:    {args.runs} per case per variant")
    print(f"  Era:     {args.protocol_era}")
    print(f"  Prime:   \"{args.prime}\"")
    print(f"  Cases:   {', '.join(c['id'] for c in cases)}")
    print(f"{'='*70}\n")

    if args.dry_run:
        for case in cases:
            print(f"[{case['id']}]")
            print(f"  baseline: {case['prompt']!r}")
            print(f"  primed:   {args.prime + ' ' + case['prompt']!r}\n")
        return

    mod = _load_test_module()

    if args.backend == "ollama" and not mod.ollama_available():
        print("[error] Ollama not running at localhost:11434", file=sys.stderr)
        sys.exit(1)
    if not mod.model_supports_tools(args.model, backend=args.backend):
        print(
            f"[error] Model '{args.model}' does not support tool calling via {args.backend}.",
            file=sys.stderr,
        )
        sys.exit(1)

    config = build_relay_config(args.protocol_era)
    relay = Relay(config=config)

    rows = []
    for case in cases:
        baseline_calls = await probe_case(
            mod, relay, args.model, args.backend, case, "baseline", case["prompt"], args.runs
        )
        primed_prompt = f"{args.prime} {case['prompt']}"
        primed_calls = await probe_case(
            mod, relay, args.model, args.backend, case, "primed", primed_prompt, args.runs
        )
        rows.append((case["id"], baseline_calls, primed_calls))
        print(
            f"  {case['id']:<28} baseline: {baseline_calls}/{args.runs} calls"
            f"   primed: {primed_calls}/{args.runs} calls"
        )

    total_baseline = sum(r[1] for r in rows)
    total_primed = sum(r[2] for r in rows)
    total_possible = len(rows) * args.runs
    print(f"\n{'='*70}")
    print(
        f"  Total spurious calls — baseline: {total_baseline}/{total_possible}"
        f"   primed: {total_primed}/{total_possible}"
    )
    print(f"{'='*70}\n")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Side probe: does a 'use what you know' prefix reduce spurious "
                     "Tier 4 tool calls? Not part of the regression sweep.",
    )
    parser.add_argument("--model", required=True, metavar="MODEL")
    parser.add_argument("--backend", default="ollama", help="Inference backend (default: ollama)")
    parser.add_argument(
        "--runs", type=int, default=3,
        help="Runs per case per variant (default: 3)",
    )
    parser.add_argument(
        "--protocol-era", default="legacy", choices=["legacy", "modern"],
        help="Upstream fetch server protocol era (default: legacy)",
    )
    parser.add_argument(
        "--cases", nargs="+", default=None, metavar="CASE_ID",
        help="Restrict to specific Tier 4 case IDs (default: all Tier 4 cases)",
    )
    parser.add_argument(
        "--prime", default=DEFAULT_PRIME, metavar="TEXT",
        help=f"Priming text prepended to the prompt (default: {DEFAULT_PRIME!r})",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print the baseline/primed prompt pairs without calling any model",
    )
    args = parser.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
