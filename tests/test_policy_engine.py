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
        # 'localhost' resolves to 127.0.0.1 — but we check IP not hostname for
        # the network rules.  The relay catches this via extra_blocked_hosts.
        rule = SSRFRule(extra_blocked_hosts=["localhost"])
        d = rule.check("fetch", {"url": "http://localhost:8080/"})
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
# AllowlistRule — unit tests
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
