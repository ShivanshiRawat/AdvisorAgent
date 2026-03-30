"""
tools/schemas.py

All tool schemas in OpenAI function-calling format.
These are consumed by agent/gemini_loop.py which converts them to Gemini's
native types.FunctionDeclaration format before passing to the model.
"""
from tools.performance_bins import thresholds_for_schema

ALL_TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "think",
            "description": (
                "Your scratchpad. Use before making decisions — dump what you know, "
                "what's missing, and what tradeoffs you're weighing."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "reasoning": {
                        "type": "string",
                        "description": "Your current reasoning in natural language.",
                    },
                },
                "required": ["reasoning"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "plan",
            "description": "Outline your approach at the start of a new user request.",
            "parameters": {
                "type": "object",
                "properties": {
                    "steps": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "step": {"type": "string"},
                                "tool": {"type": "string", "description": "Tool to use"},
                                "why": {"type": "string", "description": "Why this step matters"},
                            },
                        },
                        "description": "Ordered list of steps you plan to take.",
                    },
                },
                "required": ["steps"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_state",
            "description": (
                "Record confirmed facts, query patterns, open gaps, or reasoning into "
                "persistent memory. Call this whenever you learn something new from the user."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "confirmed_facts": {
                        "type": "object",
                        "description": "Key-value pairs of confirmed facts (e.g. {'vector_count': 40000000})",
                    },
                    "query_patterns": {
                        "type": "array",
                        "items": {"type": "object"},
                        "description": "List of identified query patterns",
                    },
                    "open_gaps": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Things you still need to know",
                    },
                    "resolved_gaps": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Previously open gaps that are now resolved",
                    },
                    "narrative_summary": {
                        "type": "string",
                        "description": "Brief summary of what you understand so far",
                    },
                    "reasoning_so_far": {
                        "type": "string",
                        "description": "Your current reasoning and hypothesis",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "Search the web for specific factual lookups: Couchbase parameter limits, "
                "release notes, or other verifiable facts. "
                "Do NOT use this for architectural decisions."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query (e.g. 'Couchbase FTS 100M vector limit')",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "use_case_search",
            "description": (
                "Search the use case library for stored patterns similar to the user's confirmed signals. "
                "Returns up to 3 matches with similarity scores, recommended indexes, and reasoning. "
                "MANDATORY: You must call this tool at least once before give_recommendation. "
                "Call this in TWO situations:\n"
                "1. EARLY — once you know search_type and scale_category — to find precedents that guide follow-up questions.\n"
                "2. LATE — after all signals are confirmed — to cross-validate your reasoning before give_recommendation.\n"
                "Use the results to understand the underlying thinking, but do NOT treat them as ground truth. "
                "Make your own decision using your intelligence. If your recommendation differs from a strong match, explain why. "
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "search_type": {
                        "type": "string",
                        "enum": ["pure_vector", "filtered_vector", "hybrid_keyword_vector", "filtered_hybrid"],
                        "description": "The fundamental query pattern.",
                    },
                    "filter_selectivity": {
                        "type": "number",
                        "description": "Fraction of data remaining after metadata filter (0.0–1.0). Use 1.0 if no filter.",
                    },
                    "scale_category": {
                        "type": "string",
                        "enum": ["small", "medium", "large", "massive", "billion_plus"],
                        "description": "Dataset size tier: small=<1M, medium=1M-50M, large=50M-100M, massive=100M-1B, billion_plus=>1B.",
                    },
                    "latency_ms": {
                        "type": "integer",
                        "description": "Latency SLA in milliseconds.",
                    },
                    "scale_change": {
                        "type": "boolean",
                        "description": "True if dataset is projected to grow from <100M to >100M vectors.",
                    },
                },
                "required": ["search_type", "filter_selectivity", "scale_category", "latency_ms", "scale_change"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "evaluate_index_viability",
            "description": (
                "MANDATORY before give_recommendation. Evaluates which index types are "
                "viable given scale, filter selectivity, and keyword search requirements."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "projected_vector_count": {
                        "type": "string",
                        "description": "3-year projected vector count (e.g. '50000000' or '50M').",
                    },
                    "filter_selectivity_pct": {
                        "type": "string",
                        "description": (
                            "Percentage of data REMAINING after filters (e.g. '15' means 15% remains). "
                            "Use '100' if no filters are applied."
                        ),
                    },
                    "requires_keyword_search": {
                        "type": "string",
                        "description": "'true' or 'false'. Does the user need fuzzy matching or BM25 keyword search?",
                    },
                },
                "required": ["projected_vector_count", "filter_selectivity_pct", "requires_keyword_search"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "compare_indexes",
            "description": (
                "Side-by-side tradeoff analysis of two index strategies. "
                "Use when two options are genuinely close to explain the difference clearly."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "option_a_type": {
                        "type": "string",
                        "enum": ["Hyperscale", "Composite", "Search", "Hybrid"],
                    },
                    "option_b_type": {
                        "type": "string",
                        "enum": ["Hyperscale", "Composite", "Search", "Hybrid"],
                    },
                    "vector_count": {"type": "integer"},
                    "has_hard_filter": {
                        "type": "boolean",
                        "description": "Is there a filter that is ALWAYS applied?",
                    },
                    "filter_selectivity_pct": {
                        "type": "number",
                        "description": "Percentage of corpus REMAINING after filter",
                    },
                    "latency_target_ms": {
                        "type": "integer",
                        "description": "Optional p95 latency target in ms",
                    },
                },
                "required": ["option_a_type", "option_b_type", "vector_count", "has_hard_filter"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_default_parameters",
            "description": (
                "Call this ONLY when the user explicitly asks for DEFAULT parameter values — "
                "using words like 'default', 'standard', or 'out-of-the-box settings'. "
                "Do NOT call this when the user asks for a starting configuration, starting point, "
                "or recommended settings — use find_baseline_configuration for those instead. "
                "Returns calculated optimal values for parameters like nlist and train_list "
                "based on the vector count. "
                "For Hybrid architectures (HVI+FTS or CVI+FTS), call this tool TWICE — once with "
                "'HVI' (or 'CVI') for the vector component, and once with 'FTS' for the search component."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "index_type": {
                        "type": "string",
                        "enum": ["HVI", "CVI", "FTS"],
                        "description": "The recommended index type.",
                    },
                    "vector_count": {
                        "type": "integer",
                        "description": "The total number of vectors in the dataset (e.g. 50000000).",
                    },
                },
                "required": ["index_type", "vector_count"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ask_user",
            "description": (
                "TERMINAL TOOL. Ask clarifying questions when critical information is missing. "
                "Every question MUST have 3–4 concrete options."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": "Context sentence to preface the questions.",
                    },
                    "questions": {
                        "type": "array",
                        "description": "List of questions to ask the user.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "question": {"type": "string"},
                                "anchor": {
                                    "type": "string",
                                    "description": "Reference the user's words, e.g. 'You mentioned category browsing...'",
                                },
                                "why_asking": {
                                    "type": "string",
                                    "description": "Why this answer changes the recommendation.",
                                },
                                "options": {
                                    "type": "array",
                                    "description": "3–4 concrete options. 'Other / Type here' is added automatically.",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "id": {"type": "string"},
                                            "label": {"type": "string"},
                                        },
                                        "required": ["id", "label"],
                                    },
                                },
                            },
                            "required": ["question", "anchor", "why_asking", "options"],
                        },
                    },
                },
                "required": ["message", "questions"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "give_recommendation",
            "description": (
                "TERMINAL TOOL. Deliver the final index recommendation. "
                "Only call after evaluate_index_viability has returned a verdict."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string"},
                    "query_pattern_recommendations": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "query_pattern": {"type": "string"},
                                "recommended_index": {"type": "string"},
                                "reasoning": {
                                    "type": "string",
                                    "description": "Physical reasoning: scale, filter mechanics, RAM constraints.",
                                },
                                "eliminated_alternatives": {
                                    "type": "object",
                                    "additionalProperties": {
                                        "type": "string",
                                        "description": "Why this index was eliminated.",
                                    },
                                },
                                "caveats": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "description": "What would change this recommendation.",
                                },
                            },
                            "required": [
                                "query_pattern",
                                "recommended_index",
                                "reasoning",
                                "eliminated_alternatives",
                            ],
                        },
                    },
                    "architecture_summary": {
                        "type": "object",
                        "properties": {
                            "total_indexes": {"type": "integer"},
                            "index_types_used": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "shared_indexes": {"type": "string"},
                            "operational_notes": {"type": "string"},
                        },
                    },
                    "next_steps": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "3 to 5 concrete, specific follow-up actions tailored to THIS conversation. "
                            "Do NOT use a fixed set of suggestions. Instead, reason about:\n"
                            "1. What was the user's original question and what might they naturally ask next?\n"
                            "2. What gaps, caveats, or trade-offs surfaced during this conversation that deserve deeper explanation?\n"
                            "3. What was eliminated and might the user want to understand why?\n"
                            "4. What operational or tuning step is the logical next move after receiving this recommendation?\n"
                            "5. What did the user seem uncertain about that you could proactively clarify?\n\n"
                            "Each item must be a short, specific sentence written from the agent's perspective. "
                            "Never repeat the same suggestions across different conversations. "
                            "Never use generic phrases like 'Answer follow-up questions' or 'Explain parameters'. "
                            "Ground every suggestion in something concrete from THIS conversation — "
                            "reference the user's scale, their index, their filter, their domain, or their stated concern. "
                            "Keep each item to one short sentence — this is displayed as a clickable menu."
                        ),
                    },
                },
                "required": ["summary", "query_pattern_recommendations", "architecture_summary"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "give_performance_profile",
            "description": (
                "TERMINAL TOOL. Call this ONLY when the user has explicitly asked for a performance "
                "requirements analysis (e.g. 'help me understand my performance needs', "
                "'what recall/QPS/latency should I target?'). "
                "Do NOT call this when collecting performance signals as part of the Benchmark Baseline "
                "Protocol — in that case, pass the values directly to find_baseline_configuration instead. "
                "Call this once you have gathered enough signal to rank Recall, QPS, and Latency "
                "and estimate target ranges for each. "
                "Do NOT call this until you have asked the user the relevant questions via ask_user "
                "and have either confirmed values or made a reasoned inference. "
                f"Bin thresholds: {thresholds_for_schema()}"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "domain_inference": {
                        "type": "string",
                        "description": (
                            "A 1-2 sentence summary of what you inferred about the user's domain "
                            "and why it drives the priority ordering you chose. "
                            "E.g. 'Fraud detection systems are high-stakes — missing a fraudulent "
                            "transaction is far worse than a slow result, so Recall takes priority.'"
                        ),
                    },
                    "metrics": {
                        "type": "array",
                        "description": (
                            "Exactly 3 entries — one each for Recall, QPS, and Latency — ordered by priority "
                            f"(primary first). Bin thresholds: {thresholds_for_schema()}"
                        ),
                        "items": {
                            "type": "object",
                            "properties": {
                                "metric": {
                                    "type": "string",
                                    "enum": ["Recall", "QPS", "Latency"],
                                },
                                "priority": {
                                    "type": "string",
                                    "enum": ["primary", "secondary", "tertiary"],
                                },
                                "bin": {
                                    "type": "string",
                                    "enum": ["Low", "Moderate", "High"],
                                    "description": (
                                        f"The categorized bin for this metric. Thresholds: {thresholds_for_schema()}"
                                    ),
                                },
                                "target_range": {
                                    "type": "string",
                                    "description": (
                                        "Human-readable target range for this metric. "
                                        "Use user-provided numbers where available, otherwise a reasoned estimate. "
                                        "Examples: '≥ 95%', '500–700 req/s', '< 100 ms p95'. "
                                        "If truly unknown, say 'to be determined — start with X and tune from there'."
                                    ),
                                },
                                "rationale": {
                                    "type": "string",
                                    "description": "One sentence explaining why this priority, bin, and range was chosen for this metric.",
                                },
                            },
                            "required": ["metric", "priority", "bin", "target_range", "rationale"],
                        },
                    },
                    "trade_off_note": {
                        "type": "string",
                        "description": (
                            "The key tension between the top two metrics for this use case. "
                            "E.g. 'Higher recall (via reranking or larger nProbe) increases latency — "
                            "tune nProbe incrementally and measure p95 at each step.'"
                        ),
                    },
                },
                "required": ["domain_inference", "metrics", "trade_off_note"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_baseline_configuration",
            "description": (
                "Queries the internal Couchbase benchmark dataset to find the most similar "
                "benchmark configuration to the user's use case. "
                "Call this when the user asks for a starting configuration, baseline parameters, "
                "or wants to know what real benchmark results look like for their scale and requirements. "
                "You MUST have collected solution type, dataset scale, vector dimensions, and "
                "performance targets (recall, QPS, latency) before calling this tool. "
                "The tool bins the raw values internally and returns the closest benchmark row. "
                "Present the result as: 'A benchmark run at <scale> with <config> achieved "
                "<recall/QPS/latency>. Your target scale is different — use this as a starting "
                "point for tuning.' Always include the scale_note from the result. "
                f"Bin thresholds used internally: {thresholds_for_schema()}"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "solution": {
                        "type": "string",
                        "enum": ["BHIVE", "GSI COMPOSITE"],
                        "description": "The index/solution type. Must match exactly what is stored in the benchmark dataset.",
                    },
                    "target_scale": {
                        "type": "integer",
                        "description": (
                            "The user's CURRENT dataset size in number of vectors/documents — today's scale, "
                            "NOT the 3-year projected scale. Use the projected scale only for evaluate_index_viability. "
                            "E.g. if the user has 100M today and expects 500M in 3 years, pass 100000000 here."
                        ),
                    },
                    "target_dimension": {
                        "type": "integer",
                        "description": "Vector dimensionality. E.g. 1536 for OpenAI text-embedding-3-large.",
                    },
                    "target_recall": {
                        "type": "number",
                        "description": "User's target recall as a decimal between 0 and 1. E.g. 0.95.",
                    },
                    "target_qps": {
                        "type": "number",
                        "description": "User's target queries per second. E.g. 1200.",
                    },
                    "target_latency": {
                        "type": "number",
                        "description": "User's target P95 latency in milliseconds. E.g. 40.",
                    },
                },
                "required": [
                    "solution",
                    "target_scale",
                    "target_dimension",
                    "target_recall",
                    "target_qps",
                    "target_latency",
                ],
            },
        },
    },
    # ── Query template tool ──────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "get_index_queries",
            "description": (
                "Returns CREATE INDEX (DDL) and SELECT (DML) query templates for the "
                "given index type. Templates contain <placeholder> notation for ALL values. "
                "The response includes two lists: 'user_must_fill' (bucket, scope, collection, "
                "field names — only the user knows these) and 'tool_can_fill' (dimension, "
                "similarity, nlist, etc. — values the LLM can substitute from earlier baseline "
                "or default parameter results). "
                "Call this when the user asks for CREATE INDEX statements, query syntax, "
                "DDL, or query templates."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "index_type": {
                        "type": "string",
                        "description": "The index type to get templates for.",
                        "enum": ["HVI", "CVI", "FTS"],
                    },
                },
                "required": ["index_type"],
            },
        },
    },
]

