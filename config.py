import os
from dotenv import load_dotenv

load_dotenv()


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

if LLM_PROVIDER == "gemini":
    GEMINI_API_KEY = _require_for_provider("GEMINI_API_KEY", "gemini")
    GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
elif LLM_PROVIDER == "openai":
    OPENAI_API_KEY = _require_for_provider("OPENAI_API_KEY", "openai")
    OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
elif LLM_PROVIDER == "anthropic":
    ANTHROPIC_API_KEY = _require_for_provider("ANTHROPIC_API_KEY", "anthropic")
    ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-5-20250929")
else:
    raise RuntimeError(
        f"Unknown LLM_PROVIDER: '{LLM_PROVIDER}'. "
        "Choose from: gemini, openai, anthropic"
    )

# ── Summary Languages ────────────────────────────────────
_raw_langs = os.getenv("SUMMARY_LANGUAGES", "English")
SUMMARY_LANGUAGES: list[str] = [
    lang.strip() for lang in _raw_langs.split(",") if lang.strip()
][:2]  # max 2 languages

# ── Email ────────────────────────────────────────────────
SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SENDER_EMAIL = _require("SENDER_EMAIL")
SENDER_PASSWORD = _require("SENDER_PASSWORD")
RECIPIENT_EMAIL = _require("RECIPIENT_EMAIL")

# ── Polling ──────────────────────────────────────────────
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "3600"))

PROCESSED_VIDEOS_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "processed_videos.json"
)
