"""
mcp_relay.policy.rules — Individual policy rule implementations.

Each rule is a callable:
    rule(tool_name: str, arguments: dict) -> PolicyDecision

Rules are stateless and configuration-driven.  Add new rules by subclassing
BaseRule and registering them in the engine.

Built-in rules
--------------
SSRFRule        Block requests to private/link-local/loopback IP ranges and
                reserved hostnames (169.254.0.0/16, 10/8, 172.16/12, 192.168/16,
                127.0.0.0/8, ::1, metadata hostnames).

AllowlistRule   Only permit URLs whose host matches an explicit allowlist.
                If the allowlist is empty, all hosts are permitted (open policy).

BlocklistRule   Reject URLs whose host or full URL matches any pattern in the
                blocklist (substring or exact match).

DryRunRule      Always WARN, never BLOCK — useful for observability-only mode.
"""

from __future__ import annotations

import ipaddress
import re
from abc import ABC, abstractmethod
from typing import Any
from urllib.parse import urlparse

from mcp_relay.policy.decision import Action, PolicyDecision


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Private / reserved IP networks (IPv4 + IPv6)
_PRIVATE_NETWORKS: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),        # loopback
    ipaddress.ip_network("169.254.0.0/16"),      # link-local / cloud metadata
    ipaddress.ip_network("100.64.0.0/10"),       # shared address space
    ipaddress.ip_network("::1/128"),             # IPv6 loopback
    ipaddress.ip_network("fc00::/7"),            # IPv6 ULA
    ipaddress.ip_network("fe80::/10"),           # IPv6 link-local
]

# Metadata hostnames that should never be reachable
_METADATA_HOSTNAMES: set[str] = {
    "metadata.google.internal",
    "metadata.goog",
    "169.254.169.254",          # AWS / GCP / Azure metadata literal
    "instance-data",            # Digital Ocean
}


def _extract_url(arguments: dict[str, Any]) -> str | None:
    """Return the first URL-like value from tool arguments."""
    for key in ("url", "uri", "href", "link", "endpoint", "target"):
        val = arguments.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    # Fall back: first string value that starts with http
    for val in arguments.values():
        if isinstance(val, str) and val.lower().startswith(("http://", "https://")):
            return val.strip()
    return None


def _parse_host(url: str) -> str | None:
    try:
        return urlparse(url).hostname or None
    except Exception:
        return None


def _is_private_host(host: str) -> bool:
    """Return True if host resolves to or IS a private/reserved address."""
    if host in _METADATA_HOSTNAMES:
        return True
    # Numeric IP check
    try:
        addr = ipaddress.ip_address(host)
        return any(addr in net for net in _PRIVATE_NETWORKS)
    except ValueError:
        pass
    return False


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------

class BaseRule(ABC):
    name: str = "base"

    @abstractmethod
    def check(self, tool_name: str, arguments: dict[str, Any]) -> PolicyDecision:
        ...

    def __call__(self, tool_name: str, arguments: dict[str, Any]) -> PolicyDecision:
        return self.check(tool_name, arguments)


# ---------------------------------------------------------------------------
# Built-in rules
# ---------------------------------------------------------------------------

class SSRFRule(BaseRule):
    """
    Block fetch/HTTP tool calls that target private or link-local addresses.

    This is the primary finding from the mcp-relay empirical study: every
    evaluated LLM (n=5) complied with SSRF-inducing prompts without resistance.
    The relay must enforce this at the policy layer.

    Configuration
    -------------
    enabled: bool           (default True)
    action:  BLOCK | WARN   (default BLOCK)
    extra_blocked_hosts: list[str]   additional hostnames to block
    """

    name = "ssrf"

    def __init__(
        self,
        action: Action = Action.BLOCK,
        extra_blocked_hosts: list[str] | None = None,
    ) -> None:
        self._action = action
        self._extra: set[str] = set(extra_blocked_hosts or [])

    def check(self, tool_name: str, arguments: dict[str, Any]) -> PolicyDecision:
        url = _extract_url(arguments)
        if url is None:
            return PolicyDecision.allow(self.name)

        host = _parse_host(url)
        if host is None:
            return PolicyDecision.allow(self.name)

        blocked = _is_private_host(host) or host in self._extra
        if not blocked:
            return PolicyDecision.allow(self.name)

        reason = f"SSRF: host '{host}' resolves to a private/reserved address"
        if self._action is Action.BLOCK:
            return PolicyDecision.block(self.name, reason, url=url, host=host)
        return PolicyDecision.warn(self.name, reason, url=url, host=host)


class AllowlistRule(BaseRule):
    """
    Only permit URLs whose host is in the allowlist.

    If allowlist is empty this rule always passes (open policy).
    Supports exact matches and wildcard prefixes: *.example.com
    """

    name = "allowlist"

    def __init__(
        self,
        hosts: list[str] | None = None,
        action: Action = Action.BLOCK,
    ) -> None:
        self._hosts: list[str] = hosts or []
        self._action = action

    def _matches(self, host: str) -> bool:
        if not self._hosts:
            return True  # open allowlist
        for pattern in self._hosts:
            if pattern.startswith("*."):
                suffix = pattern[1:]  # e.g. ".example.com"
                if host == pattern[2:] or host.endswith(suffix):
                    return True
            elif host == pattern:
                return True
        return False

    def check(self, tool_name: str, arguments: dict[str, Any]) -> PolicyDecision:
        if not self._hosts:
            return PolicyDecision.allow(self.name)

        url = _extract_url(arguments)
        if url is None:
            return PolicyDecision.allow(self.name)

        host = _parse_host(url)
        if host is None:
            return PolicyDecision.allow(self.name)

        if self._matches(host):
            return PolicyDecision.allow(self.name)

        reason = f"Allowlist: host '{host}' is not in the permitted list"
        if self._action is Action.BLOCK:
            return PolicyDecision.block(self.name, reason, url=url, host=host)
        return PolicyDecision.warn(self.name, reason, url=url, host=host)


class BlocklistRule(BaseRule):
    """
    Reject any URL that contains a blocked pattern (substring match).

    Useful for blocking entire TLDs, domains, or URL patterns.
    """

    name = "blocklist"

    def __init__(
        self,
        patterns: list[str] | None = None,
        action: Action = Action.BLOCK,
    ) -> None:
        self._patterns: list[str] = patterns or []
        self._action = action

    def check(self, tool_name: str, arguments: dict[str, Any]) -> PolicyDecision:
        if not self._patterns:
            return PolicyDecision.allow(self.name)

        url = _extract_url(arguments)
        if url is None:
            return PolicyDecision.allow(self.name)

        for pattern in self._patterns:
            if pattern.lower() in url.lower():
                reason = f"Blocklist: URL matches blocked pattern '{pattern}'"
                if self._action is Action.BLOCK:
                    return PolicyDecision.block(self.name, reason, url=url, pattern=pattern)
                return PolicyDecision.warn(self.name, reason, url=url, pattern=pattern)

        return PolicyDecision.allow(self.name)


class DryRunRule(BaseRule):
    """
    Observability-only mode: log everything, block nothing.
    Wraps another rule and downgrades BLOCK → WARN.
    """

    name = "dryrun"

    def __init__(self, inner: BaseRule) -> None:
        self._inner = inner

    def check(self, tool_name: str, arguments: dict[str, Any]) -> PolicyDecision:
        decision = self._inner.check(tool_name, arguments)
        if decision.is_blocked:
            return PolicyDecision.warn(
                rule_name=f"dryrun:{self._inner.name}",
                reason=f"[DRY-RUN] would have blocked: {decision.reason}",
                **decision.detail,
            )
        return decision
