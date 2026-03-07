"""
mcp_relay.config - Configuration loader and dataclasses.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from mcp_relay.transport import TransportMode


@dataclass
class StorageConfig:
    backend: str = "sqlite"
    path: str = "~/.mcp-relay/events.db"
    url: str | None = None  # postgres only


@dataclass
class LoggingConfig:
    format: str = "jsonl"       # jsonl | pretty
    output: str = "~/.mcp-relay/relay.log"
    rotate_mb: int = 50


@dataclass
class UpstreamConfig:
    """The real MCP server this relay forwards to."""
    command: str | None = None
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)


@dataclass
class TransportConfig:
    default_mode: TransportMode = TransportMode.LIVE
    profile: str | None = None  # path to a .yaml network profile


@dataclass
class RelayConfig:
    name: str = "mcp-relay"
    log_level: str = "INFO"
    transport: TransportConfig = field(default_factory=TransportConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    upstream: UpstreamConfig = field(default_factory=UpstreamConfig)

    @classmethod
    def from_file(cls, path: str | Path) -> "RelayConfig":
        """Load config from a YAML file."""
        path = Path(path).expanduser()
        if not path.exists():
            raise FileNotFoundError(f"Config file not found: {path}")
        with open(path) as f:
            raw: dict[str, Any] = yaml.safe_load(f) or {}
        return cls._from_dict(raw)

    @classmethod
    def defaults(cls) -> "RelayConfig":
        """Return a default config — useful for testing."""
        return cls()

    @classmethod
    def _from_dict(cls, raw: dict[str, Any]) -> "RelayConfig":
        relay_section = raw.get("relay", {})
        transport_section = raw.get("transport", {})
        storage_section = raw.get("storage", {})
        logging_section = raw.get("logging", {})
        upstream_section = raw.get("upstream", {})

        mode_str = transport_section.get("default_mode", "LIVE").upper()
        try:
            mode = TransportMode[mode_str]
        except KeyError:
            raise ValueError(f"Unknown transport mode: {mode_str}")

        return cls(
            name=relay_section.get("name", "mcp-relay"),
            log_level=relay_section.get("log_level", "INFO"),
            transport=TransportConfig(
                default_mode=mode,
                profile=transport_section.get("profile"),
            ),
            storage=StorageConfig(
                backend=storage_section.get("backend", "sqlite"),
                path=storage_section.get("path", "~/.mcp-relay/events.db"),
                url=storage_section.get("url"),
            ),
            logging=LoggingConfig(
                format=logging_section.get("format", "jsonl"),
                output=logging_section.get("output", "~/.mcp-relay/relay.log"),
                rotate_mb=logging_section.get("rotate_mb", 50),
            ),
            upstream=UpstreamConfig(
                command=upstream_section.get("command"),
                args=upstream_section.get("args", []),
                env={
                    **os.environ.copy(),
                    **upstream_section.get("env", {}),
                },
            ),
        )
