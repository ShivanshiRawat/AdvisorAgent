"""
Configuration for the Vector Index Advisor agent.
Gemini is the primary provider.
"""
import os


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

# --- Primary Provider: Gemini ---
GEMINI_API_KEY: str = os.environ.get("GEMINI_API_KEY", "")

if not GEMINI_API_KEY:
    raise EnvironmentError(
        "GEMINI_API_KEY is not set. Add it to your .env file."
    )

LLM_PROVIDER = "gemini"

# Model: override with VIA_MODEL env var if needed
# gemini-2.5-flash is the default — fast, highly capable, great tool-use
MODEL: str = os.environ.get("VIA_MODEL", "gemini-2.5-flash")

# Limits
MAX_DOC_TOKENS: int = 4000
MAX_RETRIES: int = 2

# --- Couchbase Storage (conversation persistence for debugging) ---
CB_HOST:       str = os.environ.get("CB_HOST",       "localhost")
CB_USERNAME:   str = os.environ.get("CB_USERNAME",   "Administrator")
CB_PASSWORD:   str = os.environ.get("CB_PASSWORD",   "password")
CB_BUCKET:     str = os.environ.get("CB_BUCKET",     "advisor")
CB_SCOPE:      str = os.environ.get("CB_SCOPE",      "conversations")
CB_COLLECTION: str = os.environ.get("CB_COLLECTION", "chats")
