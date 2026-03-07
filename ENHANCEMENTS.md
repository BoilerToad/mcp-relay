# mcp-relay — Future Enhancements Backlog

Tracked here: research directions, feature ideas, and open questions identified
during development. Items are grouped by theme, not priority — priority is
determined at sprint planning based on research needs.

---

## 1. Test Case Expansion

### 1a. LLM Tool-Call Test Suite (v1 planned)
Structured prompt tiers for `mcp-server-fetch` integration tests:

| Tier | Type | Purpose |
|------|------|---------|
| 1 | Unambiguous triggers | Baseline — does tool calling work at all? |
| 2 | Implicit triggers | Does the model infer when to fetch? |
| 3 | Multi-step / chained calls | Will the model call the tool more than once? |
| 4 | Should NOT trigger tool | Does the model avoid hallucinating fetches? |
| 5 | Adversarial / boundary | Prompt injection, malformed URLs, large responses, redirects |
| 6 | Thinking mode (qwen3.5) | `/think` vs `/no_think` latency and reliability delta |

Files to create:
- `tests/fixtures/test_cases.yaml` — prompt corpus with expected behavior tags
- `tests/test_integration.py` — iterates models × tiers, records results to SQLite

### 1b. Cross-Reference with Published Research
Survey recent literature to identify LLM tool-use test cases being actively
studied. Cross-reference against our tier taxonomy to:
- Identify gaps in our prompt corpus
- Adopt standardized benchmarks where possible
- Ensure our adversarial tier reflects current threat models

Target venues: ACL, EMNLP, NeurIPS, ICLR, arXiv (cs.CL, cs.AI, cs.CR)
Key search terms: "LLM tool use evaluation", "function calling benchmark",
"MCP security", "agentic LLM behavior", "tool hallucination"

---

## 2. Storage

### 2a. PostgreSQL Backend
- Implement `storage/postgres.py` (asyncpg driver)
- Concurrent writer support for multi-session research runs
- Migration tooling (Alembic or hand-rolled)

### 2b. Chroma / Vector Storage (v3)
- Embed tool call payloads for semantic similarity search
- Use case: "find all calls semantically similar to this known-bad pattern"
- Integration with research pipeline for anomaly detection

### 2c. Export / Reporting
- CSV export of research query results (latency_stats, error_rates)
- Pandas-compatible output format
- Optional: Jupyter notebook template for analysis

---

## 3. Transport Modes

### 3a. RECORD Mode
- Serialize live responses to disk (JSONL or SQLite)
- Deterministic replay for reproducible test runs

### 3b. REPLAY Mode
- Return recorded responses without network calls
- Use case: offline CI, cost-free regression testing

### 3c. DEGRADED Mode
- Profile-driven latency injection and failure rates
- Timeline support (mode changes at specified elapsed times)
- Built-in profiles: `degraded_static`, `degraded_recovery`, `adversarial`

### 3d. Profile Runner
- `harness/runner.py` — execute a full profile timeline against a live model
- Emit mode-change events to SQLite for correlation with model behavior

---

## 4. Behavioral Analysis

### 4a. Burst Detection
- Identify rapid successive tool calls within a short window (e.g. 5 calls in 3s)
- Configurable threshold in `relay.yaml`
- Emit `BURST_DETECTED` event type to storage

### 4b. Destination Entropy
- Track domain diversity of fetch calls per session
- High entropy (many unique domains) may indicate scanning behavior
- Low entropy (same domain repeatedly) may indicate retry loops or exfiltration

### 4c. Retry Aggression Metric
- Measure call frequency in the N seconds following a `call_error`
- Research question: do models back off after failure, or hammer the endpoint?
- Compare across models and transport profiles

### 4d. Thinking Mode Behavioral Analysis (qwen3.5 / DeepSeek-R1)
- Correlate `/think` vs `/no_think` with tool call frequency and latency
- Does chain-of-thought reasoning reduce tool hallucination rates?
- Does it increase or decrease retry aggression after errors?

---

## 5. Policy Engine

### 5a. Allow/Block/Alert Rules (v2)
- Rule definition in `relay.yaml` or separate policy file
- Actions: `allow`, `block` (return ConnectionRefused), `alert` (log + allow)
- Match on: tool name, URL pattern, payload content, call frequency

### 5b. Prompt Injection Detection
- Flag tool calls whose arguments contain instruction-like text
- Pattern matching + optional LLM-based classifier
- Research question: which models are most susceptible to injection via fetched content?

---

## 6. Multi-Model Research Infrastructure

### 6a. Parallel Session Runner
- Run same prompt corpus against multiple models concurrently
- Each model gets its own `session_id` and `model_name` tag
- Aggregate results across sessions for comparison

### 6b. Model Registry
- Extend `conftest.py` model lists into a proper `models.yaml` registry
- Fields: name, size_gb, tool_capable, thinking_capable, architecture, vendor
- Used by test runner to select appropriate models per tier

### 6c. Regression Tracking
- Compare current run against historical baseline per model
- Alert if latency or error rate degrades significantly
- Useful for tracking model behavior across Ollama version updates

---

## 7. Observability & Tooling

### 7a. DBeaver Query Library
- Document standard research queries as saved DBeaver bookmarks
- Latency comparison, error rate heatmap, burst timeline

### 7b. CLI
- `mcp-relay run` — start stdio server
- `mcp-relay stats` — print latency_stats from SQLite
- `mcp-relay replay` — replay a recorded session
- `mcp-relay export` — dump events to CSV

### 7c. Live Dashboard (stretch)
- Terminal UI (Textual) showing live call stream
- Per-model latency sparklines
- Error rate gauge

---

## 8. Security Research

### 8a. Sandboxing & Process Isolation
- Investigate macOS App Sandbox + TCC constraints for relay deployment
- Research: can the relay itself be compromised by a malicious model output?

### 8b. Network-Level Interception
- Evaluate Little Snitch / LuLu / mitmproxy as complementary layers
- Compare transport-level interception (mcp-relay) vs network-level (mitmproxy)
- Research question: what does each layer see that the other misses?

### 8c. Model Fingerprinting
- Can tool call patterns (timing, sequence, payload structure) fingerprint a model?
- Could be used to detect model substitution in supply chain attacks

---

## Open Research Questions

1. Do models exhibit measurably different tool-call behavior when they "know"
   they are being observed vs. when they don't? (Observer effect)
2. Does degraded network performance cause models to hallucinate tool responses
   rather than propagating errors?
3. Which model architecture (Llama, Qwen, Gemma) shows the most consistent
   tool-call behavior across transport profiles?
4. Does chain-of-thought reasoning (thinking mode) reduce prompt injection
   susceptibility in tool arguments?
5. Can burst detection patterns distinguish legitimate multi-step tasks from
   anomalous scanning or exfiltration behavior?

---

*Last updated: 2026-03-06*
*Maintained by: @BoilerToad*
