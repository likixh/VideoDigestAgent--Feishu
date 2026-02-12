"""Send summary emails via SMTP."""

import logging
import smtplib
from email.header import Header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import config

logger = logging.getLogger(__name__)


def _markdown_to_html(md: str) -> str:
    """Convert markdown to email-safe HTML."""
    import re

    html = md

    # Escape HTML entities first
    html = html.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    # Headers
    html = re.sub(r"^### (.+)$", r"<h3>\1</h3>", html, flags=re.MULTILINE)
    html = re.sub(r"^## (.+)$", r"<h2>\1</h2>", html, flags=re.MULTILINE)
    html = re.sub(r"^# (.+)$", r"<h1>\1</h1>", html, flags=re.MULTILINE)

    # Bold and italic
    html = re.sub(r"\*\*\*(.+?)\*\*\*", r"<strong><em>\1</em></strong>", html)
    html = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", html)
    html = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<em>\1</em>", html)

    # Inline code
    html = re.sub(r"`([^`]+)`", r'<code style="background:#f0f0f5;padding:2px 5px;border-radius:3px;font-size:90%;">\1</code>', html)

    # Links — [text](url)
    html = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2" style="color:#e94560;">\1</a>', html)

    # Blockquotes
    html = re.sub(
        r"((?:^&gt; .+\n?)+)",
        lambda m: '<blockquote style="border-left:3px solid #e94560;padding-left:12px;color:#555;margin:10px 0;">'
        + re.sub(r"^&gt; ", "", m.group(0), flags=re.MULTILINE).strip()
        + "</blockquote>\n",
        html,
        flags=re.MULTILINE,
    )

    # Numbered lists — consecutive lines starting with digits
    def _wrap_ol(m: re.Match) -> str:
        items = re.sub(r"^\d+\.\s+(.+)$", r"<li>\1</li>", m.group(0), flags=re.MULTILINE)
        return f"<ol>{items}</ol>"

    html = re.sub(r"((?:^\d+\.\s+.+\n?)+)", _wrap_ol, html, flags=re.MULTILINE)

    # Unordered bullet points (- or *)
    html = re.sub(r"^[-*] (.+)$", r"<li>\1</li>", html, flags=re.MULTILINE)

    # Wrap consecutive <li> (not already inside ol) in <ul>
    html = re.sub(
        r"((?:<li>.*?</li>\n?)+)",
        r"<ul>\1</ul>",
        html,
    )

    # Horizontal rules
    html = re.sub(r"^---+$", r'<hr style="border:1px solid #eee;margin:15px 0;">', html, flags=re.MULTILINE)

    # Paragraphs — double newlines
    html = re.sub(r"\n\n", r"<br><br>", html)

    return html


def _content_type_label(content_type: str) -> str:
    """Human-readable label for a content type."""
    labels = {
        "stock_analysis": "Stock Analysis",
        "macro_economics": "Macro Economics",
        "crypto": "Crypto",
        "podcast_interview": "Podcast / Interview",
        "tech_review": "Tech Review",
        "educational": "Educational",
        "news": "News",
        "cooking": "Cooking",
        "fitness": "Fitness",
        "general": "General",
    }
    return labels.get(content_type, content_type.replace("_", " ").title())


def send_summary_email(
    video_title: str,
    video_id: str,
    summaries: dict[str, str],
    channel: str,
    content_type: str = "general",
    published_at: str = "",
) -> None:
    """Send an email with summaries in all configured languages."""
    video_url = f"https://www.youtube.com/watch?v={video_id}"
    thumbnail_url = f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg"
    badge_label = _content_type_label(content_type)
    recipients = config.RECIPIENT_EMAILS

    # Format publish date if available
    date_display = ""
    if published_at:
        try:
            from datetime import datetime
            dt = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
            date_display = dt.strftime("%b %d, %Y")
        except (ValueError, TypeError):
            date_display = published_at

    msg = MIMEMultipart("alternative")
    msg["Subject"] = Header(f"[@{channel}] New Video Summary: {video_title}", "utf-8")
    msg["From"] = config.SENDER_EMAIL
    msg["To"] = ", ".join(recipients)

    # Build plain text
    text_parts = [
        f"New video from @{channel}\n"
        f"Title: {video_title}\n"
        f"Type: {badge_label}\n"
        f"Link: {video_url}\n"
    ]
    if date_display:
        text_parts[0] += f"Published: {date_display}\n"
    for lang, summary in summaries.items():
        text_parts.append(f"\n{'='*60}\n{lang}\n{'='*60}\n\n{summary}")
    text_body = "\n".join(text_parts)

    # Build HTML
    summary_html_parts = []
    for lang, summary in summaries.items():
        summary_html_parts.append(
            f'<div style="background: #f0f0f5; padding: 8px 16px; margin: 20px 0 10px 0; '
            f'border-radius: 4px; font-weight: bold; font-size: 16px;">{lang}</div>\n'
            f'{_markdown_to_html(summary)}'
        )
    summaries_html = "\n<hr style='border: 2px solid #e94560; margin: 30px 0;'>\n".join(
        summary_html_parts
    )

    date_html = ""
    if date_display:
        date_html = f'<span style="color:#888;font-size:13px;margin-left:10px;">{date_display}</span>'

    html_body = f"""\
<html>
<body style="font-family: Arial, sans-serif; max-width: 800px; margin: 0 auto; padding: 20px;">
  <div style="background: #1a1a2e; color: white; padding: 20px; border-radius: 8px 8px 0 0;">
    <h1 style="margin: 0; font-size: 18px;">New Video Summary</h1>
    <p style="margin: 8px 0 0 0; color: #ccc;">from @{channel}</p>
  </div>
  <div style="border: 1px solid #ddd; border-top: none; padding: 20px; border-radius: 0 0 8px 8px;">
    <div style="text-align: center; margin-bottom: 15px;">
      <a href="{video_url}">
        <img src="{thumbnail_url}" alt="Video thumbnail"
             style="max-width: 100%; border-radius: 6px; border: 1px solid #eee;">
      </a>
    </div>
    <h2 style="margin-top: 0;">
      <a href="{video_url}" style="color: #e94560; text-decoration: none;">{video_title}</a>
    </h2>
    <div style="margin-bottom: 12px;">
      <span style="background: #e94560; color: white; padding: 3px 10px; border-radius: 12px;
                   font-size: 12px; font-weight: bold;">{badge_label}</span>
      {date_html}
    </div>
    <hr style="border: 1px solid #eee;">
    {summaries_html}
    <hr style="border: 1px solid #eee;">
    <p style="color: #888; font-size: 12px;">
      Generated by YouTube Video Summarizer | LLM: {config.LLM_PROVIDER}
    </p>
  </div>
</body>
</html>"""

    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    logger.info("Sending email to %s", ", ".join(recipients))

    with smtplib.SMTP(config.SMTP_SERVER, config.SMTP_PORT) as server:
        server.starttls()
        server.login(config.SENDER_EMAIL, config.SENDER_PASSWORD)
        server.send_message(msg)

    logger.info("Email sent successfully")
