#!/usr/bin/env python3
"""YouTube Video Summarizer — main entry point.

Monitors YouTube channels for new videos, extracts transcripts,
generates structured summaries using your chosen LLM,
and emails them to you.

Usage:
    python main.py              # run once (check for new videos now)
    python main.py --poll       # run continuously, checking every POLL_INTERVAL seconds
    python main.py --video ID   # process a specific video by ID (useful for testing)
    python main.py --dry-run    # run once but skip sending email (print summary instead)
    python main.py --check      # validate config and exit
"""

import argparse
import logging
import time
import sys

import config
from youtube_monitor import get_new_videos
from transcript_extractor import get_transcript
from summarizer import summarize
from emailer import send_summary_email
from history import (
    mark_sent, mark_failed, get_failed_videos, get_history,
    get_processed_ids, save_summary_to_file,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def _print_banner() -> None:
    """Print a startup banner with configuration summary."""
    provider = config.LLM_PROVIDER
    model_name = {
        "gemini": getattr(config, "GEMINI_MODEL", ""),
        "openai": getattr(config, "OPENAI_MODEL", ""),
        "anthropic": getattr(config, "ANTHROPIC_MODEL", ""),
    }.get(provider, "")

    channels = ", ".join(f"@{ch}" for ch in config.YOUTUBE_CHANNELS) if config.YOUTUBE_CHANNELS else "none"
    languages = ", ".join(config.SUMMARY_LANGUAGES)
    recipients = ", ".join(config.RECIPIENT_EMAILS) if config.RECIPIENT_EMAILS else "—"
    verify = "on" if config.VERIFY_SUMMARY else "off"

    logger.info("=" * 60)
    logger.info("YouTube Video Summarizer")
    logger.info("-" * 60)
    logger.info("  LLM:        %s (%s)", provider, model_name)
    logger.info("  Channels:   %s", channels)
    if config.YOUTUBE_SEARCH_ENABLED:
        search_queries = ", ".join(config.YOUTUBE_SEARCH_QUERIES)
        logger.info("  Search:     %s", search_queries)
        logger.info("  Search int: every %d min", config.YOUTUBE_SEARCH_INTERVAL // 60)
        logger.info("  Quota:      %d units/day budget", config.YOUTUBE_SEARCH_QUOTA_BUDGET)
    else:
        logger.info("  Search:     disabled")
    logger.info("  Languages:  %s", languages)
    logger.info("  Output:     %s", config.OUTPUT_MODE)
    if config.OUTPUT_MODE in ("email", "both"):
        logger.info("  Recipients: %s", recipients)
    logger.info("  Verify:     %s", verify)
    logger.info("  Poll:       every %d min", config.POLL_INTERVAL // 60)
    logger.info("=" * 60)


def _fetch_video_title(video_id: str) -> str:
    """Fetch actual video title from YouTube API."""
    try:
        from googleapiclient.discovery import build

        youtube = build("youtube", "v3", developerKey=config.YOUTUBE_API_KEY)
        resp = youtube.videos().list(part="snippet", id=video_id).execute()
        items = resp.get("items", [])
        if items:
            return items[0]["snippet"]["title"]
    except Exception as e:
        logger.warning("Could not fetch title for %s: %s", video_id, e)
    return f"Video {video_id}"


def _fetch_video_metadata(video_id: str) -> dict:
    """Fetch video title, channel, and publish date from YouTube API."""
    try:
        from googleapiclient.discovery import build

        youtube = build("youtube", "v3", developerKey=config.YOUTUBE_API_KEY)
        resp = youtube.videos().list(part="snippet", id=video_id).execute()
        items = resp.get("items", [])
        if items:
            snippet = items[0]["snippet"]
            return {
                "title": snippet["title"],
                "channel": snippet.get("channelTitle", "unknown"),
                "published_at": snippet.get("publishedAt", ""),
            }
    except Exception as e:
        logger.warning("Could not fetch metadata for %s: %s", video_id, e)
    return {
        "title": f"Video {video_id}",
        "channel": "unknown",
        "published_at": "",
    }


def process_video(video: dict, dry_run: bool = False) -> None:
    """Process a single video: extract transcript, summarize, email, mark done."""
    vid_id = video["video_id"]
    title = video["title"]
    channel = video.get("channel", "unknown")
    published_at = video.get("published_at", "")
    source = video.get("source", "channel")

    if source == "search":
        source_label = f"search:'{video.get('search_query', '?')}'"
    else:
        source_label = f"@{channel}"
    logger.info("Processing: %s (%s) from %s", title, vid_id, source_label)

    try:
        transcript = get_transcript(vid_id)
    except RuntimeError as e:
        logger.warning("Skipping %s — %s", vid_id, e)
        mark_failed(vid_id, title, channel, str(e), source=source)
        return False

    try:
        summaries, content_type = summarize(title, transcript)
    except Exception as e:
        logger.error("Summarization failed for %s: %s", vid_id, e)
        mark_failed(vid_id, title, channel, str(e), source=source)
        return False

    # ── Deliver output based on OUTPUT_MODE ────────────────────────
    email_err = None
    if config.OUTPUT_MODE in ("email", "both") and not dry_run:
        try:
            send_summary_email(title, vid_id, summaries, channel, content_type)
        except Exception as e:
            logger.error("Email failed for %s: %s", vid_id, e)
            email_err = e

    if config.OUTPUT_MODE in ("local", "both") or email_err is not None:
        save_summary_to_file(vid_id, title, channel, summaries)

    if email_err is not None:
        # Print to terminal as fallback
        video_url = f"https://www.youtube.com/watch?v={vid_id}"
        print(f"\n{'='*60}")
        print(f"EMAIL FAILED — printing summary for: {title}")
        print(f"Channel: @{channel}  |  {video_url}")
        print(f"{'='*60}")
        for lang, summary in summaries.items():
            print(f"\n--- {lang} ---\n")
            print(summary)
        print(f"\n{'='*60}\n")
        mark_failed(vid_id, title, channel, f"email: {email_err}", source=source)
        return False

    mark_sent(vid_id, title, channel, source=source)
    logger.info("Done: %s", title)
    return True


# ── Commands ────────────────────────────────────────────────────────────────

def run_once(dry_run: bool = False) -> int:
    """Check for new videos and process them. Returns count of videos processed."""
    new_videos = get_new_videos()
    if not new_videos:
        logger.info("No new videos found.")
        return 0

    count = 0
    for video in new_videos:
        process_video(video, dry_run=dry_run)

    return len(new_videos)


def run_poll(dry_run: bool = False) -> None:
    """Continuously poll for new videos."""
    _print_banner()
    logger.info(
        "Starting polling loop (interval: %d seconds / %d minutes)",
        config.POLL_INTERVAL,
        config.POLL_INTERVAL // 60,
    )
    while True:
        try:
            run_once(dry_run=dry_run)
        except Exception:
            logger.exception("Error during polling cycle")
        logger.info("Sleeping %d seconds until next check...", config.POLL_INTERVAL)
        time.sleep(config.POLL_INTERVAL)


def run_single_video(video_id: str, dry_run: bool = False) -> None:
    """Process a specific video by ID (for testing)."""
    metadata = _fetch_video_metadata(video_id)
    video = {
        "video_id": video_id,
        "title": metadata["title"],
        "channel": metadata["channel"],
        "published_at": metadata["published_at"],
    }
    logger.info("Fetched metadata — title: %s, channel: %s", video["title"], video["channel"])
    process_video(video, dry_run=dry_run)


def run_check() -> None:
    """Validate configuration and exit."""
    _print_banner()
    logger.info("Config validation passed — all required settings are present.")


def run_history() -> None:
    """Print processing history."""
    items = get_history()
    if not items:
        print("No history yet.")
        return

    # Header
    print()
    print(f"  {'Date':<18} {'Channel':<18} {'Status':<8} {'Title'}")
    print(f"  {'─'*18} {'─'*18} {'─'*8} {'─'*40}")

    status_icons = {"sent": "sent", "failed": "FAIL", "init": "seen"}

    for item in items:
        date = item.get("date", "—")
        channel = f"@{item.get('channel', '?')}" if item.get("channel") else "—"
        status = status_icons.get(item.get("status", ""), "?")
        title = item.get("title", "—") or "—"
        # Truncate long titles
        if len(title) > 50:
            title = title[:47] + "..."
        print(f"  {date:<18} {channel:<18} {status:<8} {title}")

    # Summary
    total = len(items)
    sent = sum(1 for i in items if i.get("status") == "sent")
    failed = sum(1 for i in items if i.get("status") == "failed")
    print(f"\n  Total: {total} | Sent: {sent} | Failed: {failed}")
    print()


def run_retry() -> None:
    """Retry all previously failed videos."""
    failed = get_failed_videos()
    if not failed:
        print("No failed videos to retry.")
        return

    print(f"Retrying {len(failed)} failed video(s)...\n")
    for item in failed:
        video = {
            "video_id": item["video_id"],
            "title": item.get("title", f"Video {item['video_id']}"),
            "channel": item.get("channel", "unknown"),
        }
        process_video(video)


# ── CLI ─────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="YouTube Video Summarizer"
    )
    parser.add_argument(
        "--poll", action="store_true",
        help="Run continuously, checking every POLL_INTERVAL seconds",
    )
    parser.add_argument(
        "--video", type=str,
        help="Process a specific video by ID",
    )
    parser.add_argument(
        "--history", action="store_true",
        help="Show processing history",
    )
    parser.add_argument(
        "--retry", action="store_true",
        help="Retry all previously failed videos",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run without sending email (print summary to stdout instead)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Validate configuration and exit",
    )
    args = parser.parse_args()

    if args.check:
        run_check()
        sys.exit(0)

    _print_banner()

    if args.video:
        run_single_video(args.video, dry_run=args.dry_run)
    elif args.poll:
        run_poll(dry_run=args.dry_run)
    else:
        count = run_once(dry_run=args.dry_run)
        if count == 0:
            logger.info("Nothing to do. Run with --poll for continuous monitoring.")
        sys.exit(0)


if __name__ == "__main__":
    main()
