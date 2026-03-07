"""mcp_relay.storage - Storage adapter interface and implementations."""

from mcp_relay.storage.base import EventRecord, SessionRecord, StorageBackend
from mcp_relay.storage.sqlite import SQLiteStorage

__all__ = ["StorageBackend", "SessionRecord", "EventRecord", "SQLiteStorage"]
