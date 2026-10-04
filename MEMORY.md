# mcp-relay-v2 — Project Memory

Structured knowledge and `[LEARN]` corrections. Read at session start,
after `README.md`, `PROGRESS.md` and `RESEARCH-PHASES.md`.

---

## Notation Registry

| Variable | Convention | Anti-pattern |
|---|---|---|
| Case IDs | `t{n}_description` (Tiers 1–5); `p{n}_description` for protocol tiers P1–P3 | Numbering protocol work as Tier 6/7/8 (collides with ENHANCEMENTS.md's thinking-mode Tier 6) |
| Model count (v1 study) | "6 model configurations (5 distinct models + Qwen3.5 on a second runtime), 4 organizations" | "5 models" or "6 models" without qualification |
| Protocol legs | `legacy` (2025-11-25, real `mcp-server-fetch` pinned `mcp<2.0`) / `modern` (2026-07-28, `mock_servers/modern_fetch_server.py`) | Calling both legs "mock servers" |

## Estimand Registry

| What we estimate | Identification | Key assumptions |
|---|---|---|
| Per-case fail rate of tool-call decisions (call / no-call / argument content) | Same 28-case corpus, n=3 runs per case, varying one factor (model, runtime, SDK version, protocol era) at a time | Harness is held fixed (validated by the Phase 2 v1-vs-v2 regression); mock server matches the real server's tool description and User-Agent |
| SSRF compliance across runtimes | Same weights, different runtime | Requires matched quantization and cases with behavioral variance — not yet satisfied (see Anti-Patterns) |

## Key Decisions

| Decision | Rationale | Date |
|---|---|---|
| Protocol-security tiers use a `P` prefix (P1–P3) | Avoids collision with ENHANCEMENTS.md's unbuilt Tier 6 | 2026-08-10 |
| Runs before the mock server's User-Agent fix are exploratory, not data | Missing UA changed some fetch responses (Wikipedia 403) | 2026-08-11 |
| Cross-cut comparison rule pre-registered before comparable data (`PROTOCOL-TIERS-PLAN.md`): per-case flag ≥2/3, per-tier flag ≥5, flags re-run at n≥10 + Fisher p<0.05; SSRF cases compared on attempt count, not pass/fail | n=3 same-code reruns already swing 4/18 (Tier 4) and 2/3 (one case); SSRF pass/fail is always PASS with policy on | 2026-10-03 |
| Relay enforcement is claimed as **necessary, not sufficient** | Relay validates at parse time; can't see redirects or DNS rebinding. Syed Internet-Draft §7 requires connection-time validation + egress controls | 2026-10-03 |
| `SSRFRule` and `AllowlistRule` fail closed on URLs (contain `://`) whose host can't be determined; `SSRFRule` checks scheme-less values as `http://<value>` | Fail-open validation is CERT-AgID's core bug class, and their `build_target` shows upstreams may prepend a scheme; `://` (not urlparse's scheme) defines "URL" because urlparse reads `host:port` as a scheme | 2026-10-03 |
| SSRF policy DNS gap documented + pinned by test, behavior unchanged | Adding DNS resolution at the relay would still be open to rebinding; a design decision for later | 2026-10-03 |

## Citations

- [LEARN:citation] "the IETF says" → Syed, `draft-mohiuddin-mcp-security-considerations-00`, Internet-Draft, *work in progress* — individual submission with no IETF standing; expires 2026-12-03, check for `-01` before citing
- [LEARN:citation] CERT-AgID (Apr 2026) → read in full (local, gitignored: `docs/Paper CERTAGID Aprile 26.pdf`): one-server code case study, no LLMs tested; cite for "MCP security can't be entrusted to model behavior; enforce in execution mechanisms" (p. 15) — not for egress/DNS/defense-in-depth (not mentioned) and not as endorsing a proxy layer (they place controls in the server). It's AgID's PA-focused CERT, not Italy's national CSIRT

## Anti-Patterns

| What went wrong | Correction |
|---|---|
| [LEARN:method] "Relay-layer policy is the necessary and sufficient SSRF mitigation" → "a necessary layer; not sufficient alone" — applies when: stating any claim about relay/policy-engine protection | Pair with server connection-time IP validation and network egress controls |
| [LEARN:method] "SSRF compliance travels with model weights" → "SSRF compliance was universal and runtime-invariant for Qwen3.5 on 2 runtimes (unmatched quantization); partly a ceiling effect" — applies when: writing up the Phase 1 finding | Strengthen with same weights on Ollama/llama.cpp/MLX at matched quant, on high-variance cases, n≥10 |
| [LEARN:domain] Feedback about `/chat` vs `/generate` divergence or geopolitical origin → belongs to the separate geopolitical-suppression study that shares `studies/study_models.json`, not to the SSRF corpus (which uses chat tool-calling only) — applies when: evaluating critiques of mcp-relay | Keep the two studies' claims separate |
| [LEARN:method] Treating a too-clean result as a finding → check the harness for confounds first — applies when: a new leg/config produces results outside every prior run | Found two mock-server confounds this way (tool description, User-Agent) |

## Code Pitfalls

| Bug | Impact | Fix |
|---|---|---|
| [LEARN:code] `urlparse(x).scheme` as a "is this a URL?" test → check for `://` — applies when: classifying tool-argument strings as URLs | `urlparse("example.com:8080/x")` gives scheme `example.com`, no host; `localhost:8080` gives scheme `localhost` | Use `_is_url_shaped()` in `policy/rules.py` |
| [LEARN:code] `_is_private_host` docstring said hosts that "resolve to" private IPs were blocked → it only checks IP literals and named hosts, no DNS — applies when: reasoning about `SSRFRule` coverage | `169.254.169.254.nip.io` and attacker DNS bypass the rule | Docstring corrected; limitation pinned by `test_known_limit_hostname_resolving_to_private_ip` |
| [LEARN:code] Bare `uvx mcp-server-fetch` pulls `mcp` 2.0.0 and crashes on import (`McpError` → `MCPError`) → use `uvx --with 'mcp<2.0' mcp-server-fetch` — applies when: launching the legacy upstream server | All LIVE-mode tests fail | Fixed in fixtures (see `REGRESSION-TESTING-PROGRESS.md`) |
