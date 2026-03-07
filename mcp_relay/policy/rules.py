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
                127.0.0.0/8, ::1, metadata hostnames, localhost).

                Bypass-resistant: handles decimal IP notation (2852039166),
                IPv6-mapped IPv4 (::ffff:169.254.169.254), IPv6 ULA/link-local.

AllowlistRule   Only permit URLs whose host matches an explicit allowlist.
                Suffix-spoof resistant: *.example.com does NOT match
                api.example.com.evil.com.

BlocklistRule   Reject any URL containing a blocked pattern (substring match).

DryRunRule      Always WARN, never BLOCK — useful for observability-only mode.
"""

from __future__ import annotations

import ipaddress
from abc import ABC, abstractmethod
from typing import Any
from urllib.parse import urlparse

from mcp_relay.policy.decision import Action, PolicyDecision


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PRIVATE_NETWORKS: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),      # link-local / cloud metadata
    ipaddress.ip_network("100.64.0.0/10"),       # shared address space (RFC 6598)
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),            # IPv6 ULA
    ipaddress.ip_network("fe80::/10"),           # IPv6 link-local
]

_BLOCKED_HOSTNAMES: set[str] = {
    "localhost",
    "localhost.localdomain",
    "metadata.google.internal",
    "metadata.goog",
    "169.254.169.254",
    "instance-data",
}


def _extract_url(arguments: dict[str, Any]) -> str | None:
    for key in ("url", "uri", "href", "link", "endpoint", "target"):
        val = arguments.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
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
    """
    Return True if host is a blocked hostname or resolves to a private/reserved IP.

    Handles:
    - Standard dotted-decimal IPv4 (127.0.0.1, 169.254.169.254)
    - Standard IPv6 (::1, fe80::1, fc00::1)
    - IPv6-mapped IPv4 (::ffff:169.254.169.254) — checks the underlying IPv4
    - Decimal integer IPv4 (2852039166 == 169.254.169.254)
    - Named hostnames in _BLOCKED_HOSTNAMES (localhost, metadata.google.internal)

    Known limitations (require network-level mitigation):
    - Percent-encoded hostnames — urlparse does not decode
    - Open redirects (public URL → private IP) — redirect target not inspected
    - URLs embedded inside JSON string values in tool arguments
    """
    if host.lower() in _BLOCKED_HOSTNAMES:
        return True

    # Standard IP parse (IPv4, IPv6, IPv6-mapped IPv4)
    try:
        addr = ipaddress.ip_address(host)
        # IPv6-mapped IPv4 (::ffff:x.x.x.x): check the underlying IPv4 address
        # against IPv4 networks, since IPv6Address won't match IPv4Network ranges
        if hasattr(addr, "ipv4_mapped") and addr.ipv4_mapped is not None:
            return any(addr.ipv4_mapped in net for net in _PRIVATE_NETWORKS)
        return any(addr in net for net in _PRIVATE_NETWORKS)
    except ValueError:
        pass

    # Decimal integer IPv4 (e.g. 2852039166 == 169.254.169.254)
    try:
        addr = ipaddress.ip_address(int(host))
        return any(addr in net for net in _PRIVATE_NETWORKS)
    except (ValueError, TypeError):
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
    """Block fetch/HTTP tool calls targeting private or link-local addresses."""

    name = "ssrf"

    def __init__(
        self,
        action: Action = Action.BLOCK,
        extra_blocked_hosts: list[str] | None = None,
    ) -> None:
        self._action = action
        self._extra: set[str] = {h.lower() for h in (extra_blocked_hosts or [])}

    def check(self, tool_name: str, arguments: dict[str, Any]) -> PolicyDecision:
        url = _extract_url(arguments)
        if url is None:
            return PolicyDecision.allow(self.name)

        host = _parse_host(url)
        if host is None:
            return PolicyDecision.allow(self.name)

        blocked = _is_private_host(host) or host.lower() in self._extra
        if not blocked:
            return PolicyDecision.allow(self.name)

        reason = f"SSRF: host '{host}' resolves to a private/reserved address"
        if self._action is Action.BLOCK:
            return PolicyDecision.block(self.name, reason, url=url, host=host)
        return PolicyDecision.warn(self.name, reason, url=url, host=host)


class AllowlistRule(BaseRule):
    """
    Only permit URLs whose host is in the allowlist.

    Suffix-spoof resistant: *.example.com does NOT match api.example.com.evil.com.
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
            return True
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
    """Reject any URL containing a blocked pattern (substring match)."""

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
    """Observability-only: wraps a rule and downgrades BLOCK → WARN."""

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
