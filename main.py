#!/usr/bin/env python3
"""YouTube Stock Analysis Summarizer — main entry point.

Monitors YouTube channels for new videos, extracts transcripts,
generates structured stock analysis summaries using your chosen LLM,
and emails them to you.

Usage:
    python main.py              # run once (check for new videos now)
    python main.py --poll       # run continuously, checking every POLL_INTERVAL seconds
    python main.py --video ID   # process a specific video by ID (useful for testing)
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


def process_video(video: dict) -> None:
    """Process a single video: extract transcript, summarize, email, mark done."""
    vid_id = video["video_id"]
    title = video["title"]
    channel = video.get("channel", "unknown")

    logger.info("Processing: %s (%s) from @%s", title, vid_id, channel)

    try:
        transcript = get_transcript(vid_id)
    except RuntimeError as e:
        logger.warning("Skipping %s — %s", vid_id, e)
        return

    summaries = summarize(title, transcript)

    send_summary_email(title, vid_id, summaries, channel)

    mark_processed(vid_id)
    logger.info("Done processing: %s", title)


def run_once() -> int:
    """Check for new videos and process them. Returns count of videos processed."""
    new_videos = get_new_videos()
    if not new_videos:
        logger.info("No new videos found.")
        return 0

    for video in new_videos:
        process_video(video)

    return len(new_videos)


def run_poll() -> None:
    """Continuously poll for new videos."""
    logger.info(
        "Starting polling loop (interval: %d seconds / %d minutes)",
        config.POLL_INTERVAL,
        config.POLL_INTERVAL // 60,
    )
    logger.info("Monitoring channels: %s", ", ".join(
        f"@{ch}" for ch in config.YOUTUBE_CHANNELS
    ))
    logger.info("LLM provider: %s", config.LLM_PROVIDER)
    while True:
        try:
            run_once()
        except Exception:
            logger.exception("Error during polling cycle")
        logger.info("Sleeping %d seconds until next check...", config.POLL_INTERVAL)
        time.sleep(config.POLL_INTERVAL)


def run_single_video(video_id: str) -> None:
    """Process a specific video by ID (for testing)."""
    video = {
        "video_id": video_id,
        "title": f"Video {video_id}",
        "channel": "test",
    }
    process_video(video)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="YouTube Stock Analysis Summarizer"
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
    args = parser.parse_args()

    if args.video:
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
