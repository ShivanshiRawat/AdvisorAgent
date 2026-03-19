"""
tools/performance_tools.py

Implements find_baseline_configuration:
  1. Bins the user's raw recall / QPS / latency values using the central
     thresholds from performance_bins.py.
  2. Executes the benchmark similarity SQL++ query against the local
     Couchbase cluster (same host/credentials as conversation storage,
     keyspace: benchmark._default._default).
  3. Returns the closest matching benchmark row for the LLM to present.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any, Dict

from tools.performance_bins import (
    RECALL_LOW_MAX, RECALL_MODERATE_MAX,
    QPS_LOW_MAX, QPS_MODERATE_MAX,
    LATENCY_LOW_MAX, LATENCY_MODERATE_MAX,
    bin_recall, bin_qps, bin_latency,
)

logger = logging.getLogger(__name__)

# ── Cluster access via shared singleton ──────────────────────────────────────
# get_cluster() from couchbase_client returns the one shared Cluster object.
# No separate connection is created here.

from storage.couchbase_client import get_cluster


# ── SQL++ query ───────────────────────────────────────────────────────────────
# Field names mirror the exact document structure in benchmark._default._default.
# All threshold values are injected as named parameters so they stay in sync
# with performance_bins.py constants — never hardcoded here.

_BENCHMARK_QUERY = """
WITH params AS (
  SELECT
    $target_solution      AS target_solution,
    $target_scale         AS target_scale,
    $target_dimension     AS target_dimension,
    $target_recall_bin    AS target_recall_bin,
    $target_qps_bin       AS target_qps_bin,
    $target_latency_bin   AS target_latency_bin,

    $recall_low_max       AS recall_low_max,
    $recall_moderate_max  AS recall_moderate_max,
    $qps_low_max          AS qps_low_max,
    $qps_moderate_max     AS qps_moderate_max,
    $latency_low_max      AS latency_low_max,
    $latency_moderate_max AS latency_moderate_max
),
filtered AS (
  SELECT
    META(b).id AS doc_id,
    b AS full_document,
    b.*,
    p.*
  FROM `advisor`.benchmark.perf_data AS b, params AS p
  WHERE b.Solution = p.target_solution
    AND b.`Dataset Scale` IS VALUED
    AND b.Dimensions IS VALUED

    AND (
      p.target_recall_bin NOT IN ["high", "moderate"]
      OR (b.Recall IS VALUED AND b.Recall != "")
    )
    AND (
      p.target_qps_bin NOT IN ["high", "moderate"]
      OR (b.QPS IS VALUED AND b.QPS != "")
    )
    AND (
      p.target_latency_bin NOT IN ["high", "moderate"]
      OR (b.`P95 Latency (ms)` IS VALUED AND b.`P95 Latency (ms)` != "")
    )
),
binned AS (
  SELECT
    f.*,
    CASE
      WHEN f.Recall < f.recall_low_max      THEN "low"
      WHEN f.Recall < f.recall_moderate_max THEN "moderate"
      ELSE "high"
    END AS recall_bin_calc,
    CASE
      WHEN f.QPS < f.qps_low_max      THEN "low"
      WHEN f.QPS < f.qps_moderate_max THEN "moderate"
      ELSE "high"
    END AS qps_bin_calc,
    CASE
      WHEN f.`P95 Latency (ms)` <= f.latency_low_max      THEN "low"
      WHEN f.`P95 Latency (ms)` <= f.latency_moderate_max THEN "moderate"
      ELSE "high"
    END AS latency_bin_calc
  FROM filtered AS f
),
scored AS (
  SELECT
    b.*,
    ABS(b.`Dataset Scale` - b.target_scale)    AS scale_distance,
    ABS(b.Dimensions      - b.target_dimension) AS dimension_distance,
    (CASE WHEN b.target_recall_bin  = "high" AND b.recall_bin_calc  = "high" THEN 1 ELSE 0 END) +
    (CASE WHEN b.target_qps_bin     = "high" AND b.qps_bin_calc     = "high" THEN 1 ELSE 0 END) +
    (CASE WHEN b.target_latency_bin = "low" AND b.latency_bin_calc = "low" THEN 1 ELSE 0 END) AS high_req_match_count,
    (CASE WHEN b.recall_bin_calc  = b.target_recall_bin  THEN 0 ELSE 1 END) +
    (CASE WHEN b.qps_bin_calc     = b.target_qps_bin     THEN 0 ELSE 1 END) +
    (CASE WHEN b.latency_bin_calc = b.target_latency_bin THEN 0 ELSE 1 END) AS total_bin_mismatch
  FROM binned AS b
)
SELECT
  s.doc_id,
  s.Solution,
  s.`Dataset Scale`,
  s.Dimensions,
  s.Recall,
  s.QPS,
  s.`P95 Latency (ms)`,
  s.recall_bin_calc,
  s.qps_bin_calc,
  s.latency_bin_calc,
  s.full_document
FROM scored AS s
ORDER BY
  s.scale_distance       ASC,
  s.dimension_distance   ASC,
  s.high_req_match_count DESC,
  s.total_bin_mismatch   ASC,
  s.Recall               DESC,
  s.`P95 Latency (ms)`   ASC,
  s.QPS                  DESC
LIMIT 30;
"""


def find_baseline_configuration(
    solution: str,
    target_scale: int,
    target_dimension: int,
    target_recall: float,
    target_qps: float,
    target_latency: float,
) -> Dict[str, Any]:
    """
    1. Bins raw recall / QPS / latency using central thresholds.
    2. Queries the benchmark cluster and returns the closest row.

    The result is returned to the LLM as a JSON dict. The LLM should
    present it as: "A benchmark run at <scale> with <config> achieved
    <recall/qps/latency>. Use this as your starting point — your target
    scale is different, so tune from there."
    """

    # ── Step 1: bin the raw performance requirements ──────────────────────────
    recall_bin  = bin_recall(target_recall)
    qps_bin     = bin_qps(target_qps)
    latency_bin = bin_latency(target_latency)

    logger.info(
        "find_baseline_configuration: solution=%s scale=%s dim=%s "
        "recall=%.3f(%s) qps=%.1f(%s) latency=%.1f(%s)",
        solution, target_scale, target_dimension,
        target_recall, recall_bin,
        target_qps, qps_bin,
        target_latency, latency_bin,
    )

    # ── Step 2: connect and query ─────────────────────────────────────────────
    cluster = get_cluster()
    if cluster is None:
        return {
            "status": "error",
            "message": "Benchmark cluster is unavailable. Cannot retrieve baseline configuration.",
        }

    try:
        from couchbase.options import QueryOptions

        result = cluster.query(
            _BENCHMARK_QUERY,
            QueryOptions(named_parameters={
                "target_solution":      solution,
                "target_scale":         target_scale,
                "target_dimension":     target_dimension,
                "target_recall_bin":    recall_bin,
                "target_qps_bin":       qps_bin,
                "target_latency_bin":   latency_bin,
                "recall_low_max":       RECALL_LOW_MAX,
                "recall_moderate_max":  RECALL_MODERATE_MAX,
                "qps_low_max":          QPS_LOW_MAX,
                "qps_moderate_max":     QPS_MODERATE_MAX,
                "latency_low_max":      LATENCY_LOW_MAX,
                "latency_moderate_max": LATENCY_MODERATE_MAX,
            }),
        )

        rows = [row for row in result]

        if not rows:
            return {
                "status": "no_match",
                "message": (
                    f"No benchmark data found for solution='{solution}'. "
                    "The benchmark dataset may not contain records for this index type."
                ),
                "derived_bins": {
                    "recall_bin":  recall_bin,
                    "qps_bin":     qps_bin,
                    "latency_bin": latency_bin,
                },
            }

        top = rows[0]
        return {
            "status": "success",
            "derived_bins": {
                "recall_bin":  recall_bin,
                "qps_bin":     qps_bin,
                "latency_bin": latency_bin,
            },
            "closest_benchmark": {
                "doc_id":          top.get("doc_id"),
                "solution":        top.get("Solution"),
                "benchmark_scale": top.get("Dataset Scale"),
                "dimensions":      top.get("Dimensions"),
                "recall":          top.get("Recall"),
                "qps":             top.get("QPS"),
                "p95_latency_ms":  top.get("P95 Latency (ms)"),
                "recall_bin":      top.get("recall_bin_calc"),
                "qps_bin":         top.get("qps_bin_calc"),
                "latency_bin":     top.get("latency_bin_calc"),
                "full_document":   top.get("full_document"),
            },
            "scale_note": (
                f"This benchmark was run at {top.get('Dataset Scale'):,} vectors. "
                f"Your target is {target_scale:,} vectors. "
                "Treat the returned configuration as a starting point and tune from there."
            ),
        }

    except Exception as exc:
        logger.error("Benchmark query failed: %s", exc, exc_info=True)
        return {"status": "error", "message": str(exc)}
