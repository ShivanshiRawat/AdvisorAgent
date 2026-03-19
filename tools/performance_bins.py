"""
tools/performance_bins.py

Single source of truth for performance bin thresholds.

Imported by:
  - tools/schemas.py       → injects live values into tool description text
  - tools/performance_tools.py → binning logic + SQL query named parameters

To change thresholds: edit the constants here only. Everything else updates
automatically.
"""

# ── Recall (0.0 – 1.0) ────────────────────────────────────────────────────────
RECALL_LOW_MAX       = 0.80   # < 0.80  → "low"
RECALL_MODERATE_MAX  = 0.92   # < 0.92  → "moderate",  else → "high"

# ── QPS (queries per second) ──────────────────────────────────────────────────
QPS_LOW_MAX          = 500    # < 500   → "low"
QPS_MODERATE_MAX     = 1500   # < 1500  → "moderate",  else → "high"

# ── Latency (P95, milliseconds) ───────────────────────────────────────────────
LATENCY_LOW_MAX      = 35     # <= 35   → "low"
LATENCY_MODERATE_MAX = 80     # <= 80   → "moderate",  else → "high"


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
