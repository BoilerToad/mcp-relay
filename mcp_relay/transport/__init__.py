"""mcp_relay.transport - Transport manager and mode implementations."""

from enum import Enum

class TransportMode(Enum):
    LIVE = "live"
    RECORD = "record"
    REPLAY = "replay"
    DEGRADED = "degraded"
    OFFLINE = "offline"
