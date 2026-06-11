# Vector Index Advisor (VIA)

A conversational AI agent that recommends Couchbase vector index types and configurations based on your specific use case. VIA uses a ReAct-based reasoning loop, multi-turn conversation with working memory, and a structured tool ecosystem to deliver precise, justified index recommendations.

---

## Overview

VIA helps engineers and architects choose the right Couchbase vector index type — **HVI (Hyperscale Vector Index)**, **CVI (Composite Vector Index)**, **FTS Search Vector Index**, or a **Hybrid** approach — by gathering workload signals through guided conversation and applying deterministic decision logic.

The agent:
- Asks targeted clarifying questions before recommending
- Reasons step-by-step using a ReAct loop (think → plan → act → observe)
- Maintains working memory across the conversation
- Provides parameter baselines (nlist, train_list, nProbe, quantization)
- Generates ready-to-use SQL++ query and index DDL templates
- Persists conversation history to Couchbase for observability

---

## Architecture

```
app.py (Chainlit UI)
    └── agent/core.py          ← public entry point, history management
        └── agent/llm_loop.py  ← ReAct loop (up to 12 iterations)
            ├── agent/providers/gemini.py   ← Gemini LLM provider
            ├── agent/providers/openai.py   ← OpenAI LLM provider (alternative)
            └── tools/dispatcher.py         ← routes tool calls
                ├── tools/domain.py         ← index logic & evaluation
                ├── tools/reasoning.py      ← think / plan / update_state
                └── similarity/engine.py    ← use-case library matching

storage/conversation_store.py  ← persists turns to Couchbase
prompts/system_prompt.py       ← role, protocol, guardrails
AGENT.md                       ← expert knowledge base (loaded at runtime)
config.py                      ← environment & INI config loader
```

### ReAct Loop

Each user turn runs a loop (max 12 iterations):
1. LLM reasons with `think` / `plan` tools
2. LLM calls domain tools (evaluate, compare, search use cases, get parameters)
3. Loop terminates when LLM calls a **terminal tool**: `ask_user`, `give_recommendation`, or `give_performance_profile`

### Working Memory

Two-layer hybrid memory per session:
- **Episodic history** — raw message list (last 12 messages kept; middle compressed into narrative summary)
- **Structured state** — `confirmed_facts`, `open_gaps`, `resolved_gaps`, `query_patterns`, `narrative_summary`, `reasoning_so_far`

State is serialized as JSON and injected as the first message of every LLM call.

---

## Project Structure

```
AdvisorAgent/
├── app.py                        # Chainlit application entry point
├── config.py                     # Config loader (env + INI)
├── AGENT.md                      # Expert knowledge base for the agent
├── requirements.txt
│
├── agent/
│   ├── core.py                   # run_turn() — public API
│   ├── llm_loop.py               # ReAct loop implementation
│   └── providers/
│       ├── base.py               # BaseLLMProvider abstract class
│       ├── gemini.py             # Google Gemini provider (default)
│       └── openai.py             # OpenAI provider (alternative)
│
├── tools/
│   ├── dispatcher.py             # Tool routing + argument normalisation
│   ├── domain.py                 # Core index evaluation logic
│   ├── reasoning.py              # think / plan / update_state tools
│   └── schemas.py                # All tool schemas (OpenAI JSON Schema format)
│
├── prompts/
│   └── system_prompt.py          # Full system prompt
│
├── similarity/
│   └── engine.py                 # 10-dimensional weighted cosine similarity
│
├── storage/
│   └── conversation_store.py     # Couchbase persistence layer
│
├── public/
│   ├── custom.css                # UI styling overrides
│   └── custom.js                 # Clipboard polyfill for HTTP deployments
│
└── .chainlit/
    ├── config.toml               # Chainlit configuration
    └── translations/
        └── en-US.json            # UI string overrides
```

---

## Setup

### Prerequisites

- Python 3.10+
- A Google Gemini API key **or** OpenAI API key
- A Couchbase cluster (for conversation persistence — optional but recommended)

### Installation

```bash
git clone <repo-url>
cd AdvisorAgent

python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

pip install -r requirements.txt
```

### Environment Variables

Create a `.env` file in the project root:

```env
# LLM Provider — "gemini" (default) or "openai"
LLM_PROVIDER=gemini

# API key for the chosen provider
LLM_API_KEY=your-api-key-here

# Model name
# Gemini: gemini-2.5-pro  |  OpenAI: gpt-4o
MODEL=gemini-2.5-pro

# Agent loop settings
MAX_LOOPS=12
MAX_RETRIES=3
TEMPERATURE=0.2
THINKING_BUDGET=8000

# Couchbase connection (for conversation persistence)
CB_HOST=couchbase://your-cluster-host
CB_USERNAME=your-username
CB_PASSWORD=your-password
CB_BUCKET=your-bucket
CB_SCOPE=your-scope
CB_COLLECTION=your-collection
```

All Couchbase variables are optional. If omitted or if the connection fails, the agent runs without persistence (conversations are not saved).

---

## Running the App

```bash
chainlit run app.py
```

Open the URL shown in the terminal (default: `http://127.0.0.1:8000`).

### Running on a Remote Server (AWS / HTTP)

The app is configured for HTTP deployments out of the box:

- WebSocket transport is forced in `.chainlit/config.toml` to bypass polling handshake issues on AWS
- `public/custom.js` includes a clipboard polyfill that falls back to `execCommand` when `window.isSecureContext` is `false` (i.e., plain HTTP)

No extra flags are needed. Just run:

```bash
chainlit run app.py --host 0.0.0.0 --port 8000
```

---

## Usage

1. **Start a conversation** — describe your use case in plain English. Include any details you know: scale, query patterns, filter usage, latency targets, infrastructure constraints.

2. **Answer clarifying questions** — VIA will ask 1–3 focused questions using button options. Select an option or type a custom answer.

3. **Receive a recommendation** — once enough signals are gathered, VIA delivers:
   - Recommended index type with justification
   - Base configuration parameters (nlist, train_list, nProbe, quantization, replicas)
   - SQL++ query and index DDL templates
   - Similar use cases from the reference library
   - Caveats and next steps

4. **Iterate** — you can continue the conversation to compare alternatives, explore tradeoffs, or adjust parameters.

### Example Conversation Starters

- *"We have 50M vectors, run semantic search with no filters, p99 latency must be under 50ms."*
- *"Building a product recommendation engine with 10M items, heavy category filtering, using Capella."*
- *"We need hybrid keyword + vector search for a document retrieval system at 5M docs."*

---

## Tools

The agent has access to 13 tools:

| Tool | Purpose |
|------|---------|
| `think` | Explicit reasoning step (visible in Chain of Thought) |
| `plan` | Multi-step plan before acting |
| `update_state` | Update working memory (confirmed facts, gaps, summary) |
| `evaluate_index_viability` | Deterministic viability report for a given index + workload |
| `compare_indexes` | Side-by-side comparison of two index types |
| `get_default_parameters` | Compute nlist, train_list, nProbe baselines |
| `get_index_queries` | Generate SQL++ DDL and query templates |
| `use_case_search` | Find similar reference cases from the library |
| `get_index_info` | Retrieve detailed documentation for an index type |
| `ask_user` | Ask a clarifying question with multiple-choice options (terminal) |
| `give_recommendation` | Deliver final index recommendation (terminal) |
| `give_performance_profile` | Deliver performance analysis (terminal) |
| `web_search` | Search for current Couchbase documentation or release notes |

---

## Index Types

| Index | Best For | Key Constraint |
|-------|----------|----------------|
| **HVI** (Hyperscale Vector Index) | >10M vectors, low-filter workloads, disk-centric | Filters applied post-ANN; avoid if selectivity > 20% |
| **CVI** (Composite Vector Index) | <10M vectors, highly selective filters | In-memory FAISS; watch RAM budget |
| **FTS Search Vector Index** | Hybrid keyword + vector, full-text search | Managed by FTS service; separate tuning |
| **Hybrid** (HVI + FTS) | Mixed keyword/semantic retrieval at scale | Operational overhead of two indexes |

---

## Configuration Reference

| Variable | Default | Description |
|----------|---------|-------------|
| `LLM_PROVIDER` | `gemini` | `gemini` or `openai` |
| `MODEL` | `gemini-2.5-pro` | Model identifier |
| `TEMPERATURE` | `0.2` | LLM temperature |
| `MAX_LOOPS` | `12` | Max ReAct iterations per turn |
| `MAX_RETRIES` | `3` | Retries on LLM/tool errors |
| `THINKING_BUDGET` | `8000` | Token budget for extended thinking (Gemini) |
| `CB_HOST` | — | Couchbase connection string |
| `CB_USERNAME` | — | Couchbase username |
| `CB_PASSWORD` | — | Couchbase password |
| `CB_BUCKET` | — | Bucket name |
| `CB_SCOPE` | — | Scope name |
| `CB_COLLECTION` | — | Collection for conversation storage |

---

## Conversation Persistence

Each conversation turn is stored in Couchbase under the key `via_conversation::{session_id}` with the following structure:

```json
{
  "session_id": "...",
  "turns": [
    {
      "timestamp": "2024-01-15T10:30:00Z",
      "user_message": "...",
      "reasoning_trace": [...],
      "response_type": "ask_user | give_recommendation | give_performance_profile",
      "response_payload": {...},
      "state_snapshot": {...}
    }
  ]
}
```

The session ID is displayed at the start of each conversation and can be copied from the UI.

---

## Dependencies

Key packages (see `requirements.txt` for full list):

- `chainlit` — conversational UI framework
- `google-genai` — Gemini SDK
- `openai` — OpenAI SDK
- `couchbase` — Couchbase Python SDK
- `python-dotenv` — `.env` file loading

---

## License

Internal tooling — Couchbase, Inc.
