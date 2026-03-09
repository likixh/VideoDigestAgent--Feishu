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
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY", "")

_raw_channels = os.getenv("YOUTUBE_CHANNELS", "")
YOUTUBE_CHANNELS: list[str] = [
    ch.strip() for ch in _raw_channels.split(",") if ch.strip()
]

# ── YouTube Search (optional) ────────────────────────────
_raw_search_queries = os.getenv("YOUTUBE_SEARCH_QUERIES", "")
YOUTUBE_SEARCH_QUERIES: list[str] = [
    q.strip() for q in _raw_search_queries.split(",") if q.strip()
]
YOUTUBE_SEARCH_ENABLED = len(YOUTUBE_SEARCH_QUERIES) > 0
YOUTUBE_SEARCH_MAX_RESULTS = int(os.getenv("YOUTUBE_SEARCH_MAX_RESULTS", "5"))
YOUTUBE_SEARCH_INTERVAL = int(os.getenv("YOUTUBE_SEARCH_INTERVAL", "14400"))
YOUTUBE_SEARCH_QUOTA_BUDGET = int(os.getenv("YOUTUBE_SEARCH_QUOTA_BUDGET", "5000"))

_raw_relevance = os.getenv(
    "YOUTUBE_SEARCH_RELEVANCE_KEYWORDS",
    "AI,artificial intelligence,machine learning,deep learning,LLM,GPT,"
    "neural network,transformer,AGI,GenAI",
)
YOUTUBE_SEARCH_RELEVANCE_KEYWORDS: list[str] = [
    k.strip().lower() for k in _raw_relevance.split(",") if k.strip()
]
YOUTUBE_SEARCH_MIN_DURATION = int(os.getenv("YOUTUBE_SEARCH_MIN_DURATION", "10"))
YOUTUBE_SEARCH_MAX_TOTAL = int(os.getenv("YOUTUBE_SEARCH_MAX_TOTAL", "15"))
YOUTUBE_SEARCH_MIN_VIEWS = int(os.getenv("YOUTUBE_SEARCH_MIN_VIEWS", "1000"))

# ── Bilibili (optional) ─────────────────────────────────────
BILIBILI_ENABLED = os.getenv("BILIBILI_ENABLED", "false").lower() in ("true", "1", "yes")

BILIBILI_SESSDATA = os.getenv("BILIBILI_SESSDATA", "")
BILIBILI_BILI_JCT = os.getenv("BILIBILI_BILI_JCT", "")
BILIBILI_BUVID3 = os.getenv("BILIBILI_BUVID3", "")

_raw_bilibili_users = os.getenv("BILIBILI_USERS", "")
BILIBILI_USERS: list[str] = [
    u.strip() for u in _raw_bilibili_users.split(",") if u.strip()
]

# YouTube API key is required if any YouTube source is configured
if (YOUTUBE_CHANNELS or YOUTUBE_SEARCH_ENABLED) and not YOUTUBE_API_KEY:
    raise RuntimeError(
        "Missing required environment variable: YOUTUBE_API_KEY "
        "(needed because YOUTUBE_CHANNELS or YOUTUBE_SEARCH_QUERIES is set)"
    )

# At least one video source must be configured
if not YOUTUBE_CHANNELS and not YOUTUBE_SEARCH_ENABLED and not BILIBILI_ENABLED:
    raise RuntimeError(
        "No video sources configured. Set YOUTUBE_CHANNELS, "
        "YOUTUBE_SEARCH_QUERIES, and/or BILIBILI_ENABLED in your .env file."
    )

# ── LLM Provider ────────────────────────────────────────
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "gemini").lower()

_VALID_PROVIDERS = ("gemini", "openai", "anthropic", "openrouter")
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
        GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.1-pro-preview")
        # Ordered fallback chain when the primary model hits quota / rate-limit.
        # Set to empty string to disable fallback.
        _raw_fallbacks = os.getenv(
            "GEMINI_FALLBACK_MODELS",
            "gemini-3-pro-preview,gemini-3-flash-preview,gemini-2.5-pro,gemini-2.5-flash,gemini-2.5-flash-lite",
        )
        GEMINI_FALLBACK_MODELS: list[str] = [
            m.strip() for m in _raw_fallbacks.split(",") if m.strip()
        ]
    elif LLM_PROVIDER == "openai":
        OPENAI_API_KEY = _require_for_provider("OPENAI_API_KEY", "openai")
        OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    elif LLM_PROVIDER == "anthropic":
        ANTHROPIC_API_KEY = _require_for_provider("ANTHROPIC_API_KEY", "anthropic")
        ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-5-20250929")
    elif LLM_PROVIDER == "openrouter":
        OPENROUTER_API_KEY = _require_for_provider("OPENROUTER_API_KEY", "openrouter")
        OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini")
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

# ── Output Mode ─────────────────────────────────────────
# "email" — send email only (default, original behaviour)
# "local" — save markdown file only (no email credentials needed)
# "both"  — send email AND save locally
OUTPUT_MODE = os.getenv("OUTPUT_MODE", "email").lower()
_VALID_OUTPUT_MODES = ("email", "local", "both", "feishu")
if OUTPUT_MODE not in _VALID_OUTPUT_MODES:
    print(
        f"Error: Unknown OUTPUT_MODE '{OUTPUT_MODE}'. "
        f"Choose from: {', '.join(_VALID_OUTPUT_MODES)}",
        file=sys.stderr,
    )
    sys.exit(1)

# ── Email ────────────────────────────────────────────────
# These are only required when OUTPUT_MODE includes email.
SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))

if OUTPUT_MODE in ("email", "both"):
    SENDER_EMAIL = _require("SENDER_EMAIL")
    # Replace non-breaking spaces (\xa0) with regular spaces in the password.
    # Copy-pasting Google App Passwords from the web often introduces \xa0
    # between the 4-character groups, which causes smtplib's AUTH PLAIN to fail
    # with: UnicodeEncodeError: 'ascii' codec can't encode character '\xa0'
    SENDER_PASSWORD = _require("SENDER_PASSWORD").replace("\xa0", " ")

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
else:
    SENDER_EMAIL = os.getenv("SENDER_EMAIL", "")
    SENDER_PASSWORD = os.getenv("SENDER_PASSWORD", "")
    RECIPIENT_EMAILS: list[str] = []

# ── Polling ──────────────────────────────────────────────
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "3600"))

PROCESSED_VIDEOS_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "processed_videos.json"
)
SEARCH_STATE_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "search_state.json"
)
CHANNEL_CACHE_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "channel_cache.json"
)

# ── Feishu ──────────────────────────────────────────────
FEISHU_WEBHOOK_URL = os.getenv("FEISHU_WEBHOOK_URL", "")
FEISHU_SECRET = os.getenv("FEISHU_SECRET", "")

