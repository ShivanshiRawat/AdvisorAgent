"""
tools/performance_bins.py

Performance bin thresholds and binning helpers.

Thresholds are loaded from config/performance.ini via config.py.
To change thresholds: edit config/performance.ini.

Imported by:
  - tools/schemas.py       → injects live values into tool description text
  - tools/performance_tools.py → binning logic + SQL query named parameters
"""

from config import (
    RECALL_LOW_MAX,
    RECALL_MODERATE_MAX,
    QPS_LOW_MAX,
    QPS_MODERATE_MAX,
    LATENCY_LOW_MAX,
    LATENCY_MODERATE_MAX,
)


# ── Python binning helpers ────────────────────────────────────────────────────

def bin_recall(value: float) -> str:
    if value < RECALL_LOW_MAX:
        return "low"
    if value < RECALL_MODERATE_MAX:
        return "moderate"
    return "high"


def bin_qps(value: float) -> str:
    if value < QPS_LOW_MAX:
        return "low"
    if value < QPS_MODERATE_MAX:
        return "moderate"
    return "high"


def bin_latency(value: float) -> str:
    if value <= LATENCY_LOW_MAX:
        return "low"
    if value <= LATENCY_MODERATE_MAX:
        return "moderate"
    return "high"


def thresholds_for_schema() -> str:
    """
    Returns a human-readable threshold summary consumed by schemas.py
    to embed live values into tool descriptions.
    """
    return (
        f"Recall — low: <{RECALL_LOW_MAX}, "
        f"moderate: {RECALL_LOW_MAX}–{RECALL_MODERATE_MAX}, "
        f"high: >={RECALL_MODERATE_MAX}. "
        f"QPS — low: <{QPS_LOW_MAX} req/s, "
        f"moderate: {QPS_LOW_MAX}–{QPS_MODERATE_MAX} req/s, "
        f"high: >={QPS_MODERATE_MAX} req/s. "
        f"Latency (P95) — low: <={LATENCY_LOW_MAX} ms, "
        f"moderate: {LATENCY_LOW_MAX}–{LATENCY_MODERATE_MAX} ms, "
        f"high: >{LATENCY_MODERATE_MAX} ms."
    )
