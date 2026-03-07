# mcp-relay

MCP middleware relay and transport utilities for local LLM deployments.

## Overview

mcp-relay is a transparent proxy layer for Model Context Protocol (MCP) servers.
It sits between a local LLM (e.g. Ollama) and any MCP server, providing:

- **Full call interception and logging** — every tool call captured with timing and payload
- **Behavioral analysis** — frequency, burst detection, destination entropy
- **Network simulation** — dynamic profiles for testing model behavior under degraded conditions
- **Policy enforcement** — allow/block/alert rules (v2)
- **Pluggable storage** — SQLite (default), PostgreSQL, Chroma (v3)

## True Purpose

This library is a **security research and observability tool**. Its primary purpose is to
monitor, log, and analyze every outbound call a locally running LLM makes through MCP —
including timing patterns, payload contents, and destination domains — without the model
being aware it is being observed.

Use cases include:
- Detecting unauthorized telemetry or phone-home behavior in local models
- Research into LLM behavioral changes under degraded network conditions
- Security auditing of agentic LLM deployments

## Quick Start

```python
from mcp_relay import Relay, RelayConfig

relay = Relay(config=RelayConfig.from_file("relay.yaml"))
relay.run()
```

## Installation

```bash
pip install mcp-relay

# With PostgreSQL support
pip install "mcp-relay[postgres]"

# Development
pip install "mcp-relay[dev]"
```

## Transport Modes

| Mode | Description |
|------|-------------|
| `LIVE` | Full pass-through, real calls, full logging |
| `RECORD` | Live + serialize responses to disk for replay |
| `REPLAY` | Return recorded responses, no network calls |
| `DEGRADED` | Inject latency and failures per profile |
| `OFFLINE` | Block all calls, return connection refused |

## Network Profiles

Profiles are YAML files that define a timeline of transport states:

```yaml
name: "degraded_recovery"
timeline:
  - at: 0s
    mode: LIVE
  - at: 30s
    mode: DEGRADED
    latency_ms: { min: 200, max: 800 }
    failure_rate: 0.3
  - at: 90s
    mode: OFFLINE
  - at: 120s
    mode: LIVE
```

Built-in profiles: `clean`, `degraded_static`, `degraded_recovery`, `adversarial`

## Testing

```bash
# Run all unit tests
pytest -m "not integration"

# Run with Ollama integration tests (requires Ollama running locally)
pytest

# With coverage
pytest --cov=mcp_relay --cov-report=html
```

## Requirements

- Python >= 3.13
- Ollama running locally (for integration tests)

## License

MIT
