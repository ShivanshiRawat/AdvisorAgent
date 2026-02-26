# Vector Index Advisor (VIA) – Testing App

Conversational agent that recommends Couchbase vector index type (Hyperscale, Composite, Search Vector Index, Hybrid) and base configuration from a free-form use-case description. Uses OpenAI and Gradio.

## Setup

```bash
cd via_agent
python -m venv .venv
source .venv/bin/activate   # or .venv\Scripts\activate on Windows
pip install -r requirements.txt
```

Set your OpenAI API key:

```bash
export OPENAI_API_KEY="sk-..."
```

Or create a `.env` file with `OPENAI_API_KEY=sk-...` and load it (e.g. with `python-dotenv`) if you add that dependency.

## Run

```bash
python app.py
```

Open the URL shown in the terminal (e.g. http://127.0.0.1:7860).

## Usage

1. Type or paste your use case in free form (scale, query patterns, filters, latency, etc.).
2. The agent either:
   - **Recommends** an index type and base config, or
   - **Asks a clarification** with recommended options; select one or choose "Other (type your answer)" and submit.
3. Continue until you get a recommendation.

## Project layout

- `knowledge.py` – Condensed decision knowledge (system prompt).
- `agent.py` – Session state, OpenAI call, response parsing (clarification vs recommendation).
- `app.py` – Gradio UI: chat + clarification panel (Radio options + Other).
