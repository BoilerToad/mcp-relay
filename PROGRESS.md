# mcp-relay-v2 — Progress Tracker

One-page "where are we right now." Updated as work happens; doesn't replace
the detailed docs it points to — just tells you which one to open next.

| Doc | Scope |
|---|---|
| `SESSION-HANDOFF.md` | Why v2 exists, the migration plan (steps 1–6) |
| `REGRESSION-TESTING-PROGRESS.md` | v1-vs-v2 migration regression evidence (CONCLUDED) |
| `PROTOCOL-TIERS-PLAN.md` | MCP 2026-07-28 spec-tier work: naming, design decisions, build log |
| `ENHANCEMENTS.md` | Long-lived backlog, ideas not yet scheduled |
| `MEMORY.md` | Conventions, key decisions, `[LEARN]` corrections |
| `PROGRESS.md` (this file) | Current status snapshot + immediate next action |

## Done

- [x] `mcp>=2.0.0` SDK migration (steps 1–5) — regression-clean vs. original
      `mcp-relay` at n=3 per-case and n=30 aggregate. See
      `REGRESSION-TESTING-PROGRESS.md`.
- [x] Commit hygiene — 5 commits made and pushed to
      `chore/pin-mcp-sdk-below-v2` (migration, fixture fixes, doc cleanup,
      planning docs, sweep script).
- [x] Step 6 planning gaps resolved — P1/P2/P3 naming scheme, tier-number
      collision with `ENHANCEMENTS.md`'s existing Tier 6 resolved, P2's
      HTTP-transport prerequisite identified. See `PROTOCOL-TIERS-PLAN.md`.
- [x] Protocol-version cross-cut infrastructure built:
  `mock_servers/modern_fetch_server.py` + `--protocol-era {legacy,modern}`
      pytest option. Two confounds found and fixed during shakeout
      (tool-description text, missing `User-Agent` header) — both verified
      fixed.
- [x] `--protocol-era` passthrough added to
      `scripts/run_multi-sweep_study.py` (uncommitted).
- [x] Modern-leg data collected: **n=3, all 28 cases, `qwen2.5:latest`**
      (2026-08-13) — `sweep-reports/sweep-report-modern-qwen2.5.md`.
      5/84 failed, all in Tier 4 (`t4_definition` 2/3, `t4_capital_city`,
      `t4_historical_fact`, `t4_conversational` 1/3 each);
      `t3_redirect_follow` xfail 3/3; Tiers 1, 2, 3, 5 otherwise clean.
- [x] Literature + claim review (2026-10-03), prompted by external feedback:
  - Added CVE-2026-26118, the Syed IETF Internet-Draft (local, gitignored copy:
    `docs/draft-mohiuddin-mcp-security-considerations-00.txt`), and the
    CERT-AgID study to `docs/literature.md`; synced model counts (6
    configurations = 5 distinct models + Qwen3.5 on a second runtime).
  - **Softened the central claim** in `docs/literature.md` and
    `docs/ssrf-and-compliance.md`: relay enforcement is a *necessary*
    layer, **not sufficient** — it validates at parse time, so it can't see
    redirects or DNS rebinding (the draft's §7 requires connection-time
    validation + egress controls).
  - **New known limitation:** `SSRFRule` does no DNS resolution, so
    hostnames resolving to private IPs (e.g. `169.254.169.254.nip.io`) are
    allowed. Documented in `mcp_relay/policy/rules.py`,
    `docs/testing-strategy.md`, `docs/ssrf-and-compliance.md`; pinned by
    `test_known_limit_hostname_resolving_to_private_ip` (behavior
    unchanged — documentation + test only).
  - **`SSRFRule` now fails closed** on URLs whose host can't be determined
    (previously ALLOW — the fail-open pattern CERT-AgID's paper is about).
    "URL" means the value contains `://`. Scheme-less values are checked as
    `http://<value>` and blocked if that points somewhere private. `AllowlistRule`
    fails closed the same way. Block reason no longer claims DNS resolution.
    Tests: `test_blocks_url_with_unparseable_host` (both rules),
    `test_unparseable_host_warns_in_warn_mode`, `test_allows_schemeless_value`,
    `test_blocks_schemeless_private_target`, `test_block_reason_does_not_claim_dns_resolution`.

## In progress

- [ ] **Protocol-version cross-cut comparison.** Modern leg collected (above).
      The only legacy-leg baseline at n=3 is **Tier 4 only** (qwen2.5
      5/18 in `REGRESSION-TESTING-PROGRESS.md`) — Tier 4 alone matches the
      modern leg's 5/18, but there's no full-corpus legacy n=3 run to
      compare the other 22 cases against.
- [ ] `scripts/tier4_priming_probe.py` (+ test) — side probe: does a "use
      what you know" prefix reduce spurious Tier 4 calls? Built, uncommitted;
      no results recorded yet.

## Next action

Follow the pre-registered comparison rule in `PROTOCOL-TIERS-PLAN.md`
("Cross-cut comparison rule"). In order:

1. Commit the outstanding work; note the hash.
2. Record `ollama --version` and the `qwen2.5:latest` digest (`ollama list`).
3. Smoke-test the new policy through the full relay:
   ```bash
   pytest tests/test_llm_tool_calls.py -m integration --model qwen2.5:latest \
       -k "t5_ssrf_private_ip or t5_localhost_probe or t5_malformed_url" -v
   ```
4. Back up the DB, then run **both** legs on that commit, each with its
   own DB (the Aug 13 modern report predates the policy changes and is
   exploratory only). Note each run's start/end time for the JSONL log:
   ```bash
   cp ~/.mcp-relay/research.db ~/.mcp-relay/research-backup-2026-10-03.db

   python scripts/run_multi-sweep_study.py --model qwen2.5:latest --runs 3 \
       --protocol-era legacy --db ~/.mcp-relay/crosscut-legacy.db \
       --report sweep-reports/sweep-report-legacy-qwen2.5.md

   python scripts/run_multi-sweep_study.py --model qwen2.5:latest --runs 3 \
       --protocol-era modern --db ~/.mcp-relay/crosscut-modern.db \
       --report sweep-reports/sweep-report-modern-qwen2.5-v2.md
   ```
5. Apply the decision rule; follow up any flagged case at n≥10 per leg.

## Not started

- P1 — stateless-handle exploitation (needs a mock server issuing
  predictable + random handles)
- P2 — header-channel leakage (**blocked**: no HTTP transport exists yet)
- P3 — task-spawn abuse (needs a mock server simulating long-running tasks)
- Transport-mode completion (`OFFLINE`/`RECORD`/`REPLAY`/`DEGRADED` all
  raise `NotImplementedError` — pre-existing v1 gap, `ENHANCEMENTS.md` §3)
