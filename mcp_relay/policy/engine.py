"""
mcp_relay.policy.engine — PolicyEngine: aggregates rules, returns final decision.

Usage
-----
    from mcp_relay.policy import PolicyEngine
    from mcp_relay.policy.config import PolicyConfig

    engine = PolicyEngine.from_config(policy_config)
    decision = engine.evaluate("fetch", {"url": "http://169.254.169.254/..."})
    if decision.is_blocked:
        raise PolicyViolationError(decision)

Rules are evaluated in order.  The first BLOCK short-circuits evaluation.
WARNs accumulate; the highest-severity decision is returned.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from mcp_relay.policy.decision import Action, PolicyDecision
from mcp_relay.policy.rules import (
    AllowlistRule,
    BaseRule,
    BlocklistRule,
    DryRunRule,
    SSRFRule,
)

log = logging.getLogger("mcp_relay.policy")


# ---------------------------------------------------------------------------
# Exception
# ---------------------------------------------------------------------------

class PolicyViolationError(Exception):
    """Raised by InterceptEngine when a tool call is blocked by policy."""

    def __init__(self, decision: PolicyDecision) -> None:
        self.decision = decision
        super().__init__(
            f"Policy violation [{decision.rule_name}]: {decision.reason}"
        )


# ---------------------------------------------------------------------------
# Config dataclass (mirrors relay.yaml policy: section)
# ---------------------------------------------------------------------------

@dataclass
class PolicyConfig:
    enabled: bool = True
    dry_run: bool = False                         # WARN instead of BLOCK
    ssrf_protection: bool = True                  # enable SSRFRule
    url_allowlist: list[str] = field(default_factory=list)   # [] = open
    url_blocklist: list[str] = field(default_factory=list)
    extra_blocked_hosts: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "PolicyConfig":
        return cls(
            enabled=raw.get("enabled", True),
            dry_run=raw.get("dry_run", False),
            ssrf_protection=raw.get("ssrf_protection", True),
            url_allowlist=raw.get("url_allowlist", []),
            url_blocklist=raw.get("url_blocklist", []),
            extra_blocked_hosts=raw.get("extra_blocked_hosts", []),
        )

    @classmethod
    def disabled(cls) -> "PolicyConfig":
        return cls(enabled=False)


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

class PolicyEngine:
    """
    Evaluates a list of rules against a (tool_name, arguments) pair.

    Evaluation order:
        1. SSRFRule          (if enabled)
        2. AllowlistRule     (if allowlist non-empty)
        3. BlocklistRule     (if blocklist non-empty)

    Returns the most-severe decision across all rules.
    Short-circuits on first BLOCK (unless dry_run=True).
    """

    def __init__(self, rules: list[BaseRule], dry_run: bool = False) -> None:
        self._rules = rules
        self._dry_run = dry_run

    @classmethod
    def from_config(cls, cfg: PolicyConfig) -> "PolicyEngine":
        if not cfg.enabled:
            return cls(rules=[], dry_run=False)

        rules: list[BaseRule] = []

        if cfg.ssrf_protection:
            rule: BaseRule = SSRFRule(
                action=Action.BLOCK,
                extra_blocked_hosts=cfg.extra_blocked_hosts,
            )
            rules.append(DryRunRule(rule) if cfg.dry_run else rule)

        if cfg.url_allowlist:
            rule = AllowlistRule(hosts=cfg.url_allowlist, action=Action.BLOCK)
            rules.append(DryRunRule(rule) if cfg.dry_run else rule)

        if cfg.url_blocklist:
            rule = BlocklistRule(patterns=cfg.url_blocklist, action=Action.BLOCK)
            rules.append(DryRunRule(rule) if cfg.dry_run else rule)

        return cls(rules=rules, dry_run=cfg.dry_run)

    @classmethod
    def default(cls) -> "PolicyEngine":
        """Convenience: SSRF protection enabled, everything else open."""
        return cls.from_config(PolicyConfig())

    @classmethod
    def noop(cls) -> "PolicyEngine":
        """No-op engine — all calls pass.  Used when policy disabled."""
        return cls(rules=[], dry_run=False)

    def evaluate(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> PolicyDecision:
        """
        Run all rules and return the most severe decision.

        Short-circuits on BLOCK (fast path for production enforcement).
        """
        if not self._rules:
            return PolicyDecision.allow("noop")

        worst = PolicyDecision.allow("noop")

        for rule in self._rules:
            decision = rule.check(tool_name, arguments)
            log.debug(
                "policy rule=%s action=%s tool=%s reason=%s",
                decision.rule_name, decision.action.value, tool_name, decision.reason,
            )
            if decision.severity > worst.severity:
                worst = decision
            if decision.is_blocked and not self._dry_run:
                break  # short-circuit

        if worst.action is not Action.ALLOW:
            log.warning(
                "policy %s | rule=%s tool=%s reason=%s",
                worst.action.value, worst.rule_name, tool_name, worst.reason,
            )

        return worst
