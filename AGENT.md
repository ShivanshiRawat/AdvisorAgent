# Couchbase Vector Index Advisor — Expert Strategic Knowledge Base

---

# 1. INDEX TAXONOMY

## A. Hyperscale Vector Index (HVI / BHIVE)
**Architecture:** DiskANN / Vamana Graph.
**Storage Model:** Disk-centric (SSD) with a compact graph structure (routing layer) held in memory.
**Primary Signal:** Massive scale — datasets ranging from hundreds of millions to billions of vectors.
**Description:** HVI keeps vector data on SSD while the navigation graph lives in RAM. This separation allows billion-scale search without proportionally large RAM. Optimized for high recall with a low memory footprint (Data Greatness in Memory - DGM ratio of 2%).
**Performance Nuance:** Because vector fetching requires SSD I/O, HVI has a slightly higher latency floor than purely in-memory indexes. However, latency remains stable as the dataset grows and does not degrade with scale the way memory-mapped approaches do.
**Best For:**
- Large-scale semantic retrieval and recommendation with mid-high filtering.
- Deployments where the RAM budget is constrained relative to dataset size.
- Use cases prioritizing high accuracy with low latency at massive scale.
**Limitations:**
- Not optimized for extremely high-selectivity metadata pre-filtering compared to Composite.
- Slightly higher baseline latency floor due to SSD I/O bound nature.

## B. Composite Vector Index (CVI)
**Architecture:** Global Secondary Index (GSI) + FAISS.
**Storage Model:** Standard Plasma engine; requires its full index to reside in RAM to avoid severe latency degradation.
**Primary Signal:** Structured workloads with high selectivity constraints (e.g., category, brand, user permissions) before the vector search.
**Description:** CVI combines a traditional GSI for structured field filtering with the vector similarity component. It uses "Filter-First" logic, where metadata conditions are applied at the index level before the vector search runs, dramatically reducing the search space for the ANN step.
**Performance Nuance:** CVI is memory-intensive. The entire index should reside in RAM to maintain performance. At massive scale, RAM costs become the primary limiting factor.
**Best For:**
- Workloads with complex, multi-layered filters (e.g., location, price, amenities) that narrow results to a small subset (<20%).
- Multi-tenant architectures with strong per-tenant isolation (e.g., filtering by customer_id or kb_id).
- Search scenarios where customers already run regular SQL queries and need seamless integration.
**Limitations:**
- Not viable at billion-scale without proportionally large RAM budgets.
- Poor fit when filters have low selectivity (does not meaningfully reduce search space).

## C. Search Vector Index (FTS)
**Architecture:** Bleve-based Inverted Index.
**Storage Model:** Memory-mapped.
**Primary Signal:** Small-to-medium scale Hybrid Search requiring native lexical relevance (BM25, fuzzy matching, stemming).
**Description:** Integrates vector similarity search into Couchbase's Full Text Search service. It enables queries that combine lexical matching with semantic intent in a single unified index.
**Performance Nuance:** FTS is vertically limited. As vector count approaches the 100M range, memory pressure increases significantly.
**Best For:**
- Applications relying on text relevance plus semantic similarity at moderate scale (<100M vectors).
- Use cases requiring autocomplete, fuzzy matching, and geospatial constraints integrated with vectors.
- Operational simplicity for D2C platforms or small-scale RAG apps.
**Limitations:**
- Scaling wall around ~100M documents.
- Not supported on Windows; Linux and MacOS only.

## D. Hybrid (HVI + FTS) Strategy
**Architecture:** Orchestrated use of HVI (for billion-scale vectors) and FTS Service (for lexical keywords).
**Primary Signal:** Large-scale Hybrid Search — Text + Semantic at scale exceeding FTS limits.
**Description:** HVI handles the billion-scale vector workload while FTS handles keyword indexing. Results from both are combined and re-ranked at the application layer.
**Best For:**
- Datasets exceeding the FTS memory ceiling (~100M vectors) that still require high-precision text matching.
- Billion-scale RAG or Search apps needing both lexical precision and semantic depth.
**Limitations:**
- Higher operational complexity (orchestrating two services).
- Requires manual re-ranking logic at the application layer.

---

# 2. Differential Diagnosis: Critical Selection Pivots

## Pivot 1: Temporal Scale & Projected Growth
Never assume current document counts are static.
**Scaling Rule:** If the corpus is 10M today but growth projects to >100M in 24–36 months, exclude FTS-only options immediately.
**Infrastructure Risk:** Moving from FTS (Search API) to GSI (SQL++ syntax) is a heavy application rewrite.

## Pivot 2: Filter Selectivity Logic
**HIGH Selectivity (< 20% data remains):** Recommend Composite. If structured filters (e.g., WHERE tenant_id = 'X') eliminate 80%+ of data, pre-filtering avoids millions of unnecessary vector comparisons.
**LOW Selectivity (> 20% data remains):** Recommend Hyperscale. When filters are weak, HVI's simultaneous graph traversal is more efficient than scanning a large, barely-reduced filtered index.

## Pivot 3: Search Persona (Lexical vs. Semantic)
**Native Lexical:** Typos, fuzzy matching, and autocomplete require Search Vector Index.
**Billion-Scale Lexical:** Requires the Hybrid HVI + FTS configuration.

## Pivot 4: The Scale Bottleneck (CVI → HVI Pivot)
CVI performance degrades severely if it begins page swapping due to RAM limitations. If a dataset is large, rapidly growing, or RAM-constrained, the Solution Engineer must pivot to HVI, which uses a 2% DGM disk-centric model.

---

# 3. Performance & Production Trade-offs

| Parameter  | Impact on Production | Description |
|------------|---------------------|-------------|
| nList      | CPU vs. IO | Higher counts improve query throughput and CPU efficiency but increase build time. |
| nProbe     | Recall vs. Latency | Increasing nProbe improves accuracy (Recall) but linearly increases latency and reduces QPS. |
| Reranking  | Accuracy vs. Speed | Enabling persist_full_vector (HVI only) uses exact distances to re-sort results, maximizing accuracy at a significant latency/IOPS cost. |
| Train_list | Recall vs. Build Time | Larger samples produce a better "Codebook" but extend the initial index build duration. |

---

# 4. Silent Hazards: The "What SEs Don't Ask" Problems

**Codebook Drift (CVI):** GSI indexes rely on a Codebook trained on a static sample. Incremental updates cause the map to become non-representative, leading to silent recall degradation. Retraining is not automatic; advise manual "drop and recreate" intervals.

**Memory Pressure (FTS):** In memory-constrained environments, even if an index is under 100M, it may not fit in FTS RAM. Hyperscale is the better choice for high scale with low RAM budgets.

---

# 5. Technical Formulas & Defaults (The Physics)

### 5.1 Training List (`train_list`)
- If N < 10,000 → sample everything.
- Else → `max(10% of N, 10 × nList)`, capped at a strict maximum of **1,000,000**.

### 5.2 Centroids (`nlist`)
- Formula: `round(N / 1000)`
- Centroid Density Sweet Spot: ~1,000 vectors per centroid. >15,000 per centroid triggers excessive I/O.

---

# 6. SE Engagement Protocol: The Diagnostic Conversation

When you identify gaps, call `ask_user`. These are the typical signals worth asking about:

1. **Temporal Volume:** "You have X vectors now; based on your roadmap, where will this volume be in 3 years?"
2. **Selectivity Pressure:** "When you apply filters (like Category or Tenant ID), does that narrow the pool to under 20% of data, or are you searching broadly?"
3. **Lexical Requirement:** "Do you need typos handling and fuzzy keyword matching, or is this purely semantic 'concepts-only' search?"
4. Always provide 3-4 concrete options per question. Explain the tradeoff in natural language.

**CRITICAL — When the user says they don't know:**
If the user signals uncertainty ("unsure", "I don't know", "not sure", "hard to say", "no idea"), you MUST:
- Immediately accept it. Do NOT ask the same question again in different words.
- Record the unknown in `update_state` (add the gap to `resolved_gaps` with value `"unknown"`).
- Apply the safest conservative assumption for that signal (e.g., if selectivity is unknown → assume HVI is safer; if scale is unknown → assume it could exceed 100M).
- Move forward to `evaluate_index_viability` and then `give_recommendation`.
- State your assumption explicitly in the recommendation so the user understands what fallback was used.

---

# 6. Google Search Grounding Rules
Use search ONLY for rigid physical facts (e.g., Couchbase release notes, parameter limits). Never search for architectural decisions — use your internal reasoning and pivots.
