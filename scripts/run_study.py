#!/usr/bin/env python3
"""
run_study.py — Multi-model, multi-run study runner for mcp-relay.

Reads a study YAML config, then runs the pytest corpus once per model
per run, collecting results into SQLite.  Prints a summary table at the end.

Usage:
    python scripts/run_study.py
    python scripts/run_study.py --study studies/default.yaml
    python scripts/run_study.py --study studies/default.yaml --dry-run
    python scripts/run_study.py --study studies/default.yaml --tiers 1 4 5
    python scripts/run_study.py --study studies/default.yaml --runs 1
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import yaml

# ---------------------------------------------------------------------------
# Resolve project root (script lives in <root>/scripts/)
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_study(path: Path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


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
    ]
    if tiers:
        k_expr = " or ".join(f"tier{t}" for t in tiers)
        cmd += ["-k", k_expr]
    cmd += extra_args
    return cmd


def fmt_duration(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    return f"{m}m{s:02d}s" if m else f"{s}s"


def parse_pytest_outcome(returncode: int, output: str) -> dict:
    """Extract pass/fail/xfail counts from pytest stdout."""
    import re
    counts = {"passed": 0, "failed": 0, "xfailed": 0, "skipped": 0, "error": 0}
    # Look for lines like: "27 passed, 1 xfailed in 258.49s"
    pattern = re.compile(r"(\d+)\s+(passed|failed|xfailed|skipped|error)")
    for match in pattern.finditer(output):
        n, label = int(match.group(1)), match.group(2)
        counts[label] = counts.get(label, 0) + n
    counts["returncode"] = returncode
    return counts


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="mcp-relay multi-model study runner")
    parser.add_argument(
        "--study", default="studies/default.yaml",
        help="Path to study YAML config (default: studies/default.yaml)"
    )
    parser.add_argument(
        "--runs", type=int, default=None,
        help="Override runs_per_model from config"
    )
    parser.add_argument(
        "--tiers", nargs="+", type=int, default=None,
        help="Override tiers to run (e.g. --tiers 1 4 5)"
    )
    parser.add_argument(
        "--db", default=None,
        help="Override SQLite DB path"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print commands without executing"
    )
    args = parser.parse_args()

    study_path = ROOT / args.study
    if not study_path.exists():
        print(f"[error] Study file not found: {study_path}", file=sys.stderr)
        sys.exit(1)

    config = load_study(study_path)
    study_meta = config.get("study", {})
    study_name = study_meta.get("name", "unnamed")
    runs_per_model = args.runs or study_meta.get("runs_per_model", 1)
    tiers = args.tiers or config.get("tiers") or None
    db_path = args.db or config.get("db") or str(Path("~/.mcp-relay/research.db").expanduser())
    extra_args = config.get("extra_pytest_args") or []

    enabled_models = [
        {"name": m["name"], "backend": m.get("backend", "ollama")}
        for m in config.get("models", [])
        if m.get("enabled", True)
    ]

    print(f"\n{'='*60}")
    print(f"  mcp-relay study: {study_name}")
    print(f"  Config:          {study_path}")
    model_summary = ", ".join(f"{m['name']} ({m['backend']})" for m in enabled_models)
    print(f"  Models:          {model_summary}")
    print(f"  Runs per model:  {runs_per_model}")
    print(f"  Tiers:           {tiers or 'all'}")
    print(f"  DB:              {db_path}")
    print(f"  Started:         {datetime.now(datetime.UTC).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'='*60}\n")

    if not args.dry_run:
        ollama_models = [m for m in enabled_models if m["backend"] == "ollama"]
        mlx_models    = [m for m in enabled_models if m["backend"] == "mlx"]

        if ollama_models and not ollama_available():
            print("[error] Ollama not running at localhost:11434", file=sys.stderr)
            sys.exit(1)
        if mlx_models and not mlx_available():
            print("[error] mlx-lm server not running at localhost:8080", file=sys.stderr)
            print("        Start: mlx_lm.server --model <name> --port 8080", file=sys.stderr)
            sys.exit(1)

        if ollama_models:
            available = get_available_ollama_models()
            missing = [m["name"] for m in ollama_models if m["name"] not in available]
            if missing:
                print("[warn] Models not available in Ollama (will be skipped by pytest):")
                for name in missing:
                    print(f"  - {name}")
                print()

    # ── Run matrix ──────────────────────────────────────────────────────────
    results: list[dict] = []
    study_start = time.monotonic()

    for entry in enabled_models:
        model, backend = entry["name"], entry["backend"]
        for run_n in range(1, runs_per_model + 1):
            label = f"{model} ({backend})  run {run_n}/{runs_per_model}"
            cmd = build_pytest_cmd(model, backend, db_path, tiers, extra_args)

            print(f"[{'DRY RUN' if args.dry_run else 'RUN'}] {label}")
            if args.dry_run:
                print(f"  cmd: {' '.join(cmd)}\n")
                results.append({"model": model, "backend": backend, "run": run_n,
                                 "status": "dry-run", "duration": 0,
                                 "passed": 0, "failed": 0, "xfailed": 0})
                continue

            t0 = time.monotonic()
            proc = subprocess.run(
                cmd,
                cwd=str(ROOT),
                capture_output=False,   # let output stream to terminal
                text=True,
            )
            # Re-run capturing output just for parsing (second run is fast — pytest cache)
            parse_proc = subprocess.run(
                cmd + ["--no-header", "-q"],
                cwd=str(ROOT),
                capture_output=True,
                text=True,
            )
            duration = time.monotonic() - t0
            outcome = parse_pytest_outcome(proc.returncode, parse_proc.stdout + parse_proc.stderr)
            outcome.update({"model": model, "backend": backend, "run": run_n, "duration": duration})
            results.append(outcome)

            status = "PASS" if proc.returncode == 0 else "FAIL"
            passed  = outcome['passed']
            failed  = outcome['failed']
            xfailed = outcome['xfailed']
            dur     = fmt_duration(duration)
            print(f"  → {status}  {passed}p {failed}f {xfailed}xf  {dur}\n")

    study_duration = time.monotonic() - study_start

    # ── Summary table ────────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"  Study complete: {fmt_duration(study_duration)}")
    print(f"{'='*60}")
    print(f"  {'Model':<28} {'Run':>4} {'P':>5} {'F':>5} {'XF':>5} {'Time':>8}  Status")
    print(f"  {'-'*28} {'-'*4} {'-'*5} {'-'*5} {'-'*5} {'-'*8}  ------")
    for r in results:
        rc = r.get("returncode", 0)
        status = r.get("status", "PASS" if rc == 0 else "FAIL")
        print(
            f"  {r['model']:<28} {r['run']:>4} "
            f"{r.get('passed',0):>5} {r.get('failed',0):>5} {r.get('xfailed',0):>5} "
            f"{fmt_duration(r.get('duration', 0)):>8}  {status}"
        )

    total_pass   = sum(r.get("passed",  0) for r in results)
    total_fail   = sum(r.get("failed",  0) for r in results)
    total_xfail  = sum(r.get("xfailed", 0) for r in results)
    print(f"  {'-'*60}")
    print(f"  {'TOTAL':<28}      {total_pass:>5} {total_fail:>5} {total_xfail:>5}")
    print(f"{'='*60}\n")

    if not args.dry_run:
        print(f"Results written to: {db_path}")
        print("View with:  python demo/research_report.py both\n")

    sys.exit(1 if total_fail > 0 else 0)


if __name__ == "__main__":
    main()
