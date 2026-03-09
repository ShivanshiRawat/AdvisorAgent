"""
storage package.
Exposes save_turn for persisting conversation turns to Couchbase.
"""
from .conversation_store import save_turn

__all__ = ["save_turn"]
