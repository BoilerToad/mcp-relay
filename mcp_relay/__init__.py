"""
mcp-relay: MCP middleware relay and transport utilities.
"""

__version__ = "0.1.0"

from mcp_relay.relay import Relay
from mcp_relay.config import RelayConfig

__all__ = ["Relay", "RelayConfig", "__version__"]
