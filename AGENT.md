# Couchbase Vector Index: Expert Strategic Knowledge Base

---

# INDEX TAXONOMY

## A. Hyperscale Vector Index (HVI / BHIVE)

**Architecture:** Proprietary hybrid of IVF clustering, HNSW (routing layer), and Vamana/DiskANN (cluster layer).

**Internal Two-Layer Design:**

- **Routing Layer — HNSW of Centroids (in RAM):** HVI partitions the vector space into IVF clusters. The centroids of these clusters are organized into an **HNSW graph** held entirely in RAM. When a query arrives, this HNSW graph routes it to the most relevant clusters in *O(log N)* time without scanning all centroids linearly. HNSW is present in HVI — but only at this routing/navigation layer.

- **Cluster Layer — Vamana Graph (on SSD):** Inside each cluster, the actual user vectors are connected using a **Vamana graph** (the core algorithm of Microsoft's DiskANN). Vamana is a flat, non-hierarchical graph optimized for SSD-resident data — it performs greedy traversals that minimize random I/O compared to hierarchical approaches like HNSW, making it the correct choice for disk-centric storage.

**Storage Model:** Disk-centric (SSD) for the Vamana vector graph; compact HNSW centroid graph held in RAM (2% DGM ratio).

**Primary Signal:** Massive scale — datasets ranging from hundreds of millions to billions of vectors.

**Description:** HVI separates routing (HNSW in RAM) from search (Vamana on SSD). This two-layer design enables billion-scale search without proportionally large RAM. The HNSW centroid graph keeps query routing fast; the on-disk Vamana graph enables accurate local search within the target clusters with minimal memory overhead.

**Performance Nuance:** Because vector fetching requires SSD I/O, HVI has a slightly higher latency floor than purely in-memory indexes. However, latency remains stable as the dataset grows and does not degrade with scale the way memory-mapped approaches do.

**Best For:**
- Pure vector searches at massive scale (content discovery, recommendations, anomaly detection).
- Deployments where the RAM budget is constrained relative to dataset size.
- Use cases prioritizing high accuracy with low latency at massive scale.

**Limitations:**
- Not optimized for <20% selective filters compared to Composite (where only a small fraction of the corpus is eligible for vector search).
- Slightly higher baseline latency floor due to SSD I/O bound nature.

---

## B. Composite Vector Index (CVI)

**Architecture:** Global Secondary Index (GSI) + FAISS.

**Storage Model:** Standard Plasma engine; requires its full index to reside in RAM to avoid severe latency degradation.

**Primary Signal:** Structured workloads where filters are narrowly scoped — meaning a small percentage of the corpus (typically <20% selective) is eligible for vector search before the ANN step.

**Description:** CVI combines a traditional GSI for structured field filtering with the vector similarity component. It uses "Filter-First" logic, where metadata conditions are applied at the index level before the vector search runs, dramatically reducing the search space for the ANN step.

**Performance Nuance:** CVI is memory-intensive. The entire index should reside in RAM to maintain performance. At massive scale, RAM costs become the primary limiting factor.

**Best For:**
- Workloads where filters narrow the eligible corpus to <20% selective (e.g., only 5% of data passes the category + tenant filter, and 95% is filtered out).
- Multi-tenant architectures with strong per-tenant isolation (e.g., filtering by customer_id or kb_id).
- Search scenarios where customers already run regular SQL queries and need seamless integration.

**Limitations:**
- Not viable at billion-scale without proportionally large RAM budgets.
- Poor fit when the filter is >20% selective (a large fraction of the corpus remains, so the pre-filter doesn't meaningfully shrink the ANN search space).

---

## C. Search Vector Index (FTS)

**Architecture:** Bleve-based Inverted Index.

**Storage Model:** Memory-mapped.

**Primary Signal:** Small-to-medium scale Hybrid Search requiring native lexical relevance (BM25, fuzzy matching, stemming).

**Description:** Integrates vector similarity search into Couchbase's Full Text Search service. It enables queries that combine lexical matching with semantic intent in a single unified index.

**Performance Nuance:** FTS is vertically limited. As vector count approaches the 100M range, memory pressure increases significantly.

**Internal Mechanics:**
- Built on FAISS integrated into FTS.
- Uses **DCP** to stream data from Data Service → Search Service.
- Data stored in **segments**:
  - `persister` → flushes in-memory segments to disk
  - `merger` → consolidates segments + triggers **automatic retraining**
- Uses **snapshotting** for rollback safety.

**Automatic Index Behavior:**
- <1K vectors → Flat (exact search)
- 1K–10K → IVF + Flat
- ≥10K → IVF + Scalar Quantization

**Best For:**
- Applications relying on text relevance plus semantic similarity at moderate scale (<100M vectors).
- Use cases requiring autocomplete, fuzzy matching, and geospatial constraints integrated with vectors.
- Operational simplicity for D2C platforms or small-scale RAG apps.

**Limitations:**
- Scaling wall around ~100M documents.
- Not supported on Windows; Linux and MacOS only.

---

## D. Hybrid (HVI + FTS) Strategy

**Architecture:** Orchestrated use of HVI (for billion-scale vectors) and FTS Service (for lexical keywords).

**Primary Signal:** Large-scale Hybrid Search — Text + Semantic at scale exceeding search vector index limits.

**Description:** HVI handles the billion-scale vector workload while FTS handles keyword indexing. Results from both are combined and re-ranked at the application layer.

**Best For:**
- Datasets exceeding the FTS memory ceiling (~100M vectors) that still require high-precision text matching.
- Billion-scale RAG or Search apps needing both lexical precision and semantic depth.

**Limitations:**
- Higher operational complexity (orchestrating two services).
- Requires manual re-ranking logic at the application layer.

—
## E. Service-First Selection Logic

### The Principle
Adding a vector workload to an existing service (GSI or FTS) is preferred over provisioning a new service to minimize **“Day 1” operational friction**.

### FTS-First
If the user is already running the Search Service, the Search Vector Index is the path of least resistance for workloads <100M.

### GSI-First
If the user already utilizes the Index Service, staying within GSI (HVI or CVI) avoids the overhead of managing a new service.

### Greenfield-First
If no services are currently running, the selection is driven purely by intelligence of the agent.

---

# 2. Differential Diagnosis: Critical Selection Pivots

## Pivot 1: temporal Scale & Projected Growth

Never assume current document counts are static.

**Scaling Rule:** If the corpus is 10M today but growth projects to >100M in 24–36 months, reconsider FTS-only options.

**Infrastructure Risk:** Moving from FTS (Search API) to GSI (SQL++ syntax) is a heavy application rewrite.

---

## Pivot 2: Filter Selectivity Logic

**Definition:** x% selective means x% of the corpus is eligible for vector search; the remaining (100 - x)% is filtered out before ANN runs.

**<20% selective (small eligible pool):** Recommend Composite. If structured filters (e.g., WHERE tenant_id = 'X') allow only <20% of data through, pre-filtering avoids unnecessary vector comparisons across the rest of the corpus.

**≥20% selective (large eligible pool):** Recommend Hyperscale. When a large fraction of the corpus is eligible for vector search, HVI's simultaneous graph traversal is more efficient than scanning a large barely-reduced filtered index.

---

## Pivot 3: Search Persona (Lexical vs. Semantic)

**Native Lexical:** Typos, fuzzy matching, and autocomplete require Search Vector Index.

**Billion-Scale Lexical:** Requires the Hybrid HVI + FTS configuration.

---

## Pivot 4: The Scale Bottleneck (CVI → HVI Pivot)
CVI performance degrades severely if it begins page swapping due to RAM limitations. If a dataset is very large(hundreds of MILLION or BILLION SCALE), user mentioned that it is very rapidly growing, or mentioned explicitly RAM-constrained, the Solution Engineer must pivot to HVI, which uses a 2% DGM disk-centric model.

—
## Pivot 5: Infrastructure Inventory & Neighborhood

Determine if the user has an established footprint in GSI (Index) or FTS (Search).
try to stay in their existing "service neighborhood" unless scale, use case requirements or growth forces a pivot.

---

## Pivot 6: Migration Friction Tiers

Not all index migrations are equal. The effort required depends on whether the migration stays within the same Couchbase service or crosses service boundaries.

**Low Friction — CVI ↔ HVI (same Index Service):**
- Both use the Index Service (GSI).
- Same SQL++ DDL syntax (CREATE INDEX / CREATE VECTOR INDEX).
- Same query function: APPROX_VECTOR_DISTANCE().
- Migration = create a new index definition + rebuild. No application query changes required beyond the DDL.
- Typical effort: hours to a day, depending on dataset size and index build time.

**High Friction — FTS ↔ HVI/CVI (cross-service migration):**
- FTS uses the Search Service; HVI/CVI use the Index Service.
- Completely different query API: SEARCH() function vs APPROX_VECTOR_DISTANCE().
- Application code must be rewritten to use a different query syntax.
- May require provisioning new service nodes if the target service is not already running.
- Typical effort: days to weeks, including application changes, testing, and rollout.

**Implication for recommendations:**
When current signals favor CVI but future scale favors HVI, recommend CVI now — the future migration is cheap. When FTS is involved, weigh the cross-service migration cost heavily before recommending a path that will require it later.



---

# 3. Tunable Parameters & Trade-offs


While the Search Vector Index is managed mostly via the UI, HVI and CVI can be configured manually and tuned across two primary dimensions:

- **Index-Time (Architecture)** – Static parameters defined at index creation.
- **Query-Time (Search Behavior)** – Dynamic parameters applied per query.

When it comes to Hybrid (Hyperscale + FTS), each of the components (Hyperscale and FTS) must be configured independently.

---

## 1. Index-Time Tunables (HVI & CVI)

These parameters define the physical structure and storage characteristics of the index.  
Modifying them typically requires a **rebuild (re-index)**.

| Tunable | Description | Default Value / Formula | Performance Impact |
|----------|-------------|--------------------------|--------------------|
| **Dimension** | Length of the embedding vector (e.g., 768, 1536). Must match the embedding model output exactly. | Fixed (must match embedding model) | Higher dimensions increase storage requirements, memory consumption, and computational cost for both indexing and search. |
| **Similarity** | Distance metric used for scoring. Valid values: `L2_SQUARED` (default), `L2` (Euclidean), `DOT` (Dot Product), `COSINE`. Aliases: `EUCLIDEAN` = `L2`, `EUCLIDEAN_SQUARED` = `L2_SQUARED`. | `L2_SQUARED` | Critical for mathematical correctness. Must align with the embedding model’s training objective to ensure accurate similarity scoring. |
| **Quantization** | Compression technique applied to vectors, set via the `description` WITH clause parameter. Format: `IVF<nlist>,<quantization>` where quantization is `SQ4`, `SQ6`, `SQ8`, or `PQ<subquantizers>x<bits>`. Examples: `IVF256,SQ8`, `IVF,PQ32x8`. Omit nlist number for auto (vectors/1000). SQ options: SQ4 (4-bit, 16 bins), SQ6 (6-bit, 64 bins), SQ8 (8-bit, 256 bins). PQ requires subquantizers to be a divisor of dimensions. | `IVF,SQ8` | Reduces memory and disk footprint by storing compressed representations. SQ8 offers the best recall/memory balance for low-dimensional data. SQ4 suits billion-scale low-dimensional datasets. PQ dramatically reduces memory for high-dimensional data but lowers recall and QPS. |
| **nlist (Centroids)** | Number of clusters used to partition the vector space. | `num_vectors / 1000` | **Behaves differently per index type.** CVI: higher nlist improves QPS and reduces latency. HVI: lower nlist (fewer, larger centroids) tends to perform better because HVI uses algorithms that skip distant vectors within each centroid; increase nlist for HVI only if the working dataset far exceeds the memory quota. Both: higher nlist increases build time and memory overhead. |
| **train_list** | Number of vectors sampled to train quantization clusters. | If total vectors < 10,000: sample all vectors.<br>If ≥ 10,000: `max(num_vectors / 10, 10 × nlist)` capped at 1,000,000. | Higher values improve clustering quality and recall, but significantly increase build time and CPU usage during training. |
| **num_replica** | Number of index replicas maintained for availability and scaling. | `0` | Improves throughput under concurrent workloads and increases fault tolerance, but multiplies storage consumption. |
| **persist_full_vector** | Boolean flag to store original uncompressed vectors alongside compressed representations. | `true` | Required for reranking. Significantly increases disk usage and storage cost. |

---

## 2. Query-Time Tunables (HVI & CVI)

These parameters are applied at query execution time. If not explicitly specified, they use the defaults defined at index creation.  
They do not require rebuilding the index.

| Tunable | Description | Default Value | Performance Impact |
|----------|-------------|---------------|--------------------|
| **nProbe** | Number of clusters searched for a single query. | `1` | Higher values increase recall by expanding search coverage, but reduce throughput and increase latency. |
| **Reranking** | Recomputes similarity using full-precision vectors after approximate search. | `False` | Significantly improves recall and ranking precision. Increases latency and lowers throughput. Requires `persist_full_vector = true`. |
| **topNScan** | Number of candidate vectors evaluated before selecting final results. | Depends on query limit range, typically `40` to `300` | Higher values improve recall by inspecting more candidates, but increase latency and reduce throughput. |
| **Limit** | Number of final results returned to the client application. | `100` | Minimal performance impact, though very large values may slightly increase response size and network cost. |
| **Similarity (Query Override)** | Overrides the default distance metric for a specific query. | Uses index default | Enables flexible ranking logic per request. Must remain consistent with the index configuration for correct scoring. |

---

> **Platform Note:**  
> `topNScan` is available for **Hyperscale Vector Index (HVI) only**.

---

## 3. Search Vector Index Tunables (FTS-Based)

The Search Vector Index (FTS-based) is configured via the UI and exposes high-level optimization controls rather than low-level ANN parameters.

| Setting | Options | Description | Performance Impact |
|----------|------------------|-------------|--------------------|
| **Scoring Model** | `TF-IDF`, `BM25` | Defines how textual relevance scoring is calculated. | `BM25` typically provides better ranking quality for most workloads, while `TF-IDF` is simpler and may be sufficient for basic use cases. |
| **Optimized For** | `latency`, `memory-efficient`, `recall` | Predefined optimization profile that adjusts internal ANN parameters. | **Latency**: Uses default `nlist` and `nProbe`.<br>**Memory-efficient**: Uses `IVFSQ8` to reduce memory footprint.<br>**Recall**: Doubles `nProbe`, improving accuracy but increasing latency. |
| **Number of Replicas** | Depends on number of nodes | Number of index replicas for high availability and scaling. | Improves concurrent query throughput and resilience, but increases storage usage proportionally. |

### Advanced Query-Time Controls (FTS Vector)

| Parameter | Role | Impact |
|----------|------|--------|
| **num_candidates** | Candidates retrieved per shard | **Primary recall lever** |
| **nprobe** | Number of clusters searched | Recall ↑, Latency ↑, QPS ↓ |
| **k** | Final results | Minimal impact |

**Rules:**
- `num_candidates ≥ 3–5 × k`
- Increase `nprobe` to improve recall
- Decrease `nprobe` to improve latency/QPS

**Heuristic:**
nprobe ≈ √nlist

**Quantization Adjustment:**
- 8-bit → default balance
- 4-bit → saves memory but reduces accuracy

Rule:
If using 4-bit, increase `nprobe` by **20–30%**

**Shard Starvation (FTS):**  
If `num_candidates` is too low relative to `k`, shards may drop true top results early, causing poor global recall even when `k` is correct.

---

## Summary of Optimization Trade-offs

Vector index tuning involves balancing three primary objectives: **recall, speed, and cost efficiency**.

---

### To Maximize Accuracy (High Recall)

- Increase **train_list**
- Increase **nProbe**
- Increase **topNScan** (HVI only)
- Keep **persist_full_vector = true**
- Enable **Reranking**
- Use less aggressive **Quantization**
- For Search Index: Use **Optimized For = recall**

**Result:** Highest recall and ranking precision, at the cost of higher latency, lower throughput, longer build times, and increased storage usage.

---

### To Maximize Speed (Low Latency / High Throughput)

- Increase **nlist**
- Keep **nProbe** low
- Keep **topNScan** low (HVI only)
- Avoid **Reranking**
- Use efficient **Quantization**
- For Search Index: Use **Optimized For = latency**

**Result:** Faster responses and better scalability, with reduced recall.

---

### To Minimize Infrastructure Costs (Storage / Memory Efficiency)

- Disable **persist_full_vector**
- Use lower **train_list**
- Use efficient **Quantization** (e.g., `SQ8`)
- Prefer **HVI** over **CVI** for very large datasets (HVI is disk-centric with ~2% memory footprint)
- For Search Index: Use **Optimized For = memory-efficient**

**Result:** Reduced disk and memory footprint, but lower maximum recall and no reranking capability.

---

# 4. Silent Hazards: The "What SEs Don't Ask" Problems

**The Codebook Drift:** GSI indexes rely on a Codebook trained on a static sample. Incremental updates cause the map to become non-representative, leading to silent recall degradation. Retraining is not automatic; advise manual "drop and recreate" intervals.

**Memory Pressure (FTS):** In memory-constrained environments, even if an index is under 100M, it may not fit in FTS RAM. Hyperscale is the better choice for high scale with low RAM budgets.

**Dimensions & Metrics:** Never Assume. Dimensions must match the model output exactly (e.g., 768 or 1536), or data is silently ignored.

**Quick Diagnosis: FTS Vector Issues**

- Slow Index Build
  Segment merging + automatic retraining in progress
- Low Recall
  Increase `nprobe` and `num_candidates`
  Verify similarity metric
- Hisgh Memory Usage
  Use `memory-efficient` (4-bit quantization)

---

# 5. SE Engagement Protocol: The Diagnostic Conversation

1. **Infrastructure Audit (MANDATORY FIRST QUESTION):**  
   "Are you already running Couchbase in production? If yes, which services are currently active (GSI/Index, FTS/Search, or both)?"  
   *Goal:* Keep the recommendation within the customer's existing service neighborhood whenever possible to minimize operational friction.
2. **Temporal Volume:**  
   "You have X vectors now; based on your roadmap, where will this volume be in 3 years?"

3. **Selectivity Pressure:**
   "When you apply filters (like Category or Tenant ID), what percentage of your total data remains eligible for vector search? For example, if you filter by a single tenant, what fraction of all documents belong to that tenant?"

4. **Lexical Requirement:**  
   "Do you need typos handling and fuzzy keyword matching, or is this purely semantic 'concepts-only' search?"

6. **MANDATORY Use Case Reference:**  
   You MUST call `use_case_search` at least once before calling `give_recommendation`. Use the library to understand the thinking and decision-making patterns of similar cases, but do not treat them as ground truth. Use this as additional info alongside your own reasoning and intelligence to make the final decision.

7. **MANDATORY Options Rule:**  
   For every diagnostic question above, provide **3–4 concrete answer options** and clearly explain the architectural tradeoff of each choice in natural language.

---

# 6. Google Search Grounding Rules
Use search ONLY for rigid physical facts (e.g., Couchbase release notes, parameter limits). Never search for architectural decisions — use your internal reasoning and pivots.
