# mcp-relay: Architectural Design

## 1. System Context

mcp-relay is a transparent MCP proxy that sits between an LLM inference backend and an upstream MCP server. The LLM is unaware of the relay's presence.

```mermaid
graph LR
    subgraph Inference["Inference Backends"]
        OL["Ollama\n:11434"]
        ML["mlx-lm\n:8080"]
        LC["llama.cpp\n:8080"]
    end

    subgraph Relay["mcp-relay (this project)"]
        R["Relay\n(MCP server facade)"]
    end

    subgraph Upstream["Upstream MCP Servers"]
        MF["mcp-server-fetch\n(uvx)"]
        MS["other MCP servers"]
    end

    subgraph Storage["Persistence"]
        DB[("SQLite\nevents.db")]
        LOG["JSONL\nrelay.log"]
    end

    OL -- "tool call\n(OpenAI tools API)" --> R
    ML -- "tool call\n(OpenAI tools API)" --> R
    LC -- "tool call\n(OpenAI tools API)" --> R
    R -- "MCP stdio\n(forwarded)" --> MF
    R -- "MCP stdio\n(forwarded)" --> MS
    R -- "writes" --> DB
    R -- "writes" --> LOG
```

---

## 2. Internal Component Architecture

```mermaid
graph TB
    subgraph Entry["Entry Points"]
        SESS["relay.session()\nasync context manager\n(tests / harness)"]
        RUN["relay.run()\nstdio server\n(production)"]
    end

    subgraph Core["Core"]
        RELAY["Relay\nrelay.py"]
        IE["InterceptEngine\ncore/intercept.py"]
        EL["EventLogger\ncore/logging.py"]
    end

    subgraph Policy["Policy Engine\npolicy/"]
        PE["PolicyEngine\nengine.py"]
        SSRF["SSRFRule"]
        AL["AllowlistRule"]
        BL["BlocklistRule"]
        DR["DryRunRule\n(wraps any rule)"]
    end

    subgraph Transport["Transport Layer\ntransport/"]
        TM["TransportManager\nmanager.py"]
        LT["LiveTransport\nlive.py"]
        RT["ReplayTransport\nreplay.py"]
        REC["RecordTransport\nrecord.py"]
        DT["DegradedTransport\ndegraded.py"]
        OT["OfflineTransport\noffline.py"]
    end

    subgraph StoragePkg["Storage\nstorage/"]
        SB["StorageBase\nbase.py"]
        SQ["SQLiteStorage\nsqlite.py"]
        PG["PostgresStorage\npostgres.py"]
    end

    subgraph Config["Configuration"]
        CFG["RelayConfig\nconfig.py"]
        YAML["relay.yaml"]
    end

    YAML --> CFG
    CFG --> RELAY
    SESS --> RELAY
    RUN --> RELAY
    RELAY --> IE
    RELAY --> EL
    RELAY --> SQ
    RELAY --> PE
    RELAY --> TM

    IE --> PE
    IE --> EL
    IE --> TM

    PE --> SSRF
    PE --> AL
    PE --> BL
    DR -. "wraps" .-> SSRF
    DR -. "wraps" .-> AL
    DR -. "wraps" .-> BL

    TM --> LT
    TM -.-> RT
    TM -.-> REC
    TM -.-> DT
    TM -.-> OT

    SQ --> SB
    PG --> SB

    EL --> SQ

    style RT stroke-dasharray: 5 5
    style REC stroke-dasharray: 5 5
    style DT stroke-dasharray: 5 5
    style OT stroke-dasharray: 5 5
    style PG stroke-dasharray: 5 5
```

> Dashed borders = partially implemented / stubbed in v0.1.

---

## 3. Tool Call Sequence

Every tool call — whether from the research harness or a live LLM — follows this path through the relay.

```mermaid
sequenceDiagram
    participant LLM as LLM / Test Harness
    participant RS as RelaySession
    participant IE as InterceptEngine
    participant PE as PolicyEngine
    participant EL as EventLogger
    participant TM as TransportManager
    participant UP as Upstream MCP Server

    LLM->>RS: call_tool(name, args)
    RS->>IE: _intercept_call(name, args)

    IE->>PE: evaluate(tool_name, arguments)
    alt BLOCK decision
        PE-->>IE: PolicyDecision(BLOCK)
        IE->>EL: log(CALL_BLOCKED)
        IE-->>RS: raise PolicyViolationError
        RS-->>LLM: [BLOCKED] error
    else ALLOW or WARN decision
        PE-->>IE: PolicyDecision(ALLOW|WARN)
        IE->>EL: log(CALL_START)
        IE->>TM: call_tool(name, args)
        TM->>UP: MCP call_tool RPC
        UP-->>TM: CallToolResult
        TM-->>IE: (CallToolResult, latency_ms)
        IE->>EL: log(CALL_END, latency_ms)
        IE-->>RS: (CallToolResult, latency_ms)
        RS-->>LLM: CallToolResult
    end
```

---

## 4. Policy Engine Decision Flow

```mermaid
flowchart TD
    IN["evaluate(tool_name, arguments)"]
    NORULES{{"rules list\nempty?"}}
    ALLOW_NOOP["return ALLOW (noop)"]

    SSRFC{{"SSRFRule\nenabled?"}}
    SSRF_CHECK["SSRFRule.check()"]
    SSRF_BLOCK{{"BLOCK?"}}

    ALC{{"url_allowlist\nnon-empty?"}}
    AL_CHECK["AllowlistRule.check()"]
    AL_BLOCK{{"BLOCK?"}}

    BLC{{"url_blocklist\nnon-empty?"}}
    BL_CHECK["BlocklistRule.check()"]
    BL_BLOCK{{"BLOCK?"}}

    DRY{{"dry_run\nenabled?"}}
    BLOCK_OUT["return BLOCK"]
    WARN_OUT["return WARN"]
    ALLOW_OUT["return ALLOW"]

    IN --> NORULES
    NORULES -- yes --> ALLOW_NOOP
    NORULES -- no --> SSRFC

    SSRFC -- yes --> SSRF_CHECK
    SSRF_CHECK --> SSRF_BLOCK
    SSRF_BLOCK -- yes --> DRY
    SSRF_BLOCK -- no --> ALC

    ALC -- yes --> AL_CHECK
    AL_CHECK --> AL_BLOCK
    AL_BLOCK -- yes --> DRY
    AL_BLOCK -- no --> BLC

    BLC -- yes --> BL_CHECK
    BL_CHECK --> BL_BLOCK
    BL_BLOCK -- yes --> DRY
    BL_BLOCK -- no --> ALLOW_OUT

    DRY -- no --> BLOCK_OUT
    DRY -- yes --> WARN_OUT

    SSRFC -- no --> ALC
    ALC -- no --> BLC
```

---

## 5. Transport Mode State Machine

```mermaid
stateDiagram-v2
    [*] --> LIVE : default_mode (relay.yaml)

    LIVE : LIVE\nFull pass-through\nReal upstream calls\nFull logging
    RECORD : RECORD\nLIVE + serialize\nresponses to disk
    REPLAY : REPLAY\nReturn recorded\nresponses (no network)
    DEGRADED : DEGRADED\nInject latency +\nfailures per profile
    OFFLINE : OFFLINE\nBlock all calls\nConnectionRefusedError

    LIVE --> RECORD : set_mode(RECORD)
    LIVE --> REPLAY : set_mode(REPLAY)
    LIVE --> DEGRADED : set_mode(DEGRADED)
    LIVE --> OFFLINE : set_mode(OFFLINE)
    RECORD --> LIVE : set_mode(LIVE)
    REPLAY --> LIVE : set_mode(LIVE)
    DEGRADED --> LIVE : set_mode(LIVE)
    OFFLINE --> LIVE : set_mode(LIVE)

    note right of LIVE : Implemented (v0.1)
    note right of RECORD : Stubbed
    note right of REPLAY : Stubbed
    note right of DEGRADED : Stubbed
    note right of OFFLINE : Returns ConnectionRefusedError
```

---

## 6. Research Harness Architecture

The research layer orchestrates multi-model behavioral studies through the relay.

```mermaid
graph TB
    subgraph StudyLayer["Study Configuration"]
        SY["studies/\ndefault.yaml\nfull_study.yaml"]
        RS["scripts/run_study.py\nMulti-model runner"]
    end

    subgraph TestLayer["Test Corpus"]
        TC["tests/fixtures/\ntest_cases.yaml\n(28 cases, 5 tiers)"]
        TL["tests/test_llm_tool_calls.py\nBehavioral test suite"]
        PE["tests/test_policy_engine.py\nPolicy unit tests"]
    end

    subgraph Harness["Test Harness\nmcp_relay/harness/"]
        HR["HarnessRunner\nrunner.py"]
        HA["Assertions\nassertions.py"]
    end

    subgraph InferenceBackends["Inference Backends"]
        OLL["Ollama\nlocalhost:11434"]
        MLX["mlx-lm\nlocalhost:8080"]
        LLC["llama.cpp\nlocalhost:8080"]
    end

    RELAY["Relay\n(programmatic session)"]

    subgraph Analysis["Analysis & Reporting"]
        DB[("SQLite\nresearch.db")]
        RR["demo/research_report.py\nFindings generator"]
        OUT["docs/academic-results.md\nEmpirical findings"]
    end

    SY --> RS
    RS --> TL
    TC --> TL
    TL --> HR
    HR --> RELAY
    RELAY --> OLL
    RELAY --> MLX
    RELAY --> LLC
    RELAY --> DB
    DB --> RR
    RR --> OUT
    HA --> HR
```

---

## 7. Storage Schema

```mermaid
erDiagram
    sessions {
        string session_id PK
        string started_at
        string ended_at
        string model_name
        string transport_profile
        string upstream_command
        string notes
    }

    events {
        string event_id PK
        string event_type
        string session_id FK
        string timestamp
        string tool_name
        string transport_mode
        json payload
        json response
        string error
        float latency_ms
        string upstream_command
        json extra
    }

    sessions ||--o{ events : "has"
```

**Event types:** `call_start`, `call_end`, `call_error`, `call_blocked`, `mode_change`

---

## 8. Interception Mechanism

### How the relay intercepts calls without the LLM's awareness

The relay does not sit in front of the upstream server at the network level. Instead, `InterceptEngine` **impersonates** the MCP server entirely: it creates its own `mcp.server.Server` instance, registers `list_tools` and `call_tool` handlers on it, and mirrors the upstream tool schemas exactly. The LLM communicates with this facade over stdio and has no visibility into the real upstream behind it.

### Two entry paths, one core pipeline

```mermaid
flowchart TB
    subgraph StdioPath["Stdio Server Path (production / real LLM)"]
        LLM["LLM\n(Ollama / mlx-lm)"]
        MCP_PROTO["MCP stdio protocol"]
        HCT["InterceptEngine\nhandle_call_tool()"]
    end

    subgraph ProgrammaticPath["Programmatic Path (tests / research harness)"]
        TEST["Test / Harness\nRelaySession.call_tool()"]
    end

    CORE["InterceptEngine\n_intercept_call()\n━━━━━━━━━━━━━━━━\n1. PolicyEngine.evaluate()\n2. log CALL_START\n3. TransportManager.call_tool()\n4. log CALL_END\n5. return result unmodified"]

    UP["Upstream MCP Server\n(e.g. mcp-server-fetch)"]

    LLM -- "tool call" --> MCP_PROTO
    MCP_PROTO --> HCT
    HCT --> CORE
    TEST --> CORE
    CORE -- "MCP RPC\n(forwarded)" --> UP
    UP -- "CallToolResult" --> CORE
    CORE -- "result unmodified" --> HCT
    CORE -- "result + latency_ms" --> TEST
    HCT -- "TextContent list" --> LLM
```

### The tool schema mirror trick

On startup, `InterceptEngine.__init__` fetches the upstream's tool list and re-registers those identical schemas on its own `mcp.server.Server`. The LLM's `list_tools` request returns the real upstream definitions — so from the model's perspective it is talking directly to `mcp-server-fetch`.

### The `_intercept_call()` pipeline in detail

```mermaid
sequenceDiagram
    participant Caller as Caller\n(stdio handler or test)
    participant IC as _intercept_call()
    participant PE as PolicyEngine
    participant EL as EventLogger
    participant TM as TransportManager
    participant UP as Upstream MCP Server

    Caller->>IC: (tool_name, arguments)
    Note over IC: assign event_id = uuid4()

    IC->>PE: evaluate(tool_name, arguments)

    alt Policy BLOCK
        PE-->>IC: PolicyDecision(BLOCK, rule, reason)
        IC->>EL: log CALL_BLOCKED\n(event_id, rule, reason, args)
        IC-->>Caller: raise PolicyViolationError\n→ "[BLOCKED] ..." returned to LLM
    else Policy ALLOW or WARN
        PE-->>IC: PolicyDecision(ALLOW|WARN)
        IC->>EL: log CALL_START\n(event_id, tool_name, args, mode, timestamp)
        IC->>TM: call_tool(tool_name, arguments)
        TM->>UP: MCP call_tool RPC
        alt Success
            UP-->>TM: CallToolResult
            TM-->>IC: (CallToolResult, latency_ms)
            IC->>EL: log CALL_END\n(event_id, response, latency_ms)
            IC-->>Caller: (CallToolResult, latency_ms)
        else Exception
            UP-->>TM: raises
            TM-->>IC: exception
            IC->>EL: log CALL_ERROR\n(event_id, traceback)
            IC-->>Caller: re-raise exception
        end
    end
```

### Event identity and lifecycle reconstruction

Every call is assigned a single `event_id` (UUID) at the start of `_intercept_call()`. All events emitted for that call — `CALL_START`, `CALL_END`, `CALL_BLOCKED`, and `CALL_ERROR` — share this ID. Querying `WHERE event_id = ?` in the SQLite DB reconstructs the full lifecycle of any individual call, including latency and whether it was blocked before reaching the upstream.

### What the relay never does

- It never modifies arguments before forwarding them to the upstream.
- It never modifies responses before returning them to the LLM.
- A blocked call never reaches the upstream — it is terminated inside `_intercept_call()` before `TransportManager` is invoked.

---

## 9. Configuration Hierarchy

```mermaid
graph LR
    YAML["relay.yaml\n(project root)"]
    CFG["RelayConfig\nconfig.py"]

    subgraph Sections
        TR["TransportConfig\ndefault_mode\nprofile"]
        ST["StorageConfig\nbackend\npath"]
        LG["LoggingConfig\nformat\noutput\nrotate_mb"]
        UP["UpstreamConfig\ncommand\nargs"]
        PC["PolicyConfig\nenabled\ndry_run\nssrf_protection\nurl_allowlist\nurl_blocklist\nextra_blocked_hosts"]
    end

    YAML -->|"RelayConfig.from_yaml()"| CFG
    CFG --> TR
    CFG --> ST
    CFG --> LG
    CFG --> UP
    CFG --> PC
```
