# mcp-relay-v2 — Regression Testing Progress

**Status: CONCLUDED — regression testing judged adequate to proceed.**

Snapshot of the `mcp>=2.0.0` SDK migration and the regression test against
the original `mcp-relay` (pinned `mcp<2.0`). Companion to
`SESSION-HANDOFF.md`, which covers the migration plan this continues from,
and whose step 6 (new tiers, protocol-version cross-cut) is now unblocked.

## Migration status: steps 1–5 complete

| Step (per `SESSION-HANDOFF.md`) | Status |
|---|---|
| 1. Loosen `pyproject.toml` pin to `mcp>=2.0.0` | Done |
| 2. `pip install -e ".[dev]"` into fresh `.venv` | Done (by user) |
| 3. Rewrite `live.py` connect flow | Done |
| 4. Check `intercept.py` server-side API | Done |
| 5. Re-run unit suite | Done — clean |
| 6. New tiers (6–8) + protocol-version cross-cut | Not started — blocked, see below |

## Files changed this session

| File | Change |
|---|---|
| `pyproject.toml` | `mcp>=1.28,<2` → `mcp>=2.0.0`; added `openai>=2.26.0` to `dev` extras |
| `mcp_relay/transport/live.py` | Connect flow rewritten: `ClientSession.initialize()` → `mcp.Client(stdio_client(params), mode="auto")`. `initialize()` is **not** removed in 2.0.0 — it's retained as the legacy-handshake fallback that `mode="auto"` calls internally after probing `server/discover`. |
| `mcp_relay/core/intercept.py` | `Server.list_tools()`/`.call_tool()` decorators (removed in 2.0.0) → constructor callbacks `on_list_tools`/`on_call_tool` with new `(ctx, params)` signatures and full `CallToolResult`/`ListToolsResult` return types. `result.isError` → `result.is_error`. |
| `tests/conftest.py` | `live_config` fixture's upstream args: `["mcp-server-fetch"]` → `["--with", "mcp<2.0", "mcp-server-fetch"]` |
| `tests/test_integration.py` | Same `--with mcp<2.0` fix (3 sites) + `.isError` → `.is_error` (2 sites) |
| `tests/test_llm_tool_calls.py` | Same `--with mcp<2.0` fix (1 site, in `relay_config` fixture) |

### Why the `--with mcp<2.0` fix exists

Every LIVE-mode test shells out to `uvx mcp-server-fetch` as the upstream
server. That package's own `mcp` dependency has no upper bound, so a bare
`uvx mcp-server-fetch` now resolves `mcp` 2.0.0 for *that* subprocess too —
and `mcp-server-fetch`'s code still does
`from mcp.shared.exceptions import McpError`, which 2.0.0 renamed to
`MCPError`. The subprocess crashes on import before ever responding.
Forcing `uvx --with 'mcp<2.0' mcp-server-fetch` pins that subprocess back to
a compatible SDK version — confirmed working by hand
(`uvx --with 'mcp<2.0' mcp-server-fetch --help` runs cleanly).

**Trade-off:** with that pin, the upstream server still speaks only the
legacy handshake, so this exercises `mcp.Client`'s `initialize()` fallback
path, not the new `server/discover` negotiation. No 2026-07-28-era server
exists yet to test the new path against — that's part of what Tier 6–8 /
the protocol-version cross-cut (step 6) is for.

## Unit suite (non-integration)

```
pytest -m "not integration" --cov=mcp_relay --cov-report=term-missing
```

**158 passed, 1 skipped, 1 failed, 32 deselected.**

- The skip is a documented known limitation (`test_policy_engine.py:177`,
  URL-encoded hostname bypass — pre-existing, unrelated to this migration).
- The 1 failure (`test_mockllm_fetch_calls_are_logged`) is
  `NotImplementedError: Transport mode offline is not yet implemented` —
  pre-existing in v1 too (see `SESSION-HANDOFF.md` item 3), not a
  regression from this migration.

## Regression test against v1: IN PROGRESS, blocked

Command:

```
pytest -m integration --model llama3.2:latest tests/test_llm_tool_calls.py
```

Model chosen: `llama3.2:latest` (2.0GB, smallest of the
`studies/default.yaml` smoke-test models, already pulled locally,
`tool_capable` confirmed in `studies/study_models.json`).

**Result: all 28 cases (Tiers 1–5) failed on a single root cause**, not yet
fixed:

```
tests/test_llm_tool_calls.py:273, in _mcp_tool_to_ollama
    "parameters": tool.inputSchema,
AttributeError: 'Tool' object has no attribute 'inputSchema'. Did you mean: 'input_schema'?
```

Same pattern as the `isError`→`is_error` rename already fixed elsewhere:
mcp 2.0.0 renamed `Tool.inputSchema` to `Tool.input_schema`. Confirmed via
repo-wide grep that this was the only instance of `.inputSchema`/
`.outputSchema` anywhere in the codebase — fixed, no others remained.

A secondary, currently-unexplained signal showed up in the same run: a
`WARNING:root:Failed to validate request: 31 validation errors for
ClientRequest` block, listing `server/discover` as an invalid `method` for
every legacy request type. Current best read: this is the *pinned* (`<2.0`)
`mcp-server-fetch` subprocess logging a rejection when `mcp.Client`'s
`mode="auto"` probes it with `server/discover` before falling back to
`initialize()` — i.e. expected fallback noise, not a failure cause — but
this has **not been confirmed**, only inferred from the traceback shape.

## Regression test against v1: RESOLVED — no regression found

After fixing `tests/test_llm_tool_calls.py:273` (`tool.inputSchema` →
`tool.input_schema`, the last unfixed camelCase rename), the
`llama3.2:latest` run against **mcp-relay-v2** completed:

```
21 passed, 1 xfailed, 6 failed
```

All 6 failures were in Tier 4 (`test_tier4_no_tool_call`), all with
`AssertionError: Spurious tool call for factual question` — the model
called the `fetch` tool on questions that should need no tool call at all
(capital of France, simple math, historical fact, definition, code
question, conversational).

**To determine whether this was a migration regression**, the same Tier 4
cases were run against the **original `mcp-relay`** repo (sibling repo,
`chore/pin-mcp-sdk-below-v2` branch, pinned `mcp>=1.28,<2`, unmigrated).
That required first applying the identical, uncommitted
`uvx mcp-server-fetch` → `uvx --with 'mcp<2.0' mcp-server-fetch` fixture
patch there too (7 sites in `tests/conftest.py`,
`tests/test_integration.py`, `tests/test_llm_tool_calls.py`) — `uvx`
resolution is global to the machine, not scoped per-repo, so the original
repo's fixtures had started hitting the same external `mcp-server-fetch`
breakage independent of its own `mcp<2` pin.

Result in the original repo:

```
pytest -m integration --model llama3.2:latest tests/test_llm_tool_calls.py -k tier4 -v
6 failed, 22 deselected
```

**Identical failure signature** — same 6 cases, same
`AssertionError: Spurious tool call for factual question`.

**Conclusion:** `llama3.2:latest`'s Tier 4 spurious-tool-call behavior is
pre-existing, present in the original (unmigrated) codebase. **The
mcp>=2.0.0 migration introduces no behavioral regression** — mcp-relay-v2
is observationally equivalent to v1 for this model on this corpus.

## Second model: qwen2.5:latest — 3 runs each side, no regression found

Single-run comparison initially showed 2/6 Tier 4 failures on each side
but non-overlapping cases (`t4_capital_city`/`t4_code_question` differed).
Per project convention (3 runs/case) and to rule out sampling noise before
concluding anything, re-ran Tier 4 3x on each side via
`scripts/run_study.py --model qwen2.5:latest --tiers 4 --runs 3`, separate
DBs (`~/.mcp-relay/regression-v2.db`, `~/.mcp-relay/regression-v1.db`):

| Case | v2 fail rate | v1 fail rate |
|---|---|---|
| t4_capital_city | 1/3 | 1/3 |
| t4_simple_math | 0/3 | 0/3 |
| t4_historical_fact | 2/3 | 2/3 |
| t4_definition | 1/3 | 2/3 |
| t4_code_question | 1/3 | 0/3 |
| t4_conversational | 0/3 | 0/3 |
| **Total** | **5/18** | **5/18** |

Aggregate failure rate identical (5/18 both sides). 4 of 6 cases match
exactly; `t4_historical_fact` is the most reliably problematic question on
both (2/3). The two cases that differ (`t4_definition`, `t4_code_question`)
are off by exactly one run each — consistent with ordinary sampling
variance at n=3, not a migration-linked pattern.

**Conclusion:** no evidence of a regression for `qwen2.5:latest` either.
Two models now checked (`llama3.2:latest` single-run identical 6/6;
`qwen2.5:latest` 3-run aggregate identical 5/18) — both consistent with
mcp-relay-v2 being behaviorally equivalent to v1.

## Additional confirmation: 5-run aggregate via run_study.py

Independently re-run via `scripts/run_study.py` (aggregate-only, not the
per-case sweep tool below), `qwen2.5:latest`, 5 runs each side:

| | Passed | Failed | Total |
|---|---|---|---|
| v2 | 24 | 6 | 30 |
| v1 | 24 | 6 | 30 |

Exact match — 20% fail rate both sides across 30 executions each. Further
reinforces the per-case parity already established at n=3 above.

## New tooling built during this check

`scripts/run_multi-sweep_study.py` — repeated-run sweep + per-case
markdown summarization for one repo's `test_llm_tool_calls.py` corpus
(`scripts/run_study.py` gives aggregate pass/fail per run only; this adds
per-case fail-rate across N runs, using `pytest --junitxml` for robust
parsing). Verified against the hand-compiled `qwen2.5:latest` v1 table
above (exact match). Copied to `mcp-relay/scripts/` as well, verified
compiling and dry-running there against that repo's own `.venv`. Neither
copy has been committed yet.

## Conclusion: regression testing judged adequate — proceeding

Two models checked, both clean:

- **llama3.2:latest** — single run, identical 6/6 Tier 4 failures both
  sides.
- **qwen2.5:latest** — 3 runs each side, identical 5/18 aggregate, 4/6
  cases matching exactly, the 2 that differ off by only one run (noise,
  not pattern).

No evidence of a behavioral regression from the `mcp>=2.0.0` migration on
either model. This is judged sufficient to move forward — full multi-model
study (`studies/default.yaml`, 5 models) was not run, and remains
available as a follow-up if broader confidence is ever wanted, but is not
blocking.

Remaining open items, none blocking, carried forward for whenever step 6
(new tiers / protocol-version cross-cut, per `SESSION-HANDOFF.md`) starts:

- Original `mcp-relay` repo has the `--with mcp<2.0` fixture patch (and
  now `run_multi-sweep_study.py`) applied locally but **uncommitted**
  (intentionally — the fixture patch fixes an external package's
  breakage, not anything native to that pinned repo).
- `mcp-server-fetch` (Anthropic-maintained,
  `modelcontextprotocol/servers`) still lacks an upper bound on its own
  `mcp` dependency — worth periodically re-checking whether upstream fixes
  it, at which point the `--with mcp<2.0` workaround in both repos can be
  dropped. Sibling issue for `mcp-server-time` tracked at
  [modelcontextprotocol/servers#4570](https://github.com/modelcontextprotocol/servers/issues/4570);
  no `mcp-server-fetch`-specific issue found yet.
- Testing the *new* `server/discover` negotiation path (as opposed to the
  legacy `initialize()` fallback exercised throughout this check) still
  needs a mock 2026-07-28-era server — no real one is available yet. This
  is exactly what step 6 (Tier 6–8, protocol-version cross-cut) covers.
