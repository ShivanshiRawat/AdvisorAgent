"""
storage/couchbase_client.py

Single shared Couchbase cluster connection.
Both conversation storage and benchmark queries import get_cluster() from here.

The cluster object is created once on first call and reused thereafter.
Failures are retried on the next call — the caller decides how to handle None.
"""

from __future__ import annotations

import logging
from datetime import timedelta

logger = logging.getLogger(__name__)

_UNSET = object()   # sentinel: connection not yet attempted
_cluster = _UNSET   # couchbase.Cluster, _UNSET (not tried / failed), or None


def get_cluster():
    """Return the shared Couchbase Cluster object.

    Creates the connection on first call with a 15-second ready-wait.
    Returns None if the cluster is unreachable; retries on every subsequent
    call so transient startup failures recover automatically.
    """
    global _cluster
    if _cluster is not _UNSET and _cluster is not None:
        return _cluster

    try:
        import config
        from couchbase.auth import PasswordAuthenticator
        from couchbase.cluster import Cluster
        from couchbase.options import ClusterOptions

        auth = PasswordAuthenticator(config.CB_USERNAME, config.CB_PASSWORD)
        c = Cluster(f"couchbase://{config.CB_HOST}", ClusterOptions(auth))
        c.wait_until_ready(timedelta(seconds=15))
        _cluster = c
        logger.info("Couchbase cluster connected: %s", config.CB_HOST)
    except Exception as exc:
        logger.warning("Couchbase cluster unavailable: %s", exc)
        _cluster = _UNSET   # allow retry on next call

    return _cluster if _cluster is not _UNSET else None
