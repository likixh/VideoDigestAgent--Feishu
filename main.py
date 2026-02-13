#!/usr/bin/env python3
"""YouTube Video Summarizer — main entry point.

Monitors YouTube channels for new videos, extracts transcripts,
generates structured summaries using your chosen LLM,
and emails them to you.

Supports three pipeline engines:
    default   — Sequential pipeline (summarizer.py)
    langgraph — LangGraph state machine with RAG + quality checks
    crewai    — CrewAI multi-agent crew (researcher → analyst → writer → fact-checker)

Usage:
    python main.py              # run once (check for new videos now)
    python main.py --poll       # run continuously, checking every POLL_INTERVAL seconds
    python main.py --video ID   # process a specific video by ID (useful for testing)
    python main.py --dry-run    # run once but skip sending email (print summary instead)
    python main.py --check      # validate config and exit
    python main.py --digest     # generate and email weekly digest
    python main.py --ask "..."  # ask a question about past videos (RAG-powered)
    python main.py --compare TOPIC  # cross-channel comparison on a topic
    python main.py --trends CH  # sentiment trend for a channel
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


# ── Pipeline Router ────────────────────────────────────────────────────────────

def _get_summarizer():
    """Return the summarize function for the configured pipeline engine.

    This is the router — like an API gateway dispatching to the right
    backend microservice based on config.
    """
    engine = config.PIPELINE_ENGINE

    if engine == "langgraph":
        from langgraph_pipeline import langgraph_summarize
        logger.info("Using LangGraph pipeline engine")
        return langgraph_summarize

    elif engine == "crewai":
        from crew_summarizer import crew_summarize
        logger.info("Using CrewAI pipeline engine")
        return crew_summarize

    else:
        logger.info("Using default pipeline engine")
        return summarize


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
    engine = config.PIPELINE_ENGINE
    rag = "on" if config.RAG_ENABLED else "off"

    logger.info("=" * 60)
    logger.info("YouTube Video Summarizer")
    logger.info("-" * 60)
    logger.info("  LLM:        %s (%s)", provider, model_name)
    logger.info("  Engine:     %s", engine)
    logger.info("  RAG:        %s", rag)
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
        mark_failed(vid_id, title, channel, str(e))
        return False

    # Route to the configured pipeline engine
    summarize_fn = _get_summarizer()

    try:
        # LangGraph and CrewAI accept extra kwargs; default summarize() ignores them
        if config.PIPELINE_ENGINE in ("langgraph", "crewai"):
            summaries, content_type = summarize_fn(
                title, transcript,
                video_id=vid_id,
                channel=channel,
                published_at=published_at,
            )
        else:
            summaries, content_type = summarize_fn(title, transcript)
    except Exception as e:
        logger.error("Summarization failed for %s: %s", vid_id, e)
        mark_failed(vid_id, title, channel, str(e))
        return False

    # Index into RAG store (if enabled)
    if config.RAG_ENABLED:
        try:
            from rag_store import get_store
            store = get_store()
            store.index_video(
                video_id=vid_id,
                title=title,
                channel=channel,
                content_type=content_type,
                transcript=transcript,
                summaries=summaries,
                published_at=published_at,
            )
        except Exception as e:
            logger.warning("RAG indexing failed (non-fatal): %s", e)

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


def run_digest(days: int = 7, dry_run: bool = False) -> None:
    """Generate and send weekly digest."""
    from digest import generate_digest, send_digest_email

    digest_md, metadata = generate_digest(days=days)

    if dry_run or metadata.get("count", 0) == 0:
        print(digest_md)
        return

    try:
        send_digest_email(digest_md, metadata)
    except Exception as e:
        logger.error("Digest email failed: %s", e)
        print(digest_md)


def run_ask(question: str) -> None:
    """Ask a question about past videos (RAG-powered)."""
    if not config.RAG_ENABLED:
        print("RAG is disabled. Set RAG_ENABLED=true in your .env file.")
        print("Then process some videos so they get indexed.")
        return

    from cross_analyzer import CrossVideoAnalyzer
    analyzer = CrossVideoAnalyzer()
    answer = analyzer.ask(question)
    print(f"\n{answer}\n")


def run_compare(topic: str) -> None:
    """Cross-channel comparison on a topic."""
    if not config.RAG_ENABLED:
        print("RAG is disabled. Set RAG_ENABLED=true in your .env file.")
        return

    from cross_analyzer import CrossVideoAnalyzer
    analyzer = CrossVideoAnalyzer()
    report = analyzer.compare_channels_on_topic(topic)
    print(f"\n{report}\n")


def run_trends(channel: str) -> None:
    """Show sentiment trends for a channel."""
    if not config.RAG_ENABLED:
        print("RAG is disabled. Set RAG_ENABLED=true in your .env file.")
        return

    from cross_analyzer import CrossVideoAnalyzer
    analyzer = CrossVideoAnalyzer()
    report = analyzer.get_sentiment_trends(channel)
    print(f"\n{report}\n")


def run_rag_stats() -> None:
    """Show RAG store statistics."""
    if not config.RAG_ENABLED:
        print("RAG is disabled. Set RAG_ENABLED=true in your .env file.")
        return

    from rag_store import get_store
    store = get_store()
    stats = store.get_stats()
    print(f"\n  RAG Store Statistics:")
    print(f"  {'─'*40}")
    print(f"  Transcript chunks: {stats['transcript_chunks']}")
    print(f"  Summaries:         {stats['summaries']}")
    print(f"  Storage:           {stats['persist_dir']}")
    print()


# ── CLI ─────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="YouTube Video Summarizer",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Pipeline Engines (set PIPELINE_ENGINE env var):
  default     Sequential pipeline — classify → summarize → verify
  langgraph   LangGraph state machine — adds RAG context, quality checks, retry
  crewai      CrewAI multi-agent — researcher → analyst → writer → fact-checker

RAG Features (set RAG_ENABLED=true):
  --ask       Ask questions about past videos
  --compare   Compare what channels say about a topic
  --trends    Track a channel's sentiment over time
  --rag-stats Show RAG index statistics
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

    # New commands
    parser.add_argument(
        "--digest", action="store_true",
        help="Generate and email weekly digest of all recent summaries",
    )
    parser.add_argument(
        "--days", type=int, default=7,
        help="Number of days to include in digest (default: 7)",
    )
    parser.add_argument(
        "--ask", type=str,
        help="Ask a question about past videos (requires RAG_ENABLED=true)",
    )
    parser.add_argument(
        "--compare", type=str,
        help="Cross-channel comparison on a topic (requires RAG_ENABLED=true)",
    )
    parser.add_argument(
        "--trends", type=str,
        help="Show sentiment trends for a channel (requires RAG_ENABLED=true)",
    )
    parser.add_argument(
        "--rag-stats", action="store_true",
        help="Show RAG store statistics",
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

    if args.digest:
        _print_banner()
        run_digest(days=args.days, dry_run=args.dry_run)
        sys.exit(0)

    if args.ask:
        run_ask(args.ask)
        sys.exit(0)

    if args.compare:
        run_compare(args.compare)
        sys.exit(0)

    if args.trends:
        run_trends(args.trends)
        sys.exit(0)

    if args.rag_stats:
        run_rag_stats()
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
