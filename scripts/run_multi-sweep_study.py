#!/usr/bin/env python3
"""
run_multi-sweep_study.py — Repeated-run sweep + per-case summarization.

Single-repo tool: runs one model's test_llm_tool_calls.py corpus N times in
a row and summarizes PASS/FAIL *per case* across the sweep — e.g. "t4_
historical_fact failed 2/3 runs". scripts/run_study.py already does
multi-run, multi-model studies, but only reports aggregate passed/failed
counts per run; it can't tell you which specific case is flaky vs.
consistently broken. This fills that gap.

Deliberately scoped to whichever repo it's copied into — it does not know
about or reach into any other checkout. To compare two repos (e.g.
mcp-relay vs. mcp-relay-v2), run this script in each one separately and
diff the two summaries by hand or with another tool.

Uses `pytest --junitxml` for structured per-testcase results rather than
scraping terminal output — robust to formatting changes and avoids the
double-invocation (stream + re-capture) that run_study.py needs.

Usage:
    python scripts/run_multi-sweep_study.py --model qwen2.5:latest --tiers 4 --runs 3
    python scripts/run_multi-sweep_study.py --model llama3.2:latest --runs 5
    python scripts/run_multi-sweep_study.py --model qwen2.5:latest --tiers 4 5 --runs 3 \
        --report sweep-report.md
    python scripts/run_multi-sweep_study.py --model qwen2.5:latest --tiers 4 --runs 3 --dry-run
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Resolve project root (script lives in <root>/scripts/)
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MODELS_FILE = ROOT / "studies" / "study_models.json"
DEFAULT_DB = str(Path("~/.mcp-relay/research.db").expanduser())

CASE_ID_RE = re.compile(r"\[(.+)\]$")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_models(path: Path) -> list[dict]:
    return json.loads(path.read_text())["models"]


def _local_get(url: str, timeout: float = 2.0):
    """httpx GET that bypasses any system proxy (mitmproxy, Charles, etc.)."""
    import httpx
    with httpx.Client(trust_env=False) as client:
        return client.get(url, timeout=timeout)


def ollama_available() -> bool:
    try:
        return _local_get("http://localhost:11434/api/tags").status_code == 200
    except Exception:
        return False


def mlx_available() -> bool:
    try:
        return _local_get("http://localhost:8080/v1/models").status_code == 200
    except Exception:
        return False


def get_available_ollama_models() -> set[str]:
    try:
        r = _local_get("http://localhost:11434/api/tags")
        return {m["name"] for m in r.json().get("models", [])}
    except Exception:
        return set()


def build_pytest_cmd(
    model: str,
    backend: str,
    db_path: str,
    tiers: list[int] | None,
    junit_path: Path,
    extra_args: list[str],
) -> list[str]:
    cmd = [
        sys.executable, "-m", "pytest",
        "tests/test_llm_tool_calls.py",
        "-m", "integration",
        "-v",
        f"--model={model}",
        f"--backend={backend}",
        f"--db={db_path}",
        f"--junitxml={junit_path}",
    ]
    if tiers:
        k_expr = " or ".join(f"tier{t}" for t in tiers)
        cmd += ["-k", k_expr]
    cmd += extra_args
    return cmd


def fmt_duration(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    return f"{m}m{s:02d}s" if m else f"{s}s"


def parse_junit_results(xml_path: Path) -> dict[str, str]:
    """Parse a pytest --junitxml file into {case_id: outcome}.

    outcome is one of: passed, failed, xfailed, skipped, error.
    case_id is the parametrize bracket content (e.g. "t4_capital_city"),
    or the bare test name if not parametrized.
    """
    results: dict[str, str] = {}
    if not xml_path.exists():
        return results

    tree = ET.parse(xml_path)
    for testcase in tree.getroot().iter("testcase"):
        name = testcase.get("name", "")
        match = CASE_ID_RE.search(name)
        case_id = match.group(1) if match else name

        if testcase.find("failure") is not None or testcase.find("error") is not None:
            outcome = "failed"
        else:
            skipped = testcase.find("skipped")
            if skipped is not None:
                skip_type = (skipped.get("type") or "").lower()
                outcome = "xfailed" if "xfail" in skip_type else "skipped"
            else:
                outcome = "passed"
        results[case_id] = outcome

    return results


# ---------------------------------------------------------------------------
# Summarization
# ---------------------------------------------------------------------------

def summarize(
    model: str,
    runs: int,
    per_run_results: list[dict[str, str]],
) -> str:
    """Build a markdown table: case_id vs. per-run outcome + fail rate."""
    case_ids: list[str] = []
    seen = set()
    for run_results in per_run_results:
        for case_id in run_results:
            if case_id not in seen:
                seen.add(case_id)
                case_ids.append(case_id)

    lines = [f"### {model} — {runs} runs\n"]
    header = ["Case"] + [f"Run {i+1}" for i in range(runs)] + ["Fail rate"]
    lines.append("| " + " | ".join(header) + " |")
    lines.append("|" + "---|" * len(header))

    total_failed = total_xfailed = total_passed = total_other = 0

    for case_id in case_ids:
        row = [case_id]
        failed = xfailed = passed = other = 0
        for run_results in per_run_results:
            outcome = run_results.get(case_id, "missing")
            symbol = {
                "passed": "PASS",
                "failed": "**FAIL**",
                "xfailed": "xfail",
                "skipped": "skip",
                "error": "**ERROR**",
                "missing": "?",
            }.get(outcome, outcome)
            row.append(symbol)
            if outcome == "failed" or outcome == "error":
                failed += 1
            elif outcome == "xfailed":
                xfailed += 1
            elif outcome == "passed":
                passed += 1
            else:
                other += 1
        row.append(f"{failed}/{runs}")
        lines.append("| " + " | ".join(row) + " |")
        total_failed += failed
        total_xfailed += xfailed
        total_passed += passed
        total_other += other

    total_runs = len(case_ids) * runs
    lines.append("")
    lines.append(
        f"**Total: {total_failed}/{total_runs} failed** "
        f"({total_passed} passed, {total_xfailed} xfailed, {total_other} other)"
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Repeated-run sweep + per-case summarization for one repo's LLM tool-call corpus"
        ),
    )
    parser.add_argument(
        "--model", nargs="+", required=True, metavar="MODEL",
        help="One or more model names to sweep (must exist and be enabled in study_models.json)"
    )
    parser.add_argument(
        "--backend", default="ollama",
        help="Inference backend (default: ollama)"
    )
    parser.add_argument(
        "--tiers", nargs="+", type=int, default=None,
        help="Restrict to specific tiers (e.g. --tiers 4), default: all tiers"
    )
    parser.add_argument(
        "--runs", type=int, default=3,
        help="Number of repeated runs per model (default: 3)"
    )
    parser.add_argument(
        "--db", default=DEFAULT_DB,
        help=f"SQLite DB path for relay event storage (default: {DEFAULT_DB})"
    )
    parser.add_argument(
        "--report", default=None, metavar="PATH",
        help="Also write the markdown summary to this file"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print commands without executing"
    )
    args = parser.parse_args()

    if DEFAULT_MODELS_FILE.exists():
        all_models = load_models(DEFAULT_MODELS_FILE)
        enabled_by_name = {
            m["name"]: m.get("backend", "ollama")
            for m in all_models
            if m.get("enabled", True)
        }
        bad = [name for name in args.model if name not in enabled_by_name]
        if bad:
            print(
                f"[error] Model(s) not found or not enabled in {DEFAULT_MODELS_FILE}:",
                file=sys.stderr,
            )
            for name in bad:
                print(f"  - {name}", file=sys.stderr)
            sys.exit(1)

    print(f"\n{'='*60}")
    print("  mcp-relay multi-sweep study")
    print(f"  Models:   {', '.join(args.model)}")
    print(f"  Backend:  {args.backend}")
    print(f"  Tiers:    {args.tiers or 'all'}")
    print(f"  Runs:     {args.runs}")
    print(f"  DB:       {args.db}")
    print(f"  Started:  {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'='*60}\n")

    if not args.dry_run:
        if args.backend == "ollama" and not ollama_available():
            print("[error] Ollama not running at localhost:11434", file=sys.stderr)
            sys.exit(1)
        if args.backend == "mlx" and not mlx_available():
            print("[error] mlx-lm server not running at localhost:8080", file=sys.stderr)
            sys.exit(1)
        if args.backend == "ollama":
            available = get_available_ollama_models()
            missing = [m for m in args.model if m not in available]
            if missing:
                print("[warn] Models not available in Ollama (will be skipped by pytest):")
                for name in missing:
                    print(f"  - {name}")
                print()

    sweep_start = time.monotonic()
    report_sections: list[str] = []

    for model in args.model:
        per_run_results: list[dict[str, str]] = []

        for run_n in range(1, args.runs + 1):
            label = f"{model} ({args.backend})  run {run_n}/{args.runs}"

            with tempfile.TemporaryDirectory() as tmpdir:
                junit_path = Path(tmpdir) / "results.xml"
                cmd = build_pytest_cmd(model, args.backend, args.db, args.tiers, junit_path, [])

                print(f"[{'DRY RUN' if args.dry_run else 'RUN'}] {label}")
                if args.dry_run:
                    print(f"  cmd: {' '.join(cmd)}\n")
                    per_run_results.append({})
                    continue

                t0 = time.monotonic()
                subprocess.run(cmd, cwd=str(ROOT), text=True)
                duration = time.monotonic() - t0

                run_results = parse_junit_results(junit_path)
                per_run_results.append(run_results)

                failed = sum(1 for o in run_results.values() if o in ("failed", "error"))
                passed = sum(1 for o in run_results.values() if o == "passed")
                xfailed = sum(1 for o in run_results.values() if o == "xfailed")
                print(f"  → {passed}p {failed}f {xfailed}xf  {fmt_duration(duration)}\n")

        if not args.dry_run:
            section = summarize(model, args.runs, per_run_results)
            report_sections.append(section)
            print(section)
            print()

    sweep_duration = time.monotonic() - sweep_start
    print(f"{'='*60}")
    print(f"  Sweep complete: {fmt_duration(sweep_duration)}")
    print(f"{'='*60}\n")

    if args.report and report_sections:
        report_path = Path(args.report)
        header = (
            f"# Multi-sweep study report\n\n"
            f"Backend: {args.backend}  \n"
            f"Tiers: {args.tiers or 'all'}  \n"
            f"Runs per model: {args.runs}  \n"
            f"Generated: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}\n\n"
        )
        report_path.write_text(header + "\n\n".join(report_sections) + "\n")
        print(f"Report written to: {report_path}\n")


if __name__ == "__main__":
    main()
