#!/usr/bin/env python3
"""YouTube Video Summarizer — main entry point.

Monitors YouTube channels for new videos, extracts transcripts,
generates structured summaries using your chosen LLM,
and emails them to you.

Usage:
    python main.py              # run once (check for new videos now)
    python main.py --poll       # run continuously, checking every POLL_INTERVAL seconds
    python main.py --video ID   # process a specific video by ID
    python main.py --history    # show processing history
    python main.py --retry      # retry all previously failed videos
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


# ── Dashboard ───────────────────────────────────────────────────────────────

def print_dashboard() -> None:
    """Print a nice startup summary."""
    processed = get_processed_ids()
    verify = "ON" if config.VERIFY_SUMMARY else "OFF"

    print()
    print("  YouTube Video Summarizer")
    print("  " + "─" * 44)
    print(f"  Channels:      {', '.join(f'@{ch}' for ch in config.YOUTUBE_CHANNELS)}")
    print(f"  LLM:           {config.LLM_PROVIDER}")
    print(f"  Languages:     {', '.join(config.SUMMARY_LANGUAGES)}")
    print(f"  Verification:  {verify}")
    print(f"  Poll every:    {config.POLL_INTERVAL // 60} min")
    print(f"  Videos seen:   {len(processed)}")
    print("  " + "─" * 44)
    print()


# ── Core processing ─────────────────────────────────────────────────────────

def process_video(video: dict) -> bool:
    """Process a single video. Returns True if successful."""
    vid_id = video["video_id"]
    title = video["title"]
    channel = video.get("channel", "unknown")

    logger.info("Processing: %s (%s) from @%s", title, vid_id, channel)

    try:
        transcript = get_transcript(vid_id)
    except RuntimeError as e:
        logger.warning("Skipping %s — %s", vid_id, e)
        mark_failed(vid_id, title, channel, str(e))
        return False

    try:
        summaries = summarize(title, transcript)
    except Exception as e:
        logger.error("Summarization failed for %s: %s", vid_id, e)
        mark_failed(vid_id, title, channel, str(e))
        return False

    try:
        send_summary_email(title, vid_id, summaries, channel)
    except Exception as e:
        logger.error("Email failed for %s: %s", vid_id, e)
        mark_failed(vid_id, title, channel, f"email: {e}")
        return False

    save_summary_to_file(vid_id, title, channel, summaries)
    mark_sent(vid_id, title, channel)
    logger.info("Done: %s", title)
    return True


# ── Commands ────────────────────────────────────────────────────────────────

def run_once() -> int:
    """Check for new videos and process them. Returns count processed."""
    new_videos = get_new_videos()
    if not new_videos:
        logger.info("No new videos found.")
        return 0

    count = 0
    for video in new_videos:
        if process_video(video):
            count += 1
    return count


def run_poll() -> None:
    """Continuously poll for new videos."""
    print_dashboard()
    logger.info("Watching for new videos...")
    while True:
        try:
            run_once()
        except Exception:
            logger.exception("Error during polling cycle")
        logger.info("Sleeping %d seconds until next check...", config.POLL_INTERVAL)
        time.sleep(config.POLL_INTERVAL)


def run_single_video(video_id: str) -> None:
    """Process a specific video by ID."""
    video = {
        "video_id": video_id,
        "title": f"Video {video_id}",
        "channel": "manual",
    }
    process_video(video)


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
    args = parser.parse_args()

    if args.history:
        run_history()
    elif args.retry:
        run_retry()
    elif args.video:
        run_single_video(args.video)
    elif args.poll:
        run_poll()
    else:
        count = run_once()
        if count == 0:
            logger.info("Nothing to do. Run with --poll for continuous monitoring.")
        sys.exit(0)


if __name__ == "__main__":
    main()
