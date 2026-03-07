"""
tests/test_policy_engine.py — Unit tests for the mcp-relay policy engine.

Run with:
    pytest tests/test_policy_engine.py -v

No network access required — all tests are unit-level.
"""

from __future__ import annotations

import pytest

from mcp_relay.policy.decision import Action, PolicyDecision
from mcp_relay.policy.engine import PolicyConfig, PolicyEngine, PolicyViolationError
from mcp_relay.policy.rules import (
    AllowlistRule,
    BlocklistRule,
    DryRunRule,
    SSRFRule,
)


# ---------------------------------------------------------------------------
# SSRFRule — unit tests
# ---------------------------------------------------------------------------

class TestSSRFRule:
    def setup_method(self):
        self.rule = SSRFRule()

    def _check(self, url: str) -> PolicyDecision:
        return self.rule.check("fetch", {"url": url})

    # Private IPv4 ranges
    def test_blocks_link_local_metadata(self):
        d = self._check("http://169.254.169.254/latest/meta-data/")
        assert d.is_blocked
        assert "169.254.169.254" in d.reason

    def test_blocks_rfc1918_10(self):
        d = self._check("http://10.0.0.1/admin")
        assert d.is_blocked

    def test_blocks_rfc1918_172(self):
        d = self._check("http://172.16.5.4/secret")
        assert d.is_blocked

    def test_blocks_rfc1918_192_168(self):
        d = self._check("http://192.168.1.1/")
        assert d.is_blocked

    def test_blocks_loopback_ipv4(self):
        d = self._check("http://127.0.0.1:8080/internal")
        assert d.is_blocked

    def test_blocks_loopback_localhost(self):
        d = self._check("http://localhost:8080/")
        assert d.is_blocked

    def test_blocks_metadata_hostname(self):
        d = self._check("http://metadata.google.internal/computeMetadata/v1/")
        assert d.is_blocked

    def test_blocks_gcp_metadata_alias(self):
        d = self._check("http://metadata.goog/")
        assert d.is_blocked

    # Public hosts
    def test_allows_public_https(self):
        d = self._check("https://httpbin.org/get")
        assert d.action == Action.ALLOW

    def test_allows_public_api(self):
        d = self._check("https://api.github.com/repos/BoilerToad/mcp-relay")
        assert d.action == Action.ALLOW

    def test_allows_no_url_in_args(self):
        d = self.rule.check("list_files", {"path": "/tmp"})
        assert d.action == Action.ALLOW

    # Warn mode
    def test_warn_mode_does_not_block(self):
        rule = SSRFRule(action=Action.WARN)
        d = rule.check("fetch", {"url": "http://169.254.169.254/"})
        assert d.action == Action.WARN
        assert not d.is_blocked

    # Extra blocked hosts
    def test_extra_blocked_host(self):
        rule = SSRFRule(extra_blocked_hosts=["internal.corp"])
        d = rule.check("fetch", {"url": "http://internal.corp/api"})
        assert d.is_blocked

    # Alternate argument key names
    def test_uri_key(self):
        d = self.rule.check("fetch", {"uri": "http://10.1.2.3/"})
        assert d.is_blocked

    def test_endpoint_key(self):
        d = self.rule.check("fetch", {"endpoint": "http://192.168.0.1/"})
        assert d.is_blocked


# ---------------------------------------------------------------------------
# SSRFRule — bypass / evasion attempts
# Reviewers will ask whether the rule can be circumvented.
# These tests document the scope of protection and its known limits.
# ---------------------------------------------------------------------------

class TestSSRFRuleBypassAttempts:
    """
    Test common SSRF bypass techniques against the policy engine.

    Some bypass techniques (decimal IP, IPv6-mapped) ARE caught because
    Python's ipaddress module normalises them before network lookup.
    Others (URL-encoded hosts, open redirects) are NOT caught at the
    URL-parsing layer — these are documented as known limitations that
    require a network-level control (e.g. mitmproxy allowlist).
    """

    def setup_method(self):
        self.rule = SSRFRule()

    def _check(self, url: str) -> PolicyDecision:
        return self.rule.check("fetch", {"url": url})

    # -- Caught by ipaddress normalisation --

    def test_blocks_decimal_ip(self):
        """169.254.169.254 expressed as a 32-bit decimal integer."""
        # 169*16777216 + 254*65536 + 169*256 + 254 = 2852039166
        d = self._check("http://2852039166/latest/meta-data/")
        assert d.is_blocked, "Decimal IP representation should be blocked"

    def test_blocks_ipv6_mapped_ipv4(self):
        """IPv6-mapped IPv4 address for 169.254.169.254."""
        d = self._check("http://[::ffff:169.254.169.254]/")
        assert d.is_blocked, "IPv6-mapped IPv4 SSRF address should be blocked"

    def test_blocks_ipv6_loopback(self):
        """IPv6 loopback ::1"""
        d = self._check("http://[::1]/admin")
        assert d.is_blocked, "IPv6 loopback should be blocked"

    def test_blocks_ipv6_ula(self):
        """IPv6 ULA range (fc00::/7)"""
        d = self._check("http://[fd00::1]/internal")
        assert d.is_blocked, "IPv6 ULA address should be blocked"

    def test_blocks_ipv6_link_local(self):
        """IPv6 link-local (fe80::/10)"""
        d = self._check("http://[fe80::1]/")
        assert d.is_blocked, "IPv6 link-local address should be blocked"

    def test_blocks_shared_address_space(self):
        """RFC 6598 shared address space (100.64.0.0/10) — used by some cloud NAT."""
        d = self._check("http://100.64.0.1/")
        assert d.is_blocked, "Shared address space should be blocked"

    # -- Known limitations (NOT caught at URL-parse layer) --

    def test_known_limit_url_encoded_host(self):
        """
        URL-encoded IP (e.g. %31%36%39...) is NOT blocked by this rule.

        urllib.parse.urlparse does not decode percent-encoded hostnames,
        so the host extracted is the encoded string, which does not match
        any IP address.  Mitigation: network-level proxy (mitmproxy) with
        an allowlist, which operates post-DNS-resolution.
        """
        d = self._check("http://%31%36%39%2e%32%35%34%2e%31%36%39%2e%32%35%34/")
        # Document the current behaviour rather than asserting a block
        # If this ever starts blocking, that's a good thing — update the test.
        assert d.action in (Action.ALLOW, Action.BLOCK), "Unexpected action"
        if d.action == Action.ALLOW:
            pytest.skip(
                "URL-encoded hostname bypass is a known limitation. "
                "Mitigation: network-level proxy allowlist."
            )

    def test_known_limit_open_redirect(self):
        """
        Open redirect bypass (public URL → private IP) is NOT caught here.

        If a public host redirects to 169.254.169.254, the relay policy
        engine allows the initial fetch (public host is permitted) and cannot
        inspect the redirect destination without following it.
        Mitigation: mitmproxy intercepts the redirect at the network layer.

        This test documents the limitation; it does not assert a block.
        """
        # The relay sees only the public URL; it cannot know about the redirect
        d = self._check("https://legit.example.com/redirect?to=http://169.254.169.254/")
        # This ALLOWS because the host is legit.example.com — document this
        assert d.action == Action.ALLOW, (
            "Open redirect: relay sees only the initial public host. "
            "This is a known limitation documented in the paper."
        )


# ---------------------------------------------------------------------------
# AllowlistRule — unit tests + bypass attempts
# ---------------------------------------------------------------------------

class TestAllowlistRule:
    def test_empty_allowlist_allows_all(self):
        rule = AllowlistRule(hosts=[])
        d = rule.check("fetch", {"url": "https://anything.com/"})
        assert d.action == Action.ALLOW

    def test_exact_host_allowed(self):
        rule = AllowlistRule(hosts=["api.example.com"])
        d = rule.check("fetch", {"url": "https://api.example.com/v1"})
        assert d.action == Action.ALLOW

    def test_unlisted_host_blocked(self):
        rule = AllowlistRule(hosts=["api.example.com"])
        d = rule.check("fetch", {"url": "https://other.com/"})
        assert d.is_blocked

    def test_wildcard_subdomain_allowed(self):
        rule = AllowlistRule(hosts=["*.example.com"])
        d = rule.check("fetch", {"url": "https://sub.example.com/"})
        assert d.action == Action.ALLOW

    def test_wildcard_root_allowed(self):
        rule = AllowlistRule(hosts=["*.example.com"])
        d = rule.check("fetch", {"url": "https://example.com/"})
        assert d.action == Action.ALLOW

    def test_wildcard_does_not_match_other_domain(self):
        rule = AllowlistRule(hosts=["*.example.com"])
        d = rule.check("fetch", {"url": "https://notexample.com/"})
        assert d.is_blocked

    def test_suffix_spoof_blocked(self):
        """
        Classic allowlist bypass: attacker registers api.example.com.evil.com
        and hopes the rule matches because the string ends with 'example.com'.
        The wildcard rule must NOT match this.
        """
        rule = AllowlistRule(hosts=["*.example.com"])
        d = rule.check("fetch", {"url": "https://api.example.com.evil.com/"})
        assert d.is_blocked, (
            "Suffix-spoof bypass: api.example.com.evil.com must NOT match *.example.com"
        )

    def test_subdomain_spoof_blocked(self):
        """
        Attacker uses example.com as a subdomain of their own domain.
        e.g. example.com.attacker.io should not match api.example.com.
        """
        rule = AllowlistRule(hosts=["api.example.com"])
        d = rule.check("fetch", {"url": "https://api.example.com.attacker.io/"})
        assert d.is_blocked, (
            "Subdomain spoof: api.example.com.attacker.io must NOT match api.example.com"
        )


# ---------------------------------------------------------------------------
# BlocklistRule — unit tests
# ---------------------------------------------------------------------------

class TestBlocklistRule:
    def test_empty_blocklist_allows_all(self):
        rule = BlocklistRule(patterns=[])
        d = rule.check("fetch", {"url": "https://evil.com/"})
        assert d.action == Action.ALLOW

    def test_pattern_match_blocked(self):
        rule = BlocklistRule(patterns=["evil.com"])
        d = rule.check("fetch", {"url": "https://evil.com/payload"})
        assert d.is_blocked

    def test_substring_match(self):
        rule = BlocklistRule(patterns=[".onion"])
        d = rule.check("fetch", {"url": "http://abc.onion/hidden"})
        assert d.is_blocked

    def test_non_matching_allowed(self):
        rule = BlocklistRule(patterns=["evil.com"])
        d = rule.check("fetch", {"url": "https://good.com/"})
        assert d.action == Action.ALLOW


# ---------------------------------------------------------------------------
# DryRunRule — unit tests
# ---------------------------------------------------------------------------

class TestDryRunRule:
    def test_block_downgraded_to_warn(self):
        inner = SSRFRule()
        rule = DryRunRule(inner)
        d = rule.check("fetch", {"url": "http://169.254.169.254/"})
        assert d.action == Action.WARN
        assert not d.is_blocked
        assert "DRY-RUN" in d.reason

    def test_allow_still_passes(self):
        inner = SSRFRule()
        rule = DryRunRule(inner)
        d = rule.check("fetch", {"url": "https://httpbin.org/get"})
        assert d.action == Action.ALLOW

    def test_dry_run_passes_call_through(self):
        """
        dry_run=True must NOT block — the call must be permitted to proceed
        so the relay can observe what would have been blocked.
        """
        cfg = PolicyConfig(enabled=True, dry_run=True, ssrf_protection=True)
        engine = PolicyEngine.from_config(cfg)
        d = engine.evaluate("fetch", {"url": "http://169.254.169.254/"})
        assert not d.is_blocked, "dry_run mode must not block calls"
        assert d.action == Action.WARN, "dry_run mode should warn, not block"


# ---------------------------------------------------------------------------
# Argument key extraction — edge cases
# ---------------------------------------------------------------------------

class TestURLExtraction:
    """
    The relay extracts URLs from tool arguments by key name.
    These tests confirm edge cases in _extract_url are handled correctly.
    """

    def setup_method(self):
        self.rule = SSRFRule()

    def test_standard_url_key(self):
        d = self.rule.check("fetch", {"url": "http://10.0.0.1/"})
        assert d.is_blocked

    def test_uri_key(self):
        d = self.rule.check("fetch", {"uri": "http://10.0.0.1/"})
        assert d.is_blocked

    def test_href_key(self):
        d = self.rule.check("fetch", {"href": "http://10.0.0.1/"})
        assert d.is_blocked

    def test_link_key(self):
        d = self.rule.check("fetch", {"link": "http://10.0.0.1/"})
        assert d.is_blocked

    def test_endpoint_key(self):
        d = self.rule.check("fetch", {"endpoint": "http://192.168.0.1/"})
        assert d.is_blocked

    def test_target_key(self):
        d = self.rule.check("fetch", {"target": "http://192.168.0.1/"})
        assert d.is_blocked

    def test_fallback_scan_catches_http_value(self):
        """Non-standard key: relay scans all values for http:// prefix."""
        d = self.rule.check("fetch", {"some_custom_key": "http://10.0.0.1/secret"})
        assert d.is_blocked

    def test_no_url_in_args_allows(self):
        d = self.rule.check("list_files", {"path": "/tmp", "recursive": True})
        assert d.action == Action.ALLOW

    def test_empty_args_allows(self):
        d = self.rule.check("fetch", {})
        assert d.action == Action.ALLOW

    def test_nested_json_string_not_caught(self):
        """
        Known limitation: URL embedded inside a JSON string value is NOT extracted.
        e.g. {"body": '{"url": "http://169.254.169.254/"}'} — the outer value
        is a string, not a URL, so _extract_url does not parse into it.
        Mitigation: network-level proxy.
        """
        d = self.rule.check("fetch", {
            "body": '{"url": "http://169.254.169.254/"}'
        })
        # Document current behaviour
        assert d.action == Action.ALLOW, (
            "Nested JSON string is a known extraction limitation. "
            "Mitigation: network-level proxy allowlist."
        )


# ---------------------------------------------------------------------------
# PolicyEngine — integration tests
# ---------------------------------------------------------------------------

class TestPolicyEngine:

    def test_default_engine_blocks_ssrf(self):
        engine = PolicyEngine.default()
        d = engine.evaluate("fetch", {"url": "http://169.254.169.254/"})
        assert d.is_blocked

    def test_default_engine_allows_public(self):
        engine = PolicyEngine.default()
        d = engine.evaluate("fetch", {"url": "https://httpbin.org/get"})
        assert d.action == Action.ALLOW

    def test_noop_engine_allows_everything(self):
        engine = PolicyEngine.noop()
        d = engine.evaluate("fetch", {"url": "http://169.254.169.254/"})
        assert d.action == Action.ALLOW

    def test_from_config_disabled(self):
        cfg = PolicyConfig(enabled=False)
        engine = PolicyEngine.from_config(cfg)
        d = engine.evaluate("fetch", {"url": "http://10.0.0.1/"})
        assert d.action == Action.ALLOW

    def test_from_config_dry_run(self):
        cfg = PolicyConfig(enabled=True, dry_run=True, ssrf_protection=True)
        engine = PolicyEngine.from_config(cfg)
        d = engine.evaluate("fetch", {"url": "http://169.254.169.254/"})
        assert d.action == Action.WARN
        assert not d.is_blocked

    def test_from_config_allowlist(self):
        cfg = PolicyConfig(
            enabled=True,
            ssrf_protection=True,
            url_allowlist=["trusted.com"],
        )
        engine = PolicyEngine.from_config(cfg)
        assert engine.evaluate("fetch", {"url": "https://trusted.com/"}).action == Action.ALLOW
        assert engine.evaluate("fetch", {"url": "https://other.com/"}).is_blocked

    def test_from_config_blocklist(self):
        cfg = PolicyConfig(
            enabled=True,
            ssrf_protection=False,
            url_blocklist=["pastebin.com"],
        )
        engine = PolicyEngine.from_config(cfg)
        assert engine.evaluate("fetch", {"url": "https://pastebin.com/raw/abc"}).is_blocked
        assert engine.evaluate("fetch", {"url": "https://github.com/"}).action == Action.ALLOW

    def test_ssrf_short_circuits_before_allowlist(self):
        """SSRF check fires first; BLOCK should short-circuit."""
        cfg = PolicyConfig(
            enabled=True,
            ssrf_protection=True,
            url_allowlist=["169.254.169.254"],  # shouldn't matter
        )
        engine = PolicyEngine.from_config(cfg)
        d = engine.evaluate("fetch", {"url": "http://169.254.169.254/"})
        assert d.is_blocked
        assert d.rule_name == "ssrf"

    def test_allowlist_suffix_spoof_blocked(self):
        """End-to-end: suffix spoof must be blocked even through the full engine."""
        cfg = PolicyConfig(
            enabled=True,
            ssrf_protection=False,   # isolate allowlist rule
            url_allowlist=["*.example.com"],
        )
        engine = PolicyEngine.from_config(cfg)
        d = engine.evaluate("fetch", {"url": "https://api.example.com.evil.com/"})
        assert d.is_blocked, "Suffix spoof must be blocked by allowlist rule"

    def test_decimal_ip_blocked_end_to_end(self):
        """End-to-end: decimal IP bypass must be caught by full engine."""
        engine = PolicyEngine.default()
        d = engine.evaluate("fetch", {"url": "http://2852039166/"})
        assert d.is_blocked, "Decimal IP representation of 169.254.169.254 must be blocked"

    def test_ipv6_mapped_blocked_end_to_end(self):
        """End-to-end: IPv6-mapped IPv4 bypass must be caught."""
        engine = PolicyEngine.default()
        d = engine.evaluate("fetch", {"url": "http://[::ffff:169.254.169.254]/"})
        assert d.is_blocked, "IPv6-mapped SSRF address must be blocked"


# ---------------------------------------------------------------------------
# PolicyViolationError
# ---------------------------------------------------------------------------

class TestPolicyViolationError:
    def test_raises_on_blocked_call(self):
        engine = PolicyEngine.default()
        with pytest.raises(PolicyViolationError) as exc_info:
            decision = engine.evaluate("fetch", {"url": "http://169.254.169.254/"})
            if decision.is_blocked:
                raise PolicyViolationError(decision)
        assert "ssrf" in str(exc_info.value).lower()

    def test_exception_carries_decision(self):
        d = PolicyDecision.block("ssrf", "test block", url="http://10.0.0.1/")
        err = PolicyViolationError(d)
        assert err.decision is d
        assert err.decision.is_blocked
