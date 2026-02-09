import os
from dotenv import load_dotenv

load_dotenv()


def _require(var: str) -> str:
    val = os.getenv(var)
    if not val:
        raise RuntimeError(f"Missing required environment variable: {var}")
    return val


YOUTUBE_API_KEY = _require("YOUTUBE_API_KEY")
ANTHROPIC_API_KEY = _require("ANTHROPIC_API_KEY")
YOUTUBE_CHANNEL_HANDLE = os.getenv("YOUTUBE_CHANNEL_HANDLE", "RhinoFinance")

SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SENDER_EMAIL = _require("SENDER_EMAIL")
SENDER_PASSWORD = _require("SENDER_PASSWORD")
RECIPIENT_EMAIL = _require("RECIPIENT_EMAIL")

POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "3600"))

PROCESSED_VIDEOS_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "processed_videos.json"
)
