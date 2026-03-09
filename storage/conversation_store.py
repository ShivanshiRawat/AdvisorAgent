"""
storage/conversation_store.py

Persists conversation turns to Couchbase for debugging.

One document per Chainlit session, updated (upserted) after every turn.
Key format: via_conversation::{session_id}

Document shape:
{
    "doc_type": "via_conversation",
    "conversation_id": "<chainlit session id>",
    "started_at": "<ISO timestamp>",
    "last_updated": "<ISO timestamp>",
    "total_turns": 2,
    "turns": [
        {
            "turn_index": 0,
            "timestamp": "<ISO timestamp>",
            "user_message": "...",
            "reasoning_trace": [...],
            "response_type": "clarification" | "recommendation" | "text" | "error",
            "response_payload": {...},
            "state_snapshot": {...}
        }
    ]
}

Failures are caught, logged, and silently ignored so the agent is never interrupted.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy Couchbase connection singleton
# ---------------------------------------------------------------------------

_UNSET = object()   # sentinel: connection not yet attempted
_collection = _UNSET  # couchbase.Collection, _UNSET, or None (failed)


def _get_collection():
    """Return the Couchbase collection, creating the connection on first call.
    Returns None if the SDK isn't installed or the cluster is unreachable.
    Retries on every call until a successful connection is made.
    """
    global _collection
    if _collection is not _UNSET and _collection is not None:
        return _collection

    try:
        import config
        from couchbase.auth import PasswordAuthenticator
        from couchbase.cluster import Cluster
        from couchbase.options import ClusterOptions

        auth = PasswordAuthenticator(config.CB_USERNAME, config.CB_PASSWORD)
        cluster = Cluster(f"couchbase://{config.CB_HOST}", ClusterOptions(auth))
        cluster.wait_until_ready(timedelta(seconds=5))
        bucket = cluster.bucket(config.CB_BUCKET)
        scope  = bucket.scope(config.CB_SCOPE)
        _collection = scope.collection(config.CB_COLLECTION)
        logger.info(
            "Couchbase connected: %s / %s / %s",
            config.CB_BUCKET, config.CB_SCOPE, config.CB_COLLECTION,
        )
    except Exception as exc:
        logger.warning("Couchbase storage unavailable: %s", exc)
        _collection = _UNSET  # allow retry on next turn

    return _collection if _collection is not _UNSET else None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _doc_key(session_id: str) -> str:
    return f"via_conversation::{session_id}"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def save_turn(
    session_id: str,
    user_message: str,
    response_type: str,
    response_payload: Dict[str, Any],
    reasoning_trace: List[Dict[str, Any]],
    state_snapshot: Dict[str, Any],
) -> None:
    """Upsert the conversation document with a new turn appended.

    This function is entirely fire-and-forget from the caller's perspective.
    Any exception is caught, logged, and silently discarded.
    """
    try:
        collection = _get_collection()
        if collection is None:
            return  # Storage unavailable — already logged at connection time

        key = _doc_key(session_id)
        now = _now_iso()

        new_turn = {
            "timestamp":       now,
            "user_message":    user_message,
            "reasoning_trace": reasoning_trace,
            "response_type":   response_type,
            "response_payload": response_payload,
            "state_snapshot":  state_snapshot,
        }

        # Try to fetch the existing document and append to it
        try:
            result  = collection.get(key)
            doc     = result.content_as[dict]
            turns   = doc.get("turns", [])
            new_turn["turn_index"] = len(turns)
            turns.append(new_turn)
            doc["turns"]        = turns
            doc["last_updated"] = now
            doc["total_turns"]  = len(turns)
            collection.replace(key, doc)

        except Exception:
            # Document doesn't exist yet — create it
            new_turn["turn_index"] = 0
            doc = {
                "doc_type":        "via_conversation",
                "conversation_id": session_id,
                "started_at":      now,
                "last_updated":    now,
                "total_turns":     1,
                "turns":           [new_turn],
            }
            collection.insert(key, doc)

        logger.debug("Saved turn %d for session %s", new_turn["turn_index"], session_id)

    except Exception as exc:
        logger.error("Failed to save conversation turn to Couchbase: %s", exc)
