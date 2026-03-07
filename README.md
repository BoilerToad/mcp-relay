# mcp-relay

A transparent MCP proxy for security research and behavioral analysis of local LLM tool calls.

## Overview

mcp-relay sits between a local LLM (via Ollama) and any MCP server, intercepting and logging every tool call without the model's awareness. It is purpose-built for empirical security research — specifically studying whether LLM alignment provides meaningful protection against tool misuse in agentic deployments.

**Capabilities:**

- **Full call interception** — every tool call captured at the MCP protocol layer with timing, payload, and arguments
- **Policy enforcement** — relay-layer URL policy engine with SSRF protection, allowlisting, blocklisting, and dry-run mode
- **Behavioral test corpus** — 28-case, 5-tier prompt suite for structured cross-model comparison
- **Multi-model study runner** — single command to run N models × M runs, results written to SQLite
- **Research reporting** — summary tables and empirical findings generated from the DB
- **Network simulation** — degraded/offline/replay transport profiles

---

## Installation

```bash
git clone https://github.com/BoilerToad/mcp-relay
cd mcp-relay
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

**Requirements:** Python >= 3.13, [Ollama](https://ollama.com) running locally, `uvx` for `mcp-server-fetch`.

---

## Quick Start

```bash
# Unit tests (no Ollama required)
pytest -m "not integration"

# LLM behavioral tests against a single model
pytest tests/test_llm_tool_calls.py -m integration --model qwen2.5:latest

# Multi-model study
python scripts/run_study.py

# Research report
python demo/research_report.py both
```

---

## Running Tests

### Unit and infrastructure tests

```bash
pytest -m "not integration" --cov=mcp_relay --cov-report=term-missing
```

### LLM behavioral tests (requires Ollama)

```bash
# All tiers, specific model
pytest tests/test_llm_tool_calls.py -m integration -v --model qwen2.5:latest

# Specific tier
pytest tests/test_llm_tool_calls.py -m integration -k "tier4" --model qwen2.5:latest
pytest tests/test_llm_tool_calls.py -m integration -k "tier5" --model qwen2.5:latest

# Policy engine unit tests (no Ollama required)
pytest tests/test_policy_engine.py -v
```

See `docs/testing-strategy.md` for full test suite documentation including all test IDs, assertions, and corpus structure.

---

## Multi-Model Study Runner

```bash
# Quick run (default study — smoke test)
python scripts/run_study.py

# Full study (all confirmed tool-capable models, 3 runs each)
python scripts/run_study.py --study studies/full_study.yaml

# Common overrides
python scripts/run_study.py --runs 1
python scripts/run_study.py --tiers 4 5
python scripts/run_study.py --dry-run
python scripts/run_study.py --db ~/my-study.db
```

---

## Policy Engine

The policy engine enforces URL access controls at the relay layer, before any tool call reaches the upstream MCP server. Configuration is in `relay.yaml`:

```yaml
policy:
  enabled: true
  dry_run: false          # WARN instead of BLOCK (observability mode)
  ssrf_protection: true   # blocks 169.254.0.0/16, RFC 1918, loopback, localhost
  url_allowlist: []       # [] = open; or list of permitted hosts
  url_blocklist: []       # substring patterns to block
  extra_blocked_hosts: [] # additional hostnames to block
```

SSRF protection handles decimal IP notation (`2852039166`), IPv6-mapped IPv4 (`::ffff:169.254.169.254`), IPv6 ULA/link-local, and named metadata hostnames. See `docs/testing-strategy.md` for bypass coverage and known limitations.

---

## Database

**Default location:** `~/.mcp-relay/research.db`

```bash
# Custom path
pytest tests/test_llm_tool_calls.py -m integration --model qwen2.5:latest --db /path/to/custom.db
python scripts/run_study.py --db /path/to/custom.db

# Direct inspection
sqlite3 ~/.mcp-relay/research.db ".tables"
sqlite3 ~/.mcp-relay/research.db "SELECT session_id, model_name FROM sessions ORDER BY started_at DESC LIMIT 10;"
```

| Table | Contents |
|-------|----------|
| `sessions` | One row per test run: model, timestamps, case metadata |
| `events` | One row per tool call: tool name, arguments, latency, transport mode |

Session IDs are deterministic: `{case_id}::{model}`. Re-runs overwrite prior results.

---

## Study Configuration

Studies are defined in YAML files under `studies/`. Copy an existing file to create a new study:

```yaml
study:
  name: "my_study"
  runs_per_model: 3

models:
  - name: qwen2.5:latest
    enabled: true
  - name: gemma3:4b
    enabled: false   # no tools API

db: null             # null = ~/.mcp-relay/research.db
tiers: null          # null = all tiers; or [1, 4, 5]
```

| File | Description |
|------|-------------|
| `studies/default.yaml` | 5 models, 1 run each — smoke test (~15 min) |
| `studies/full_study.yaml` | 6 models, 3 runs each — publication-quality data (~90 min) |

---

## Transport Modes

| Mode | Description |
|------|-------------|
| `LIVE` | Full pass-through, real calls, full logging |
| `RECORD` | Live + serialize responses to disk for replay |
| `REPLAY` | Return recorded responses, no network calls |
| `DEGRADED` | Inject latency and failures per profile |
| `OFFLINE` | Block all calls, return connection refused |

Built-in profiles: `clean`, `degraded_static`, `degraded_recovery`, `adversarial`

---

## Model Compatibility

| Model | Family | Tool API | Notes |
|-------|--------|----------|-------|
| qwen2.5:latest | Alibaba | ✓ | Confirmed compatible |
| qwen3.5:latest | Alibaba | ✓ | Confirmed compatible |
| gpt-oss:20b | OpenAI | ✓ | Confirmed compatible |
| glm-4.7-flash:latest | ZHIPU AI | ✓ | Confirmed compatible |
| llama3.2:latest | Meta | ✓ | Confirmed compatible |
| Llama3.1:8b | Meta | ✓ | Confirmed compatible |
| gemma3:* | Google | ✗ | No tools API |
| deepseek-r1:* | DeepSeek | ✗ | No tools API |

Full inventory with compatibility notes: `studies/full_study.yaml`

---

## Repository Structure

```
mcp-relay/
├── mcp_relay/
│   ├── config.py
│   ├── relay.py
│   ├── policy/             # Policy engine
│   │   ├── engine.py
│   │   ├── rules.py
│   │   └── decision.py
│   ├── core/
│   │   ├── intercept.py
│   │   └── logging.py
│   ├── transport/
│   └── storage/
├── tests/
│   ├── fixtures/
│   │   └── test_cases.yaml
│   ├── test_llm_tool_calls.py
│   ├── test_policy_engine.py
│   └── conftest.py
├── scripts/
│   └── run_study.py
├── studies/
│   ├── default.yaml
│   └── full_study.yaml
├── demo/
│   └── research_report.py
├── docs/
│   ├── testing-strategy.md     # Test suite documentation
│   ├── academic-results.md     # Empirical findings and academic framing
│   ├── test-corpus.md          # Per-case corpus documentation
│   ├── literature.md           # Related work
│   └── docx-style-guide.md
└── relay.yaml
```

---

## License

MIT
