# Protocol-Tier Planning — MCP 2026-07-28 Spec Work

Resolves two planning gaps found while scoping `SESSION-HANDOFF.md` step 6
against `mcp-relay-2026-07-28-spec-update.md` (source doc, lives in the
original `mcp-relay` repo root — not copied here, it's a planning doc, not
code). Read that doc for full case-design detail; this file only records
the naming/tooling decisions made on top of it, and what's still open.

## Decision 1: new protocol-security work uses a P-prefix, not Tier 6/7/8

`ENHANCEMENTS.md:14-22` already has a Tier taxonomy for the existing
fetch-server SSRF-compliance corpus, including a **Tier 6 — "Thinking
mode (qwen3.5)"** entry (`/think` vs `/no_think` latency/reliability,
backlog since 2026-03-06, never built — `docs/test-corpus.md`'s real,
implemented taxonomy only documents Tiers 1–5).

The spec-update doc's Tier 6/7/8 (stateless-handle exploitation,
header-channel leakage, task-spawn abuse) are a structurally different
axis — protocol-level attack surface, not "does the model call the fetch
tool correctly." Reusing the same numeric Tier space for both was always
going to collide eventually.

**Resolved:** new protocol-security tiers get their own namespace, prefix
`P`, instead of extending the numeric `Tier` sequence:

| Spec-update doc name | This project's name |
|---|---|
| Tier 6 — Stateless-handle exploitation | **P1** — Stateless-handle exploitation |
| Tier 7 — Header-channel data leakage | **P2** — Header-channel data leakage |
| Tier 8 — Task-spawn abuse | **P3** — Task-spawn abuse |
| Protocol-version cross-cut | unchanged — not a tier, a cross-cutting variable re-run over existing Tiers 1–5 |

`ENHANCEMENTS.md`'s existing Tier 6 ("thinking mode") is undisturbed and
keeps its number. Case IDs for the new work should follow the existing
`t{n}_description` convention with the new prefix, e.g. `p1_predictable_
handle_reuse`, to stay self-explanatory in pytest output and greps.

## Decision 2: `probe_coverage.py` convention does not apply — skip it

Both `SESSION-HANDOFF.md` and the spec-update doc say "add new tier
definitions to the framework config before running anything — don't
hand-construct probe commands from memory; use `probe_coverage.py --quiet`
verbatim." No such script exists in either `mcp-relay` or `mcp-relay-v2`
— only `scripts/probe-ssrf.py`, a single-purpose SSRF policy probe,
unrelated in function. This instruction appears to be carried over from a
different project template (the `probe_models.json`/`--sync-probes`
pattern is a documented convention elsewhere in the user's research
projects) and was never actually instantiated for mcp-relay.

**Resolved:** treat that instruction as inapplicable here. New tier cases
(P1/P2/P3, and the cross-cut's re-run config) go directly into
`tests/fixtures/test_cases.yaml`, the same way Tiers 1–5 already work.
pytest's `@pytest.mark.parametrize` over the YAML already gives full case
coverage with no separate coverage-checking layer needed.

## Known prerequisite gap: P2 needs HTTP transport

`Mcp-Method`/`Mcp-Name` are HTTP headers — meaningless over stdio.
`SESSION-HANDOFF.md`'s original harness audit (item 1) already noted the
codebase only implements a stdio transport (`LiveTransport` via
`stdio_client`); no HTTP transport exists anywhere in `mcp-relay`. P2
(header-channel leakage) cannot be tested until that's built — a
materially bigger prerequisite than case design, closer in scope to the
original SDK migration than to writing new YAML rows.

P1 and P3 have no such blocker — both are stdio-compatible in principle
(P1 needs a mock server issuing handles as ordinary tool arguments; P3
needs a mock server that accepts a task-spawn request and simulates a
long-running operation).

## Still open — not decided yet

- **Sequencing**: the spec-update doc's own suggested order puts the
  protocol-version cross-cut before P1–P3 (cheapest, reuses the existing
  Tier 1–5 case set, most directly extends the already-published
  weights/runtime finding). Not yet confirmed as this project's actual
  order — deferred pending a separate decision.
- **Mock server design**: cross-cut needs a mock server speaking both
  2025-11-25 (stateful, `initialize` handshake) and 2026-07-28 (stateless,
  `server/discover`) — doesn't exist yet, not yet designed.
- **P2 HTTP transport work**: not scoped, not started.
