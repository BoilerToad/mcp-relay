"""
Shared pytest fixtures for mcp-relay test suite.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from mcp_relay.config import RelayConfig
from mcp_relay.core.logging import EventLogger
from mcp_relay.storage.sqlite import SQLiteStorage
from mcp_relay.transport import TransportMode


# ------------------------------------------------------------------
# Config fixtures
# ------------------------------------------------------------------

@pytest.fixture
def default_config(tmp_path: Path) -> RelayConfig:
    config = RelayConfig.defaults()
    config.logging.output = str(tmp_path / "relay.log")
    config.storage.path = str(tmp_path / "events.db")
    return config


@pytest.fixture
def live_config(tmp_path: Path) -> RelayConfig:
    config = RelayConfig.defaults()
    config.logging.output = str(tmp_path / "relay.log")
    config.storage.path = str(tmp_path / "events.db")
    config.transport.default_mode = TransportMode.LIVE
    config.upstream.command = "uvx"
    config.upstream.args = ["mcp-server-fetch"]
    return config


# ------------------------------------------------------------------
# Storage fixture
# ------------------------------------------------------------------

@pytest.fixture
def storage(tmp_path: Path) -> SQLiteStorage:
    """A fresh initialized SQLiteStorage for each test."""
    s = SQLiteStorage(tmp_path / "relay_test.db")
    s.initialize()
    yield s
    s.close()


# ------------------------------------------------------------------
# Logger fixture
# ------------------------------------------------------------------

@pytest.fixture
def event_logger(tmp_path: Path) -> EventLogger:
    log_path = tmp_path / "test_events.log"
    logger = EventLogger(output_path=log_path, echo_stderr=False)
    yield logger
    logger.close()


# ------------------------------------------------------------------
# Mock MCP tool fixtures
# ------------------------------------------------------------------

@pytest.fixture
def mock_tool_result():
    from mcp.types import CallToolResult, TextContent
    return CallToolResult(
        content=[TextContent(type="text", text="mock response")],
        isError=False,
    )


@pytest.fixture
def mock_tools():
    from mcp.types import Tool
    return [
        Tool(
            name="fetch",
            description="Fetch a URL",
            inputSchema={
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
            },
        ),
        Tool(
            name="echo",
            description="Echo input back",
            inputSchema={
                "type": "object",
                "properties": {"message": {"type": "string"}},
                "required": ["message"],
            },
        ),
    ]


# ------------------------------------------------------------------
# Ollama model inventory
# ------------------------------------------------------------------

TOOL_CAPABLE_MODELS = [
    "llama3.2:latest",
    "qwen2.5:latest",
    "Llama3.1:8b",
    "gemma3:4b",
    "gemma3:12b",
]

ALL_LOCAL_MODELS = [
    "deepseek-r1:14b", "deepseek-r1:32b", "deepseek-r1:7b", "deepseek-r1:8b",
    "deepseek-v3.2:cloud", "gemini-3-pro-preview:latest",
    "gemma2:27b", "gemma3:12b", "gemma3:12b-beeai", "gemma3:27b", "gemma3:4b",
    "glm-4.7:cloud", "gpt-oss:20b",
    "hf.co/unsloth/DeepSeek-R1-Distill-Llama-8B-GGUF:F16",
    "Llama3.1:8b", "Llama3.1:latest", "llama3.2-vision:11b", "llama3.2:latest",
    "llama3.3:latest", "llava:13b", "mxbai-embed-large:latest",
    "nemotron-3-nano:latest", "qwen2.5:latest", "x/z-image-turbo:latest",
]
