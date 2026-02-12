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
from youtube_monitor import get_new_videos, mark_processed
from transcript_extractor import get_transcript
from summarizer import summarize
from emailer import send_summary_email

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

    channels = ", ".join(f"@{ch}" for ch in config.YOUTUBE_CHANNELS)
    languages = ", ".join(config.SUMMARY_LANGUAGES)
    recipients = ", ".join(config.RECIPIENT_EMAILS)
    verify = "on" if config.VERIFY_SUMMARY else "off"

    logger.info("=" * 60)
    logger.info("YouTube Video Summarizer")
    logger.info("-" * 60)
    logger.info("  LLM:        %s (%s)", provider, model_name)
    logger.info("  Channels:   %s", channels)
    logger.info("  Languages:  %s", languages)
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

    logger.info("Processing: %s (%s) from @%s", title, vid_id, channel)

    try:
        transcript = get_transcript(vid_id)
    except RuntimeError as e:
        logger.warning("Skipping %s — %s", vid_id, e)
        return

    summaries, content_type = summarize(title, transcript)

    if dry_run:
        logger.info("[DRY RUN] Skipping email — printing summary to stdout")
        for lang, summary in summaries.items():
            print(f"\n{'='*60}")
            print(f"  {lang}")
            print(f"{'='*60}\n")
            print(summary)
        return

    send_summary_email(
        title,
        vid_id,
        summaries,
        channel,
        content_type=content_type,
        published_at=published_at,
    )

    mark_processed(vid_id)
    logger.info("Done processing: %s", title)


def run_once(dry_run: bool = False) -> int:
    """Check for new videos and process them. Returns count of videos processed."""
    new_videos = get_new_videos()
    if not new_videos:
        logger.info("No new videos found.")
        return 0

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


def main() -> None:
    parser = argparse.ArgumentParser(
        description="YouTube Video Summarizer"
    )
    parser.add_argument(
        "--poll",
        action="store_true",
        help="Run continuously, checking every POLL_INTERVAL seconds",
    )
    parser.add_argument(
        "--video",
        type=str,
        help="Process a specific video by ID (for testing)",
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
