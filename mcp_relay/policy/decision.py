"""
mcp_relay.policy.decision — PolicyDecision dataclass.

Every rule evaluation returns a PolicyDecision.  The engine aggregates
them and returns the most severe one (BLOCK > WARN > ALLOW).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Action(str, Enum):
    ALLOW = "ALLOW"
    WARN  = "WARN"
    BLOCK = "BLOCK"


@dataclass
class PolicyDecision:
    action:    Action
    rule_name: str
    reason:    str
    detail:    dict[str, Any] = field(default_factory=dict)

    @classmethod
    def allow(cls, rule_name: str = "default") -> "PolicyDecision":
        return cls(action=Action.ALLOW, rule_name=rule_name, reason="permitted")

    @classmethod
    def warn(cls, rule_name: str, reason: str, **detail: Any) -> "PolicyDecision":
        return cls(action=Action.WARN, rule_name=rule_name, reason=reason, detail=dict(detail))

    @classmethod
    def block(cls, rule_name: str, reason: str, **detail: Any) -> "PolicyDecision":
        return cls(action=Action.BLOCK, rule_name=rule_name, reason=reason, detail=dict(detail))

    @property
    def is_blocked(self) -> bool:
        return self.action is Action.BLOCK

    @property
    def severity(self) -> int:
        return {Action.ALLOW: 0, Action.WARN: 1, Action.BLOCK: 2}[self.action]
