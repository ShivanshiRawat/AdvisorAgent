"""
Configuration for the Vector Index Advisor agent.

Secrets (API keys, passwords) come from .env.
Everything else comes from INI files under config/.

The active LLM provider is selected via config/llm.ini [provider] name
or the LLM_PROVIDER environment variable.
"""

import configparser
import os
from pathlib import Path

# ---------------------------------------------------------------------------
# .env loader (secrets only)
# ---------------------------------------------------------------------------

def _load_dotenv() -> None:
    """Load .env file from the same directory if it exists."""
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if not os.path.isfile(env_path):
        return
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip("'\"")
            if key:
                os.environ[key] = value


_load_dotenv()

# ---------------------------------------------------------------------------
# INI loader helper
# ---------------------------------------------------------------------------

_CONFIG_DIR = Path(__file__).parent / "config"


def _read_ini(filename: str) -> configparser.ConfigParser:
    cp = configparser.ConfigParser()
    cp.read(_CONFIG_DIR / filename)
    return cp


# ---------------------------------------------------------------------------
# LLM provider  (config/llm.ini)
# ---------------------------------------------------------------------------

_llm = _read_ini("llm.ini")

LLM_PROVIDER: str = os.environ.get(
    "LLM_PROVIDER", _llm.get("provider", "name", fallback="gemini"),
).lower()

# Common LLM settings
TEMPERATURE: float = _llm.getfloat("common", "temperature", fallback=0.3)
MAX_LOOPS:   int   = _llm.getint("loop", "max_loops", fallback=12)
MAX_RETRIES: int   = _llm.getint("loop", "max_exception_retries", fallback=2)

# Provider-specific model name (section matches provider name)
MODEL: str = os.environ.get(
    "VIA_MODEL", _llm.get(LLM_PROVIDER, "model", fallback=""),
)

# Gemini-specific (harmlessly ignored by other providers)
THINKING_BUDGET: int = _llm.getint("gemini", "thinking_budget", fallback=0)

# ---------------------------------------------------------------------------
# API key resolution  (provider → env-var name)
# ---------------------------------------------------------------------------

_KEY_ENV_MAP = {
    "gemini": "GEMINI_API_KEY",
    "openai": "OPENAI_API_KEY",
}

_key_env_var = _KEY_ENV_MAP.get(LLM_PROVIDER, f"{LLM_PROVIDER.upper()}_API_KEY")
LLM_API_KEY: str = os.environ.get(_key_env_var, "")

if not LLM_API_KEY:
    raise EnvironmentError(
        f"{_key_env_var} is not set. Add it to your .env file "
        f"(current LLM_PROVIDER={LLM_PROVIDER})."
    )

# ---------------------------------------------------------------------------
# Couchbase  (config/couchbase.ini  +  CB_PASSWORD from .env)
# ---------------------------------------------------------------------------

_cb = _read_ini("couchbase.ini")

CB_HOST:       str = _cb.get("connection", "host")
CB_USERNAME:   str = _cb.get("connection", "username")
CB_PASSWORD:   str = os.environ.get("CB_PASSWORD", "password")
CB_TIMEOUT:    int = _cb.getint("connection", "timeout_seconds")

CB_BUCKET:     str = _cb.get("conversation_storage", "bucket")
CB_SCOPE:      str = _cb.get("conversation_storage", "scope")
CB_COLLECTION: str = _cb.get("conversation_storage", "collection")

CB_BENCH_BUCKET:     str = _cb.get("benchmark", "bucket")
CB_BENCH_SCOPE:      str = _cb.get("benchmark", "scope")
CB_BENCH_COLLECTION: str = _cb.get("benchmark", "collection")

# ---------------------------------------------------------------------------
# Performance bins / thresholds  (config/performance.ini)
# ---------------------------------------------------------------------------

_pf = _read_ini("performance.ini")

RECALL_LOW_MAX:       float = _pf.getfloat("recall",  "low_max")
RECALL_MODERATE_MAX:  float = _pf.getfloat("recall",  "moderate_max")

QPS_LOW_MAX:          float = _pf.getfloat("qps",     "low_max")
QPS_MODERATE_MAX:     float = _pf.getfloat("qps",     "moderate_max")

LATENCY_LOW_MAX:      float = _pf.getfloat("latency", "low_max")
LATENCY_MODERATE_MAX: float = _pf.getfloat("latency", "moderate_max")

BENCH_RESULT_LIMIT:   int   = _pf.getint("benchmark_query", "result_limit")
