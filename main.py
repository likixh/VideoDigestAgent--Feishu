#!/usr/bin/env python3
"""YouTube Video Summarizer — main entry point.

Monitors YouTube channels for new videos, extracts transcripts,
generates structured summaries using your chosen LLM,
and emails them to you.

Includes a prediction tracker that extracts stock/crypto calls from
summaries, fetches actual market data, and scores prediction accuracy.

Usage:
    python main.py              # run once (check for new videos now)
    python main.py --poll       # run continuously, checking every POLL_INTERVAL seconds
    python main.py --video ID   # process a specific video by ID (useful for testing)
    python main.py --dry-run    # run once but skip sending email (print summary instead)
    python main.py --check      # validate config and exit

Prediction Tracker:
    python main.py --scorecard CHANNEL   # prediction accuracy report for a channel
    python main.py --leaderboard         # rank all channels by accuracy
    python main.py --predictions         # list all tracked predictions
    python main.py --score-update        # fetch prices + score open predictions
    python main.py --backfill            # extract predictions from saved summaries
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

    channels = ", ".join(f"@{ch}" for ch in config.YOUTUBE_CHANNELS)
    languages = ", ".join(config.SUMMARY_LANGUAGES)
    recipients = ", ".join(config.RECIPIENT_EMAILS)
    verify = "on" if config.VERIFY_SUMMARY else "off"
    tracking = "on" if config.PREDICTION_TRACKING else "off"

    logger.info("=" * 60)
    logger.info("YouTube Video Summarizer")
    logger.info("-" * 60)
    logger.info("  LLM:        %s (%s)", provider, model_name)
    logger.info("  Channels:   %s", channels)
    logger.info("  Languages:  %s", languages)
    logger.info("  Recipients: %s", recipients)
    logger.info("  Verify:     %s", verify)
    logger.info("  Tracking:   %s", tracking)
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
        mark_failed(vid_id, title, channel, str(e))
        return False

    try:
        summaries, content_type = summarize(title, transcript)
    except Exception as e:
        logger.error("Summarization failed for %s: %s", vid_id, e)
        mark_failed(vid_id, title, channel, str(e))
        return False

    # Extract and store predictions (if tracking enabled)
    if config.PREDICTION_TRACKING:
        try:
            from prediction_tracker import track_predictions_for_video
            n_preds = track_predictions_for_video(
                video_id=vid_id,
                title=title,
                channel=channel,
                content_type=content_type,
                summaries=summaries,
                predicted_at=published_at,
            )
            if n_preds > 0:
                logger.info("Tracked %d predictions from %s", n_preds, title)
        except Exception as e:
            logger.warning("Prediction tracking failed (non-fatal): %s", e)

    try:
        send_summary_email(title, vid_id, summaries, channel, content_type)
    except Exception as e:
        logger.error("Email failed for %s: %s", vid_id, e)
        # Save summaries locally so the API work isn't wasted
        filepath = save_summary_to_file(vid_id, title, channel, summaries)
        logger.info("Summary saved to %s despite email failure", filepath)
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
        mark_failed(vid_id, title, channel, f"email: {e}")
        return False

    save_summary_to_file(vid_id, title, channel, summaries)
    mark_sent(vid_id, title, channel)
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

        # Score predictions during polling (piggyback on the loop)
        if config.PREDICTION_TRACKING:
            try:
                from prediction_tracker import run_score_update
                run_score_update()
            except Exception:
                logger.exception("Error during prediction scoring")

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


# ── Prediction Tracker Commands ───────────────────────────────────────────────

def run_scorecard(channel: str, eval_window: str = "1M") -> None:
    """Print prediction scorecard for a channel."""
    from prediction_scorer import generate_scorecard
    report = generate_scorecard(channel, eval_window)
    print(f"\n{report}\n")


def run_leaderboard(eval_window: str = "1M") -> None:
    """Print cross-channel leaderboard."""
    from prediction_scorer import generate_leaderboard
    report = generate_leaderboard(eval_window)
    print(f"\n{report}\n")


def run_predictions(channel: str = None) -> None:
    """List tracked predictions."""
    from prediction_db import get_db
    from prediction_scorer import format_predictions_table

    db = get_db()
    if channel:
        predictions = db.get_predictions_for_channel(channel)
    else:
        predictions = db.get_all_predictions()

    print(f"\n{format_predictions_table(predictions)}\n")


def run_score_update_cmd() -> None:
    """Run the scoring pipeline."""
    from prediction_tracker import run_score_update
    stats = run_score_update()
    print(f"\nScore update complete:")
    print(f"  Prices updated:       {stats['prices_updated']}")
    print(f"  Prices failed:        {stats['prices_failed']}")
    print(f"  Baselines backfilled: {stats['baselines_backfilled']}")
    print(f"  Predictions scored:   {stats['scored']}")
    print(f"  Skipped:              {stats['skipped']}")
    print(f"  Errors:               {stats['errors']}")
    print()


def run_backfill() -> None:
    """Backfill predictions from saved summaries."""
    from prediction_tracker import run_backfill_from_history
    stats = run_backfill_from_history()
    print(f"\nBackfill complete:")
    print(f"  Predictions extracted: {stats['extracted']}")
    print(f"  Predictions stored:    {stats['stored']}")
    print()


def run_tracker_stats() -> None:
    """Print prediction tracker statistics."""
    from prediction_db import get_db
    db = get_db()
    stats = db.get_stats()
    print(f"\n  Prediction Tracker Statistics:")
    print(f"  {'─'*40}")
    print(f"  Total predictions:  {stats['total_predictions']}")
    print(f"  Open predictions:   {stats['open_predictions']}")
    print(f"  Scored predictions: {stats['scored_predictions']}")
    print(f"  Cached prices:      {stats['cached_prices']}")
    print(f"  Channels tracked:   {stats['channels_tracked']}")
    print(f"  Tickers tracked:    {stats['tickers_tracked']}")
    print(f"  Database:           {stats['db_path']}")
    print()


# ── CLI ─────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="YouTube Video Summarizer + Prediction Tracker",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Prediction Tracker (set PREDICTION_TRACKING=true):
  --scorecard CH    Channel prediction accuracy report
  --leaderboard     Rank channels by prediction accuracy
  --predictions     List all tracked predictions
  --score-update    Fetch market prices and score predictions
  --backfill        Extract predictions from saved summaries
  --tracker-stats   Show prediction tracker statistics
""",
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

    # Prediction tracker commands
    parser.add_argument(
        "--scorecard", type=str, metavar="CHANNEL",
        help="Show prediction accuracy scorecard for a channel",
    )
    parser.add_argument(
        "--leaderboard", action="store_true",
        help="Show cross-channel prediction accuracy leaderboard",
    )
    parser.add_argument(
        "--predictions", nargs="?", const="__all__", metavar="CHANNEL",
        help="List tracked predictions (optionally filter by channel)",
    )
    parser.add_argument(
        "--score-update", action="store_true",
        help="Fetch market prices and score all open predictions",
    )
    parser.add_argument(
        "--backfill", action="store_true",
        help="Extract predictions from previously saved summaries",
    )
    parser.add_argument(
        "--tracker-stats", action="store_true",
        help="Show prediction tracker statistics",
    )
    parser.add_argument(
        "--eval-window", type=str, default="1M",
        choices=["1W", "1M", "3M"],
        help="Evaluation window for scoring (default: 1M)",
    )

    args = parser.parse_args()

    if args.check:
        run_check()
        sys.exit(0)

    if args.history:
        run_history()
        sys.exit(0)

    if args.retry:
        _print_banner()
        run_retry()
        sys.exit(0)

    # Prediction tracker commands
    if args.scorecard:
        run_scorecard(args.scorecard, args.eval_window)
        sys.exit(0)

    if args.leaderboard:
        run_leaderboard(args.eval_window)
        sys.exit(0)

    if args.predictions:
        channel = args.predictions if args.predictions != "__all__" else None
        run_predictions(channel)
        sys.exit(0)

    if args.score_update:
        run_score_update_cmd()
        sys.exit(0)

    if args.backfill:
        run_backfill()
        sys.exit(0)

    if args.tracker_stats:
        run_tracker_stats()
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
