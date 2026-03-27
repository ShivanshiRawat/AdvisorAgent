"""
Configuration for the Vector Index Advisor agent.

Secrets (API keys, passwords) come from .env.
Everything else comes from INI files under config/.
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
# Secrets (.env)
# ---------------------------------------------------------------------------

GEMINI_API_KEY: str = os.environ.get("GEMINI_API_KEY", "")

if not GEMINI_API_KEY:
    raise EnvironmentError(
        "GEMINI_API_KEY is not set. Add it to your .env file."
    )

LLM_PROVIDER = "gemini"

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
# Gemini / LLM  (config/gemini.ini)
# ---------------------------------------------------------------------------

_gm = _read_ini("gemini.ini")

MODEL:           str   = os.environ.get("VIA_MODEL", _gm.get("model", "name"))
TEMPERATURE:     float = _gm.getfloat("model", "temperature")
THINKING_BUDGET: int   = _gm.getint("model", "thinking_budget")

MAX_LOOPS:    int = _gm.getint("loop", "max_loops")
MAX_RETRIES:  int = _gm.getint("loop", "max_exception_retries")

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
