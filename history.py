"""Track video processing history — replaces the simple processed_videos.json.

Stores richer metadata per video to support --history, --retry, and local summary files.
"""

import json
import logging
import os
from datetime import datetime, timezone

import config

logger = logging.getLogger(__name__)

HISTORY_FILE = config.PROCESSED_VIDEOS_FILE  # reuse same path
SUMMARIES_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "summaries"
)


def _load_history() -> dict[str, dict]:
    """Load full history. Returns {video_id: {...metadata}}."""
    if not os.path.exists(HISTORY_FILE):
        return {}
    with open(HISTORY_FILE, "r") as f:
        data = json.load(f)
    # Migrate from old format (plain list of IDs)
    if isinstance(data, list):
        return {vid_id: {"status": "sent", "title": "", "channel": ""} for vid_id in data}
    return data


def _save_history(history: dict[str, dict]) -> None:
    with open(HISTORY_FILE, "w") as f:
        json.dump(history, f, indent=2, ensure_ascii=False)


MAX_RETRIES = 3


def get_processed_ids() -> set[str]:
    """Return video IDs that should be skipped when checking for new videos.

    Includes sent and init (seen) videos.  Failed videos are excluded so they
    get automatically retried, unless they have already failed MAX_RETRIES times.
    """
    history = _load_history()
    return {
        vid_id
        for vid_id, meta in history.items()
        if meta.get("status") != "failed"
        or meta.get("retry_count", 1) >= MAX_RETRIES
    }


def mark_sent(video_id: str, title: str, channel: str, source: str = "channel",
              platform: str = "youtube") -> None:
    history = _load_history()
    history[video_id] = {
        "status": "sent",
        "title": title,
        "channel": channel,
        "source": source,
        "platform": platform,
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
    }
    _save_history(history)


def mark_failed(video_id: str, title: str, channel: str, error: str,
                source: str = "channel", platform: str = "youtube") -> None:
    history = _load_history()
    prev = history.get(video_id, {})
    retry_count = prev.get("retry_count", 0) + 1
    history[video_id] = {
        "status": "failed",
        "title": title,
        "channel": channel,
        "source": source,
        "platform": platform,
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
        "error": error,
        "retry_count": retry_count,
    }
    _save_history(history)
    if retry_count >= MAX_RETRIES:
        logger.warning("Video %s has failed %d times — will not auto-retry", video_id, retry_count)


def mark_seen(video_id: str) -> None:
    """Mark a video as seen during initialization (no processing)."""
    history = _load_history()
    if video_id not in history:
        history[video_id] = {
            "status": "init",
            "title": "",
            "channel": "",
            "date": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
        }
        _save_history(history)


def get_failed_videos() -> list[dict]:
    """Return list of videos that failed processing."""
    history = _load_history()
    failed = []
    for vid_id, meta in history.items():
        if meta.get("status") == "failed":
            failed.append({"video_id": vid_id, **meta})
    return failed


def get_history() -> list[dict]:
    """Return full history sorted by date (newest first)."""
    history = _load_history()
    items = [{"video_id": vid_id, **meta} for vid_id, meta in history.items()]
    items.sort(key=lambda x: x.get("date", ""), reverse=True)
    return items


def _get_model_name() -> str:
    return {
        "gemini": getattr(config, "GEMINI_MODEL", ""),
        "openai": getattr(config, "OPENAI_MODEL", ""),
        "anthropic": getattr(config, "ANTHROPIC_MODEL", ""),
        "openrouter": getattr(config, "OPENROUTER_MODEL", ""),
    }.get(config.LLM_PROVIDER, "")


def save_summary_to_file(
    video_id: str, title: str, channel: str, summaries: dict[str, str],
    platform: str = "youtube",
) -> str:
    """Save summaries as a local markdown file. Returns the file path."""
    os.makedirs(SUMMARIES_DIR, exist_ok=True)

    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    # Sanitize title for filename
    safe_title = "".join(c if c.isalnum() or c in " -_" else "" for c in title)[:50].strip()
    platform_tag = f"[{platform}]_" if platform != "youtube" else ""
    filename = f"{date_str}_{platform_tag}{channel}_{safe_title}.md"
    filepath = os.path.join(SUMMARIES_DIR, filename)

    if platform == "bilibili":
        bvid = video_id.replace("bilibili:", "")
        video_url = f"https://www.bilibili.com/video/{bvid}"
    else:
        video_url = f"https://www.youtube.com/watch?v={video_id}"

    lines = [
        f"# {title}",
        f"",
        f"- **Platform:** {platform.title()}",
        f"- **Channel:** {channel}",
        f"- **Link:** {video_url}",
        f"- **Date:** {date_str}",
        f"- **Model:** {config.LLM_PROVIDER} / {_get_model_name()}",
        f"",
    ]

    for lang, summary in summaries.items():
        if len(summaries) > 1:
            lines.append(f"---")
            lines.append(f"## {lang}")
            lines.append(f"")
        lines.append(summary)
        lines.append(f"")

    with open(filepath, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    logger.info("Saved summary to %s", filepath)
    return filepath
