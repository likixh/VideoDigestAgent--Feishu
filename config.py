import logging
import os
import sys

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


def _require(var: str) -> str:
    val = os.getenv(var)
    if not val:
        raise RuntimeError(f"Missing required environment variable: {var}")
    return val


def _require_for_provider(var: str, provider: str) -> str:
    """Only require a variable if the active LLM_PROVIDER needs it."""
    val = os.getenv(var)
    if not val:
        raise RuntimeError(
            f"Missing required environment variable: {var} "
            f"(needed for LLM_PROVIDER={provider})"
        )
    return val


# ── YouTube ──────────────────────────────────────────────
YOUTUBE_API_KEY = _require("YOUTUBE_API_KEY")

_raw_channels = os.getenv("YOUTUBE_CHANNELS", "")
if not _raw_channels.strip():
    raise RuntimeError(
        "Missing required environment variable: YOUTUBE_CHANNELS. "
        "Set it to a comma-separated list of YouTube handles (without @)."
    )
YOUTUBE_CHANNELS: list[str] = [
    ch.strip() for ch in _raw_channels.split(",") if ch.strip()
]

# ── LLM Provider ────────────────────────────────────────
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "gemini").lower()

_VALID_PROVIDERS = ("gemini", "openai", "anthropic")
if LLM_PROVIDER not in _VALID_PROVIDERS:
    print(
        f"Error: Unknown LLM_PROVIDER '{LLM_PROVIDER}'. "
        f"Choose from: {', '.join(_VALID_PROVIDERS)}",
        file=sys.stderr,
    )
    sys.exit(1)

try:
    if LLM_PROVIDER == "gemini":
        GEMINI_API_KEY = _require_for_provider("GEMINI_API_KEY", "gemini")
        GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
    elif LLM_PROVIDER == "openai":
        OPENAI_API_KEY = _require_for_provider("OPENAI_API_KEY", "openai")
        OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    elif LLM_PROVIDER == "anthropic":
        ANTHROPIC_API_KEY = _require_for_provider("ANTHROPIC_API_KEY", "anthropic")
        ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-5-20250929")
except RuntimeError as e:
    print(f"Error: {e}", file=sys.stderr)
    print(
        "Hint: Make sure the API key is set in your .env file or environment.",
        file=sys.stderr,
    )
    sys.exit(1)

# ── Summary Languages ────────────────────────────────────
_raw_langs = os.getenv("SUMMARY_LANGUAGES", "English")
_all_langs = [lang.strip() for lang in _raw_langs.split(",") if lang.strip()]
if len(_all_langs) > 2:
    logger.warning(
        "SUMMARY_LANGUAGES has %d languages but max is 2. "
        "Only using the first two: %s",
        len(_all_langs),
        ", ".join(_all_langs[:2]),
    )
SUMMARY_LANGUAGES: list[str] = _all_langs[:2]

# ── Verification ─────────────────────────────────────────
VERIFY_SUMMARY = os.getenv("VERIFY_SUMMARY", "false").lower() in ("true", "1", "yes")

# ── Email ────────────────────────────────────────────────
SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SENDER_EMAIL = _require("SENDER_EMAIL")
SENDER_PASSWORD = _require("SENDER_PASSWORD")

# Support multiple recipients (comma-separated), with backwards compatibility
_raw_recipients = os.getenv("RECIPIENT_EMAILS", "") or os.getenv("RECIPIENT_EMAIL", "")
if not _raw_recipients.strip():
    raise RuntimeError(
        "Missing required environment variable: RECIPIENT_EMAILS (or RECIPIENT_EMAIL). "
        "Set it to one or more email addresses, comma-separated."
    )
RECIPIENT_EMAILS: list[str] = [
    r.strip() for r in _raw_recipients.split(",") if r.strip()
]

# ── Polling ──────────────────────────────────────────────
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "3600"))

PROCESSED_VIDEOS_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "processed_videos.json"
)

# ── Pipeline Engine ─────────────────────────────────────
# Choose which summarization pipeline to use:
#   "default"   — Original sequential pipeline (summarizer.py)
#   "langgraph" — LangGraph state machine with RAG, quality checks, retry logic
#   "crewai"    — CrewAI multi-agent crew (researcher → analyst → writer → fact-checker)
PIPELINE_ENGINE = os.getenv("PIPELINE_ENGINE", "default").lower()

_VALID_ENGINES = ("default", "langgraph", "crewai")
if PIPELINE_ENGINE not in _VALID_ENGINES:
    print(
        f"Error: Unknown PIPELINE_ENGINE '{PIPELINE_ENGINE}'. "
        f"Choose from: {', '.join(_VALID_ENGINES)}",
        file=sys.stderr,
    )
    sys.exit(1)

# ── RAG (Retrieval-Augmented Generation) ────────────────
# Enable RAG to give the summarizer context from previous videos.
# Requires: pip install chromadb
RAG_ENABLED = os.getenv("RAG_ENABLED", "false").lower() in ("true", "1", "yes")

# ── Prediction Tracking ──────────────────────────────────
# Extracts stock/crypto predictions from summaries,
# fetches actual market data, and scores accuracy.
# Requires: pip install yfinance
PREDICTION_TRACKING = os.getenv("PREDICTION_TRACKING", "false").lower() in ("true", "1", "yes")
