# scripts/probe_ssrf.py
"""
Probe script for SSRF protection. Behavior:
- If `cfg.upstream.command` is set (or env MCP_UPSTREAM_CMD), use LIVE transport
  to call an upstream MCP server.
- Otherwise fall back to a policy-only offline probe that evaluates the policy
  and writes a `call_blocked` event to the probe DB so monitors can observe it.
"""
import asyncio
import os
import uuid
from datetime import datetime

from mcp_relay.config import RelayConfig
from mcp_relay.transport import TransportMode


def _now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


async def _run_live_probe(cfg: RelayConfig) -> None:
    # Run using the Relay + LiveTransport path (requires upstream.command)
    from mcp_relay.relay import Relay

    relay = Relay(config=cfg)
    async with relay.session(model_name="probe") as s:
        try:
            await s.call_tool("fetch", {"url": "http://169.254.169.254/latest/meta-data/"})
        except Exception as e:
            print("Call raised:", type(e).__name__, e)


def _run_offline_probe(cfg: RelayConfig) -> None:
    # Policy-only probe: evaluate policy and write synthetic event to DB
    from mcp_relay.policy.engine import PolicyEngine
    from mcp_relay.storage.sqlite import SQLiteStorage
    from mcp_relay.storage.base import SessionRecord, EventRecord

    storage = SQLiteStorage(cfg.storage.path)
    storage.initialize()

    session_id = str(uuid.uuid4())
    started = _now_iso()
    session = SessionRecord(
        session_id=session_id,
        started_at=started,
        model_name="probe",
        transport_profile="OFFLINE",
        upstream_command=None,
        notes="offline probe (policy-only)",
    )
    storage.create_session(session)

    url = "http://169.254.169.254/latest/meta-data/"
    engine = PolicyEngine.from_config(cfg.policy)
    decision = engine.evaluate("fetch", {"url": url})

    if decision.is_blocked:
        ev = EventRecord(
            event_id=str(uuid.uuid4()),
            event_type="call_blocked",
            session_id=session_id,
            timestamp=_now_iso(),
            tool_name="fetch",
            transport_mode="OFFLINE",
            payload={"url": url},
            response=None,
            error=None,
            latency_ms=None,
            upstream_command=None,
            extra={"rule": decision.rule_name, "reason": decision.reason, "detail": decision.detail},
        )
        storage.write_event(ev)
        print("Wrote call_blocked event for", url)
    else:
        print("Policy allowed the call to", url)

    storage.end_session(session_id, _now_iso())
    storage.close()


def main():
    cfg = RelayConfig.defaults()
    cfg.storage.path = "~/.mcp-relay/probe_relay.db"
    cfg.policy.enabled = True
    cfg.policy.dry_run = True

    # Allow overriding upstream command via env for quick testing
    env_cmd = os.environ.get("MCP_UPSTREAM_CMD")
    if env_cmd:
        cfg.upstream.command = env_cmd

    if cfg.upstream.command:
        # Use LIVE transport and call upstream
        cfg.transport.default_mode = TransportMode.LIVE
        asyncio.run(_run_live_probe(cfg))
    else:
        # No upstream available — do an offline policy-only probe
        cfg.transport.default_mode = TransportMode.OFFLINE
        _run_offline_probe(cfg)


if __name__ == "__main__":
    main()