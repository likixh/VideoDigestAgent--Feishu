"""Weekly Digest / Newsletter Generator.

Like a scheduled report job (cron + templating) that aggregates
all summaries from a time period into a single executive briefing.

Think of it as the "newsletter microservice" — it reads from the
data store, aggregates, and produces a formatted output.

Features:
    - Aggregates all summaries from the past N days
    - Groups by content type and channel
    - Generates an executive overview using LLM
    - Produces both email and markdown output
    - Includes cross-channel trend analysis

Usage:
    python main.py --digest              # Generate and email weekly digest
    python main.py --digest --days 3     # Last 3 days instead of 7
    python main.py --digest --dry-run    # Print without emailing
"""

import json
import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Optional

import config
from summarizer import _llm_call
from history import get_history, SUMMARIES_DIR

logger = logging.getLogger(__name__)


def generate_digest(
    days: int = 7,
    include_cross_analysis: bool = True,
) -> tuple[str, dict]:
    """Generate a digest of all summaries from the past N days.

    Args:
        days: Number of days to look back.
        include_cross_analysis: Whether to include cross-channel analysis.

    Returns:
        (digest_markdown, metadata_dict)
    """
    logger.info("Generating digest for the past %d days...", days)

    # ── Gather recent summaries ───────────────────────────────────────────
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    cutoff_str = cutoff.strftime("%Y-%m-%d")

    history = get_history()
    recent = [
        item for item in history
        if item.get("status") == "sent"
        and item.get("date", "") >= cutoff_str
    ]

    if not recent:
        logger.info("No videos processed in the past %d days.", days)
        return "No videos were processed in this period.", {"count": 0}

    logger.info("Found %d videos in the past %d days", len(recent), days)

    # ── Load full summaries from saved files ──────────────────────────────
    summaries_by_channel: dict[str, list[dict]] = {}
    summaries_by_type: dict[str, list[dict]] = {}

    for item in recent:
        channel = item.get("channel", "unknown")
        title = item.get("title", "Unknown")
        video_id = item.get("video_id", "")

        # Try to load the saved summary file
        summary_text = _load_saved_summary(video_id, title, channel)

        entry = {
            "title": title,
            "channel": channel,
            "video_id": video_id,
            "date": item.get("date", ""),
            "summary": summary_text,
        }

        summaries_by_channel.setdefault(channel, []).append(entry)

    # ── Generate the digest ───────────────────────────────────────────────
    digest_parts = []

    # Header
    end_date = datetime.now(timezone.utc).strftime("%B %d, %Y")
    start_date = cutoff.strftime("%B %d, %Y")
    digest_parts.append(f"# Weekly Video Digest")
    digest_parts.append(f"**{start_date} — {end_date}**\n")
    digest_parts.append(f"*{len(recent)} videos summarized across {len(summaries_by_channel)} channels*\n")

    # Executive overview (LLM-generated)
    overview = _generate_executive_overview(recent, summaries_by_channel)
    digest_parts.append("## Executive Overview\n")
    digest_parts.append(overview)
    digest_parts.append("")

    # Per-channel breakdown
    digest_parts.append("## Channel Summaries\n")
    for channel, entries in summaries_by_channel.items():
        digest_parts.append(f"### @{channel} ({len(entries)} video{'s' if len(entries) != 1 else ''})\n")
        for entry in entries:
            video_url = f"https://www.youtube.com/watch?v={entry['video_id']}"
            digest_parts.append(f"#### [{entry['title']}]({video_url})")
            digest_parts.append(f"*{entry['date']}*\n")
            if entry["summary"]:
                # Include a condensed version
                condensed = _condense_summary(entry["summary"])
                digest_parts.append(condensed)
            digest_parts.append("")

    # Cross-channel analysis (if RAG is available)
    if include_cross_analysis:
        try:
            from cross_analyzer import CrossVideoAnalyzer
            analyzer = CrossVideoAnalyzer()
            cross_report = analyzer.generate_cross_analysis_report()
            if cross_report and "No data" not in cross_report:
                digest_parts.append("## Cross-Channel Analysis\n")
                digest_parts.append(cross_report)
        except Exception as e:
            logger.warning("Cross-analysis skipped: %s", e)

    digest_markdown = "\n".join(digest_parts)

    metadata = {
        "count": len(recent),
        "channels": list(summaries_by_channel.keys()),
        "period_start": cutoff_str,
        "period_end": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    # Save digest to file
    _save_digest(digest_markdown, metadata)

    return digest_markdown, metadata


def send_digest_email(digest_markdown: str, metadata: dict) -> None:
    """Send the digest as an email.

    Reuses the emailer infrastructure but with a different template.
    """
    from emailer import _markdown_to_html, _sanitize

    import smtplib
    from email.message import EmailMessage

    digest_markdown = _sanitize(digest_markdown)
    period = f"{metadata.get('period_start', '?')} to {metadata.get('period_end', '?')}"
    count = metadata.get("count", 0)
    channels = metadata.get("channels", [])

    msg = EmailMessage()
    msg["Subject"] = f"Weekly Video Digest — {count} videos ({period})"
    msg["From"] = config.SENDER_EMAIL
    msg["To"] = ", ".join(config.RECIPIENT_EMAILS)

    # Plain text
    msg.set_content(digest_markdown, charset="utf-8")

    # HTML
    html_body = f"""\
<html>
<body style="font-family: Arial, sans-serif; max-width: 800px; margin: 0 auto; padding: 20px;">
  <div style="background: linear-gradient(135deg, #1a1a2e, #16213e); color: white; padding: 25px; border-radius: 8px 8px 0 0;">
    <h1 style="margin: 0; font-size: 22px;">Weekly Video Digest</h1>
    <p style="margin: 8px 0 0 0; color: #ccc;">{period}</p>
    <p style="margin: 4px 0 0 0; color: #e94560; font-weight: bold;">
      {count} videos | {len(channels)} channels
    </p>
  </div>
  <div style="border: 1px solid #ddd; border-top: none; padding: 20px; border-radius: 0 0 8px 8px;">
    {_markdown_to_html(digest_markdown)}
    <hr style="border: 1px solid #eee;">
    <p style="color: #888; font-size: 12px;">
      Generated by YouTube Video Summarizer | LLM: {config.LLM_PROVIDER}
    </p>
  </div>
</body>
</html>"""

    msg.add_alternative(html_body, subtype="html", charset="utf-8")

    logger.info("Sending digest email to %s", ", ".join(config.RECIPIENT_EMAILS))

    with smtplib.SMTP(config.SMTP_SERVER, config.SMTP_PORT) as server:
        server.starttls()
        server.login(config.SENDER_EMAIL, config.SENDER_PASSWORD)
        server.send_message(msg)

    logger.info("Digest email sent successfully")


# ── Internal Helpers ──────────────────────────────────────────────────────────


def _load_saved_summary(video_id: str, title: str, channel: str) -> str:
    """Try to load a previously saved summary file."""
    if not os.path.exists(SUMMARIES_DIR):
        return ""

    # Search for matching files
    safe_title_prefix = "".join(
        c if c.isalnum() or c in " -_" else "" for c in title
    )[:30].strip()

    for filename in os.listdir(SUMMARIES_DIR):
        if channel in filename and (safe_title_prefix in filename or video_id in filename):
            filepath = os.path.join(SUMMARIES_DIR, filename)
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    return f.read()
            except Exception:
                continue

    return ""


def _generate_executive_overview(
    recent: list[dict],
    by_channel: dict[str, list[dict]],
) -> str:
    """Use LLM to generate an executive overview of all recent content."""
    overview_prompt = (
        "You are an executive assistant creating a weekly briefing. "
        "Synthesize the following video summaries into a concise executive "
        "overview (3-5 paragraphs). Highlight:\n"
        "- The most important themes across all channels\n"
        "- Any actionable insights or calls to action\n"
        "- Notable disagreements between channels\n"
        "- Key data points and numbers worth remembering\n\n"
        "Write in a professional, concise tone."
    )

    context_parts = []
    for channel, entries in by_channel.items():
        context_parts.append(f"\n=== @{channel} ===")
        for entry in entries:
            context_parts.append(
                f"Title: {entry['title']} ({entry['date']})\n"
                f"{entry['summary'][:500] if entry['summary'] else '(summary not available)'}\n"
            )

    user_msg = "\n".join(context_parts)

    try:
        return _llm_call(overview_prompt, user_msg)
    except Exception as e:
        logger.error("Executive overview generation failed: %s", e)
        return (
            f"*{len(recent)} videos were processed across "
            f"{len(by_channel)} channels this week.*"
        )


def _condense_summary(summary: str, max_lines: int = 15) -> str:
    """Condense a full summary to key points for the digest."""
    lines = summary.strip().splitlines()

    # Keep headers and first few bullet points
    condensed = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#"):
            condensed.append(line)
        elif stripped.startswith("- ") or stripped.startswith("* "):
            condensed.append(line)
        elif "TL;DR" in stripped or "tl;dr" in stripped.lower():
            # Include everything from TL;DR onwards
            idx = lines.index(line)
            condensed.extend(lines[idx:idx + 6])
            break

        if len(condensed) >= max_lines:
            condensed.append("*(see full summary for more details)*")
            break

    return "\n".join(condensed) if condensed else summary[:500] + "..."


def _save_digest(digest_markdown: str, metadata: dict) -> str:
    """Save digest to a file."""
    os.makedirs(SUMMARIES_DIR, exist_ok=True)

    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    filename = f"{date_str}_weekly_digest.md"
    filepath = os.path.join(SUMMARIES_DIR, filename)

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(digest_markdown)

    logger.info("Digest saved to %s", filepath)
    return filepath
