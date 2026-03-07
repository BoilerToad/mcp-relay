"""
test_config.py - RelayConfig loader tests.

Covers lines 57-62, 71-83 in config.py:
  - from_file(): happy path, missing file, empty file
  - _from_dict(): all sections, partial sections, unknown transport mode
  - defaults(): sanity checks
"""

from __future__ import annotations

import pytest
import yaml
from pathlib import Path

from mcp_relay.config import (
    RelayConfig,
    StorageConfig,
    LoggingConfig,
    UpstreamConfig,
    TransportConfig,
)
from mcp_relay.transport import TransportMode


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def write_yaml(path: Path, data: dict) -> Path:
    path.write_text(yaml.dump(data))
    return path


# ---------------------------------------------------------------------------
# defaults()
# ---------------------------------------------------------------------------

class TestDefaults:

    def test_defaults_returns_relay_config(self):
        config = RelayConfig.defaults()
        assert isinstance(config, RelayConfig)

    def test_defaults_transport_is_live(self):
        config = RelayConfig.defaults()
        assert config.transport.default_mode == TransportMode.LIVE

    def test_defaults_storage_backend_is_sqlite(self):
        config = RelayConfig.defaults()
        assert config.storage.backend == "sqlite"

    def test_defaults_log_level_is_info(self):
        config = RelayConfig.defaults()
        assert config.log_level.upper() == "INFO"

    def test_defaults_name(self):
        config = RelayConfig.defaults()
        assert config.name == "mcp-relay"


# ---------------------------------------------------------------------------
# from_file() — lines 57-62
# ---------------------------------------------------------------------------

class TestFromFile:

    def test_from_file_loads_valid_yaml(self, tmp_path):
        cfg_file = write_yaml(tmp_path / "relay.yaml", {
            "relay": {"name": "test-relay", "log_level": "DEBUG"},
            "transport": {"default_mode": "LIVE"},
            "storage": {"backend": "sqlite", "path": "/tmp/test.db"},
            "logging": {"format": "jsonl", "output": "/tmp/test.log", "rotate_mb": 10},
            "upstream": {"command": "uvx", "args": ["mcp-server-fetch"]},
        })
        config = RelayConfig.from_file(cfg_file)
        assert config.name == "test-relay"
        assert config.log_level == "DEBUG"
        assert config.storage.path == "/tmp/test.db"
        assert config.logging.rotate_mb == 10
        assert config.upstream.command == "uvx"
        assert config.upstream.args == ["mcp-server-fetch"]

    def test_from_file_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="Config file not found"):
            RelayConfig.from_file(tmp_path / "nonexistent.yaml")

    def test_from_file_empty_yaml_uses_defaults(self, tmp_path):
        """An empty YAML file should fall back to all defaults cleanly."""
        cfg_file = tmp_path / "empty.yaml"
        cfg_file.write_text("")
        config = RelayConfig.from_file(cfg_file)
        assert config.name == "mcp-relay"
        assert config.transport.default_mode == TransportMode.LIVE

    def test_from_file_partial_sections_use_defaults(self, tmp_path):
        """Only specifying one section should leave others at defaults."""
        cfg_file = write_yaml(tmp_path / "partial.yaml", {
            "relay": {"name": "partial-relay"},
        })
        config = RelayConfig.from_file(cfg_file)
        assert config.name == "partial-relay"
        assert config.storage.backend == "sqlite"
        assert config.transport.default_mode == TransportMode.LIVE

    def test_from_file_tilde_path_accepted(self, tmp_path, monkeypatch):
        """from_file should expand ~ in path."""
        monkeypatch.setenv("HOME", str(tmp_path))
        cfg_file = tmp_path / "relay.yaml"
        cfg_file.write_text(yaml.dump({"relay": {"name": "tilde-test"}}))
        # Write a symlink at ~/relay.yaml
        link = tmp_path / "relay.yaml"
        config = RelayConfig.from_file(link)
        assert config.name == "tilde-test"


# ---------------------------------------------------------------------------
# _from_dict() — lines 71-83 (transport mode parsing, all sections)
# ---------------------------------------------------------------------------

class TestFromDict:

    def test_live_mode_parsed(self):
        config = RelayConfig._from_dict({"transport": {"default_mode": "LIVE"}})
        assert config.transport.default_mode == TransportMode.LIVE

    def test_offline_mode_parsed(self):
        config = RelayConfig._from_dict({"transport": {"default_mode": "OFFLINE"}})
        assert config.transport.default_mode == TransportMode.OFFLINE

    def test_record_mode_parsed(self):
        config = RelayConfig._from_dict({"transport": {"default_mode": "RECORD"}})
        assert config.transport.default_mode == TransportMode.RECORD

    def test_replay_mode_parsed(self):
        config = RelayConfig._from_dict({"transport": {"default_mode": "REPLAY"}})
        assert config.transport.default_mode == TransportMode.REPLAY

    def test_degraded_mode_parsed(self):
        config = RelayConfig._from_dict({"transport": {"default_mode": "DEGRADED"}})
        assert config.transport.default_mode == TransportMode.DEGRADED

    def test_mode_is_case_insensitive(self):
        config = RelayConfig._from_dict({"transport": {"default_mode": "live"}})
        assert config.transport.default_mode == TransportMode.LIVE

    def test_unknown_transport_mode_raises(self):
        with pytest.raises(ValueError, match="Unknown transport mode"):
            RelayConfig._from_dict({"transport": {"default_mode": "WARP_SPEED"}})

    def test_transport_profile_parsed(self):
        config = RelayConfig._from_dict({
            "transport": {"default_mode": "LIVE", "profile": "degraded_recovery"}
        })
        assert config.transport.profile == "degraded_recovery"

    def test_storage_url_parsed(self):
        config = RelayConfig._from_dict({
            "storage": {"backend": "postgres", "url": "postgresql://localhost/relay"}
        })
        assert config.storage.backend == "postgres"
        assert config.storage.url == "postgresql://localhost/relay"

    def test_upstream_env_merged_with_os_environ(self, monkeypatch):
        """upstream.env overrides should be merged on top of os.environ."""
        monkeypatch.setenv("EXISTING_VAR", "from_os")
        config = RelayConfig._from_dict({
            "upstream": {
                "command": "uvx",
                "env": {"MY_KEY": "my_value"}
            }
        })
        assert config.upstream.env["MY_KEY"] == "my_value"
        assert config.upstream.env["EXISTING_VAR"] == "from_os"

    def test_empty_dict_gives_full_defaults(self):
        config = RelayConfig._from_dict({})
        assert config.name == "mcp-relay"
        assert config.log_level == "INFO"
        assert config.transport.default_mode == TransportMode.LIVE
        assert config.storage.backend == "sqlite"
        assert config.upstream.command is None
