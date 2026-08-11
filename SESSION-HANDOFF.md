# mcp-relay-v2 — Session Handoff

Context carried over from a Claude Code session in the original `mcp-relay`
repo that couldn't resume directly into this window. Read this first.

## What mcp-relay is

A transparent MCP proxy for security research: sits between a local LLM and
an MCP server, logging every tool call. Completed corpus (28 cases, Tiers
1–5, 3 runs/case) established the key finding: **SSRF compliance behavior
travels with model weights, not the inference runtime** (tested across
Ollama and mlx-lm backends).

## Why this v2 folder exists

On 2026-07-28, MCP published a major spec revision (`2026-07-28`) —
described as the largest revision since launch. See
`mcp-relay-2026-07-28-spec-update.md` in the original `mcp-relay` repo root
for the full proposal (not copied here — it's a planning doc, not code).
Key points:

- The `initialize`/`initialized` handshake and `Mcp-Session-Id` header are
  removed. Sessions become explicit, application-managed opaque handles.
- New mandatory `Mcp-Method`/`Mcp-Name` headers — flagged risk of secrets
  leaking into headers (visible to proxies/logs).
- Tasks moved out of core protocol — spawn-and-abandon becomes a DoS
  pattern.
- Predictable state handles become a new risk class (session hijack).
- ~12 month backward-compat window; ~30% of indexed MCP servers are
  actively maintained, so a long tail of stateful pre-2026-07-28 servers
  will coexist with the new spec.

This creates two follow-on research threads: (1) new behavioral tiers
(handle prediction, header leakage, task-spawn abuse) using the same
refusal/compliance framing as the existing SSRF corpus, and (2) a
protocol-version cross-cut — re-running the existing Tier 1–5 corpus
against stateful (2025-11-25) vs. stateless (2026-07-28) mock servers.

## What's been done so far (in the original `mcp-relay` repo)

1. **Audited the harness for spec-breaking dependencies.** Found exactly
   one call site tied to the old handshake:
   `mcp_relay/transport/live.py:56` — `await self._session.initialize()`.
   No `Mcp-Session-Id` usage anywhere — the codebase only implements a
   stdio transport (`LiveTransport` via `stdio_client`); the new mandatory
   HTTP headers don't apply to any code path that currently exists.
   (Note: the `session_id` seen throughout `relay.py`, `core/intercept.py`,
   `storage/*.py` is mcp-relay's own internal research bookkeeping — a
   UUID per test run for the SQLite `sessions`/`events` tables. Unrelated
   to MCP protocol session state; don't confuse it with the new spec's
   handle concept when building Tier 6.)

2. **Found the real blocker: the SDK, not the harness code.** The `mcp`
   Python SDK's 2.0.0 release (stable, shipped the same day as the spec,
   2026-07-28) is the breaking version:
   - `ClientSession.initialize()` is removed, replaced by
     `.discover()` / `.adopt()` / `Client(mode='auto')` for automatic
     version negotiation.
   - `Mcp-Session-Id` handling is removed from stateless flows.
   - The SDK's own migration guidance: pin `mcp>=1.28,<2` if not ready to
     migrate.
   - `pyproject.toml` previously had `mcp>=1.26.0` with **no upper
     bound** — a fresh install would have silently pulled in 2.0.0 and
     broken `LiveTransport`.

3. **Pinned and shipped the fix** in the original repo:
   - `pyproject.toml`: `mcp>=1.26.0` → `mcp>=1.28,<2`
   - venv upgraded 1.26.0 → 1.29.0 (final v1.x release; still has
     `ClientSession.initialize()`)
   - Unit suite verified green (158 passed; 1 pre-existing failure —
     unimplemented `offline` transport mode — confirmed unrelated to the
     SDK version, fails identically on 1.26.0)
   - Committed and pushed to GitHub on branch
     `chore/pin-mcp-sdk-below-v2` (not yet merged to `main`; PR not yet
     opened — https://github.com/BoilerToad/mcp-relay/pull/new/chore/pin-mcp-sdk-below-v2)

4. **Created this folder** (`mcp-relay-v2`, sibling to `mcp-relay`) via
   `git clone` from `origin`, checked out at the pinned-SDK commit
   (branch `chore/pin-mcp-sdk-below-v2`). Then filtered content per
   explicit decisions:
   - `docs/` — kept `architectural_design.md`, `docx-style-guide.md`,
     `literature.md`, `test-corpus.md`, `testing-strategy.md`. Removed
     findings/results docs: `academic-results_v1.md`,
     `findings-initial.docx`, and both `findings_*.txt` files.
   - `studies/` — copied in full (`default.yaml`, `full_study.yaml`,
     `study_models.json`) — these are configs/registry, not results, so
     nothing was excluded.
   - `tests/` — copied in full; more will be added here for the new
     tiers.
   - Root-level result artifacts (`.coverage`, `coverage_report.txt`,
     `integration_run.txt`, `llm_test_run*.txt`) are gitignored in the
     source repo, so they were never part of the clone — no action
     needed.
   - A fresh Python 3.13 `.venv` already exists here (created by the
     user). **Nothing has been installed into it yet.**

## What's next (not started yet)

The actual v2 SDK migration, in this folder:

1. Loosen `pyproject.toml`'s pin from `mcp>=1.28,<2` to allow
   `mcp>=2.0.0` (this repo copy only — the original stays pinned `<2`).
2. `pip install -e ".[dev]"` into the fresh `.venv` here to pull in
   `mcp` 2.0.0.
3. Rewrite `mcp_relay/transport/live.py`'s connect flow: replace
   `await self._session.initialize()` with whatever `.discover()` /
   `.adopt()` / `Client(mode='auto')` requires — check the SDK's actual
   migration guide for before/after code, don't guess at the new API
   shape from the release notes summary alone.
4. Check `mcp_relay/core/intercept.py` (relay acting as an MCP *server*
   via `mcp.server.Server` + `stdio_server`) for equivalent v2 API
   changes on the server side.
5. Re-run the unit suite; expect breakage beyond just `live.py` until
   the whole connect/handshake path is updated.
6. Only after the harness works on v2: start on the new tiers proposed
   in `mcp-relay-2026-07-28-spec-update.md` (Tier 6 handle exploitation,
   Tier 7 header leakage, Tier 8 task-spawn abuse) and the protocol-
   version cross-cut re-run of Tiers 1–5.

## Project conventions to carry forward

- 3 independent runs per case, matching the existing corpus design.
- Add new tier definitions to the framework config before running
  anything — don't hand-construct probe commands from memory; use
  `probe_coverage.py --quiet` verbatim.
- Registry files (e.g. `probe_models.json` / `study_models.json`) are
  not edited directly — use `--sync-probes` and fill only the null
  research fields it flags.
- New Python code ships with tests; write the failing regression test
  before fixing any bug encountered along the way.
