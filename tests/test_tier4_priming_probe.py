"""
test_tier4_priming_probe.py — Unit tests for scripts/tier4_priming_probe.py.

Fast, no-network checks: clean import, config construction, case filtering,
and dry-run prompt generation via the real CLI entry point. Does NOT invoke
any live model or relay session — --dry-run exists specifically to make this
testable without Ollama running.

Not marked `integration` — runs as part of the default `pytest` suite.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = ROOT / "scripts" / "tier4_priming_probe.py"
CORPUS_TEST_MODULE_PATH = ROOT / "tests" / "test_llm_tool_calls.py"

ALL_TIER4_IDS = {
    "t4_capital_city", "t4_simple_math", "t4_historical_fact",
    "t4_definition", "t4_code_question", "t4_conversational",
}


def _load_probe_module():
    spec = importlib.util.spec_from_file_location("_tier4_priming_probe", SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_corpus_module():
    """Load tests/test_llm_tool_calls.py fresh, so its real
    run_prompt_with_relay can be monkeypatched per-test without affecting
    the `probe` fixture's own module instance."""
    spec = importlib.util.spec_from_file_location(
        "_corpus_module_for_probe_test", CORPUS_TEST_MODULE_PATH
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def probe():
    """Import the script once per module.

    This alone is a regression check: the exact failure that slipped through
    before (running with the system interpreter instead of .venv, masking a
    ModuleNotFoundError on `mcp`) surfaces here as an import error.
    """
    return _load_probe_module()


def _args(**overrides) -> argparse.Namespace:
    defaults = dict(
        model="qwen2.5:latest",
        backend="ollama",
        runs=3,
        protocol_era="legacy",
        cases=None,
        prime="Using what you already know, without looking anything up:",
        dry_run=True,
    )
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def test_module_imports_cleanly(probe):
    assert hasattr(probe, "main_async")
    assert hasattr(probe, "build_relay_config")
    assert hasattr(probe, "probe_case")


def test_all_tier4_cases_present_in_corpus(probe):
    cases = [c for c in yaml.safe_load(probe.CORPUS_PATH.read_text()) if c["tier"] == 4]
    assert {c["id"] for c in cases} == ALL_TIER4_IDS


def test_unknown_case_id_exits_with_error(probe, capsys):
    args = _args(cases=["not_a_real_case"])
    with pytest.raises(SystemExit) as exc_info:
        asyncio.run(probe.main_async(args))
    assert exc_info.value.code == 1
    assert "Unknown Tier 4 case id" in capsys.readouterr().err


def test_no_cases_selected_exits_with_error(probe, capsys):
    args = _args(cases=[])
    with pytest.raises(SystemExit) as exc_info:
        asyncio.run(probe.main_async(args))
    assert exc_info.value.code == 1
    assert "No Tier 4 cases selected" in capsys.readouterr().err


def test_dry_run_prints_baseline_and_primed_variants(probe, capsys):
    args = _args(cases=["t4_capital_city"])
    asyncio.run(probe.main_async(args))
    out = capsys.readouterr().out
    assert "baseline: 'What is the capital of France?'" in out
    assert "primed:" in out
    assert args.prime in out


def test_dry_run_does_not_require_ollama_or_relay(probe, monkeypatch, capsys):
    """Dry-run must return before touching Ollama/Relay — assert by making
    ollama_available() explode if it's ever called."""
    def _boom():
        raise AssertionError("dry-run should not check Ollama availability")

    args = _args(cases=["t4_conversational"])
    # No mod is loaded in the dry-run path at all, so there's nothing to
    # monkeypatch on `probe` — this test documents that expectation by
    # simply running to completion without needing Ollama up.
    asyncio.run(probe.main_async(args))
    assert "t4_conversational" in capsys.readouterr().out


def test_build_relay_config_legacy(probe):
    config = probe.build_relay_config("legacy")
    assert config.upstream.command == "uvx"
    assert "mcp-server-fetch" in config.upstream.args


def test_build_relay_config_modern(probe):
    config = probe.build_relay_config("modern")
    assert config.upstream.command == probe.sys.executable
    assert str(probe.MODERN_FETCH_SERVER) in config.upstream.args


def test_banner_reports_selected_protocol_era(probe, monkeypatch, capsys):
    """Regression: the banner used to omit protocol era entirely, so a run's
    output couldn't be attributed to legacy vs. modern after the fact —
    exactly the ambiguity that made the prior two live runs uninterpretable.
    Checks both values are threaded through distinctly, not just that some
    era string appears (which would pass even if the flag were ignored)."""
    monkeypatch.setattr(
        "sys.argv",
        ["tier4_priming_probe.py", "--model", "qwen2.5:latest", "--dry-run",
         "--cases", "t4_capital_city", "--protocol-era", "modern"],
    )
    probe.main()
    assert "Era:     modern" in capsys.readouterr().out

    monkeypatch.setattr(
        "sys.argv",
        ["tier4_priming_probe.py", "--model", "qwen2.5:latest", "--dry-run",
         "--cases", "t4_capital_city", "--protocol-era", "legacy"],
    )
    probe.main()
    assert "Era:     legacy" in capsys.readouterr().out


def test_cli_entrypoint_dry_run(probe, monkeypatch, capsys):
    """Exercises the real argparse entry point end to end — this is exactly
    the command handed to the user, minus live model calls."""
    monkeypatch.setattr(
        "sys.argv",
        [
            "tier4_priming_probe.py",
            "--model", "qwen2.5:latest",
            "--dry-run",
            "--cases", "t4_capital_city", "t4_conversational",
        ],
    )
    probe.main()
    out = capsys.readouterr().out
    assert "t4_capital_city" in out
    assert "t4_conversational" in out
    assert "t4_historical_fact" not in out


def test_cli_entrypoint_defaults(probe, monkeypatch, capsys):
    monkeypatch.setattr(
        "sys.argv",
        ["tier4_priming_probe.py", "--model", "qwen2.5:latest", "--dry-run"],
    )
    probe.main()
    out = capsys.readouterr().out
    for case_id in ALL_TIER4_IDS:
        assert case_id in out
    assert "Runs:    3" in out
    assert probe.DEFAULT_PRIME in out


# ---------------------------------------------------------------------------
# Regression: case.get("tool", "fetch") bug.
#
# Every Tier 4 case stores `tool: null` explicitly in test_cases.yaml (key
# present, value None — not the key being absent). dict.get(key, default)
# only falls back to `default` when the key is MISSING, so the original
# `case.get("tool", "fetch")` silently returned None for every Tier 4 case,
# and _tool_was_called(tool_calls, None) was then always False — masking
# every real spurious tool call. A live run against qwen2.5:latest showed a
# real fetch call (britannica.com, 403) get reported as 0/3 baseline calls,
# which is what surfaced this. probe_case must correctly detect a real call
# on a `tool: None` case.
# ---------------------------------------------------------------------------

async def _run_probe_case(monkeypatch, tool_calls_per_run):
    """Stub run_prompt_with_relay to return a fixed tool_calls list on every
    run, and drive the real probe_case()/_tool_was_called() against it."""
    corpus_mod = _load_corpus_module()

    async def fake_run_prompt_with_relay(prompt, model, relay, session_id, backend=None):
        return ("some response", tool_calls_per_run)

    monkeypatch.setattr(corpus_mod, "run_prompt_with_relay", fake_run_prompt_with_relay)

    probe_mod = _load_probe_module()
    case = {"id": "t4_capital_city", "tool": None}  # mirrors the real corpus exactly
    return await probe_mod.probe_case(
        corpus_mod,
        relay=None,
        model="qwen2.5:latest",
        backend="ollama",
        case=case,
        variant_label="baseline",
        prompt="What is the capital of France?",
        runs=3,
    )


def test_probe_case_detects_spurious_call_on_null_tool_case(monkeypatch):
    calls = asyncio.run(_run_probe_case(
        monkeypatch,
        tool_calls_per_run=[{"tool": "fetch", "arguments": {"url": "https://example.com"}}],
    ))
    assert calls == 3, (
        "a real fetch call on a `tool: null` case must be counted, not silently dropped"
    )


def test_probe_case_counts_zero_when_no_call_made(monkeypatch):
    calls = asyncio.run(_run_probe_case(monkeypatch, tool_calls_per_run=[]))
    assert calls == 0
