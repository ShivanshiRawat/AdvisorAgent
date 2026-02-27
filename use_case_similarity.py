"""
use_case_similarity.py

Vector-based use case similarity engine.
Encodes user signals into a 10-dim vector and performs weighted cosine
similarity against pre-computed signature_vectors in use_cases.json.

Vector layout (10 dimensions):
  [0-3]   search_type          one-hot
  [4]     filter_selectivity   fraction of data remaining (0.0–1.0)
  [5-7]   scale                one-hot (<100M, 100M-1B, >1B)
  [8]     latency_ms           p95 ms ÷ 1000
  [9]     scale_change         0 or 1
"""

from __future__ import annotations

import json
import math
import os
from typing import Any


# ── Encoding maps ────────────────────────────────────────────────────────────

SEARCH_TYPE_INDEX = {
    "pure_vector":           0,
    "filtered_vector":       1,
    "hybrid_keyword_vector": 2,
    "filtered_hybrid":       3,
}

SCALE_BUCKET = {
    "small":        0,   # <100M
    "medium":       0,
    "large":        1,   # 100M-1B
    "massive":      1,
    "billion_plus": 2,   # >1B
}

LATENCY_NORM = 1000  # divisor to bring ms into 0.x range

VECTOR_DIM = 10


# ── Weight vector ────────────────────────────────────────────────────────────

WEIGHTS = [
    4.0, 4.0, 4.0, 4.0,   # search_type
    4.0,                   # filter_selectivity
    3.0, 3.0, 3.0,         # scale
    1.0,                   # latency_ms
    4.0,                   # scale_change
]


# ── Hard gate compatibility ──────────────────────────────────────────────────

COMPATIBLE_SEARCH_TYPES: dict[str, set[str]] = {
    "pure_vector":           {"pure_vector"},
    "filtered_vector":       {"filtered_vector", "pure_vector"},
    "hybrid_keyword_vector": {"hybrid_keyword_vector", "filtered_hybrid"},
    "filtered_hybrid":       {"filtered_hybrid", "hybrid_keyword_vector", "filtered_vector"},
}


# ── Encoding ─────────────────────────────────────────────────────────────────

def encode_signals(signals: dict[str, Any]) -> list[float]:
    """Encode a signals dict into an 11-dim numeric vector."""
    vec = [0.0] * VECTOR_DIM

    # [0-3] search_type — one-hot
    idx = SEARCH_TYPE_INDEX.get(signals.get("search_type", ""))
    if idx is not None:
        vec[idx] = 1.0

    # [4] filter_selectivity — raw fraction
    vec[4] = float(signals.get("filter_selectivity", 0))

    # [5-7] scale — one-hot
    bucket = SCALE_BUCKET.get(signals.get("scale_category", ""))
    if bucket is not None:
        vec[5 + bucket] = 1.0

    # [8] latency_ms — normalised
    vec[8] = float(signals.get("latency_ms", 0)) / LATENCY_NORM

    # [9] trajectory
    vec[9] = 1.0 if signals.get("scale_change") else 0.0

    return vec


# ── Similarity ───────────────────────────────────────────────────────────────

def weighted_cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity in weight-scaled space."""
    wa = [ai * wi for ai, wi in zip(a, WEIGHTS)]
    wb = [bi * wi for bi, wi in zip(b, WEIGHTS)]

    dot = sum(ai * bi for ai, bi in zip(wa, wb))
    na = math.sqrt(sum(x * x for x in wa))
    nb = math.sqrt(sum(x * x for x in wb))

    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


# ── Hard gates ───────────────────────────────────────────────────────────────

def _passes_hard_gates(user_signals: dict[str, Any], pattern_signals: dict[str, Any]) -> bool:
    """Eliminate patterns that violate search_type compatibility."""
    compatible = COMPATIBLE_SEARCH_TYPES.get(user_signals.get("search_type", ""), set())
    return pattern_signals.get("search_type", "") in compatible


# ── Public interface ─────────────────────────────────────────────────────────

def find_similar_cases(
    user_signals: dict[str, Any],
    use_cases_path: str,
    top_n: int = 3,
) -> list[dict[str, Any]]:
    """
    Find the most similar stored use cases via weighted cosine similarity.

    Uses pre-computed signature_vector from each stored pattern.
    Returns top_n results sorted by similarity descending.
    """
    with open(use_cases_path) as f:
        patterns = json.load(f).get("use_cases", [])

    user_vec = encode_signals(user_signals)
    results = []

    for pattern in patterns:
        p_signals = pattern.get("signals", {})
        if not _passes_hard_gates(user_signals, p_signals):
            continue

        stored_vec = pattern.get("signature_vector")
        if not stored_vec:
            continue

        similarity = weighted_cosine_similarity(user_vec, stored_vec)

        results.append({
            "pattern_id":         pattern.get("pattern_id"),
            "short_description":  pattern.get("short_description"),
            "recommended_index":  pattern.get("recommended_index"),
            "why_chosen":         pattern.get("why_chosen"),
            "why_not":            pattern.get("why_not"),
            "decision_factors":   pattern.get("decision_factors"),
            "similarity":         round(similarity, 4),
        })

    results.sort(key=lambda r: r["similarity"], reverse=True)
    return results[:top_n]


# ── Signature vector generator ───────────────────────────────────────────────

def generate_signature_vectors(use_cases_path: str) -> None:
    """Re-compute and write signature_vector for every use case."""
    with open(use_cases_path) as f:
        data = json.load(f)

    for uc in data.get("use_cases", []):
        uc["signature_vector"] = encode_signals(uc.get("signals", {}))

    with open(use_cases_path, "w") as f:
        json.dump(data, f, indent=4)

    for uc in data["use_cases"]:
        print(f"  {uc['pattern_id']:<16} → {uc['signature_vector']}")


if __name__ == "__main__":
    import sys
    path = os.path.join(os.path.dirname(__file__), "..", "knowledge_base", "use_cases.json")

    if len(sys.argv) > 1 and sys.argv[1] == "generate":
        generate_signature_vectors(path)
    else:
        results = find_similar_cases(
            user_signals={
                "search_type":          "hybrid_keyword_vector",
                "filter_selectivity":   1.0,
                "scale_category":       "billion_plus",
                "latency_ms":           150,
                "selectivity_movement": False,
                "scale_change":         False,
            },
            use_cases_path=path,
            top_n=3,
        )
        for r in results:
            print(f"  {r['similarity']:.0%}  {r['pattern_id']:<16} → {r['recommended_index']} ({r['short_description']})")
