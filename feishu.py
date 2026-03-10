"""Send summary notifications via Feishu (Lark) webhook."""

import logging
import time
import hmac
import hashlib
import base64
import json
import re
import urllib.request

import config

logger = logging.getLogger(__name__)


def _sign(secret: str, timestamp: str) -> str:
    key = f"{timestamp}\n{secret}".encode("utf-8")
    msg = hmac.new(key, b"", hashlib.sha256).digest()
    return base64.b64encode(msg).decode("utf-8")


def _clean_inline(text: str) -> str:
    """Remove inline markdown: bold, italic, code, links."""
    if not text:
        return ""
    text = re.sub(r"\*{1,3}(.+?)\*{1,3}", r"\1", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    return text.strip()

_EMOJI_NUMBERS = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"]

def _bullet_lines(items: list[str], max_len: int = 100) -> list[str]:
    """Format a list of items with emoji number bullets."""
    lines = []
    for i, item in enumerate(items):
        emoji = _EMOJI_NUMBERS[i] if i < len(_EMOJI_NUMBERS) else "•"
        lines.append(f"  {emoji} {item[:max_len]}")
    return lines

def _extract_section(text: str, *headings: str) -> str:
    """Extract content under the first matching markdown heading."""
    for heading in headings:
        pattern = rf"#{1,3}\s*{re.escape(heading)}.*?\n(.*?)(?=\n#{1,3}\s|\Z)"
        match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
        if match:
            return match.group(1).strip()
    return ""


def _extract_tldr(text: str) -> str:
    section = _extract_section(text, "TL;DR", "TLDR", "TL：DR")
    if not section:
        return ""
    # Take first 3 sentences max
    sentences = re.split(r"(?<=[。.!?])\s*", _clean_inline(section))
    return " ".join(s.strip() for s in sentences[:3] if s.strip())


def _extract_bullets(text: str, *headings: str) -> list[str]:
    section = _extract_section(text, *headings)
    if not section:
        return []
    bullets = []
    for line in section.split("\n"):
        line = line.strip()
        if re.match(r"^[-*•]\s+", line):
            cleaned = _clean_inline(re.sub(r"^[-*•]\s+", "", line))
            if cleaned:
                bullets.append(cleaned)
    return bullets[:3]


def _extract_sentiment(text: str) -> str:
    """Extract sentiment/outlook score line like 'Sentiment: 8/10 — Bullish'."""
    match = re.search(
        r"\*{0,2}((?:Sentiment|Outlook|情绪|展望)\s*:\s*\d+/10[^*\n]*)\*{0,2}",
        text, re.IGNORECASE
    )
    return match.group(1).strip() if match else ""


def _extract_tickers(text: str) -> list[str]:
    """Extract ticker symbols from 'Stock Tickers Mentioned' section."""
    section = _extract_section(text, "Stock Tickers Mentioned", "Tokens/Projects Mentioned")
    if not section:
        return []
    tickers = []
    for line in section.split("\n"):
        m = re.match(r"^[-*•]?\s*\*{0,2}([A-Z]{1,5})\b", line.strip())
        if m:
            tickers.append(m.group(1))
    return list(dict.fromkeys(tickers))[:8]  # dedupe, max 8


def _extract_stock_details(text: str) -> list[dict]:
    """Extract per-stock detail blocks (### TICKER — Name)."""
    pattern = r"###\s+([A-Z]{1,5})\s*[—–-]\s*([^\n]+)\n(.*?)(?=\n###|\n##|\Z)"
    matches = re.finditer(pattern, text, re.DOTALL)
    stocks = []
    for m in matches:
        ticker = m.group(1).strip()
        name = _clean_inline(m.group(2).strip())
        block = m.group(3)

        def _field(label):
            fm = re.search(rf"\*{{0,2}}(?:{label})\*{{0,2}}\s*:\s*(.+?)(?=\n\s*[-*]|\n\*{{0,2}}|\Z)",
                           block, re.IGNORECASE | re.DOTALL)
            return _clean_inline(fm.group(1).strip().split("\n")[0]) if fm else ""

        stocks.append({
            "ticker": ticker,
            "name": name,
            "conviction": _field("Conviction"),
            "bull": _field("Bull Thesis|Bull Case"),
            "bear": _field("Bear Thesis|Bear Case"),
            "target": _field("Price Target|Price Levels"),
            "takeaway": _field("Key Takeaway"),
        })
    return stocks[:5]


def _format_finance(
    video_title: str, video_url: str, channel_display: str,
    date_display: str, summary: str, lang: str, content_type: str,
) -> list[str]:
    """Format a finance-focused summary block."""
    lines = []

    # ── Sentiment / Outlook ──────────────────────────
    sentiment = _extract_sentiment(summary)
    if sentiment:
        lines += ["", f"📊 {sentiment}", ""]
        # Remove sentiment line from summary to prevent duplication in fallback
        summary = re.sub(
            r"\*{0,2}(?:Sentiment|Outlook)\s*:\s*\d+/10[^\n]*\*{0,2}\n?",
            "", summary, flags=re.IGNORECASE
        )

    # ── Stock / Crypto tickers overview ─────────────
    if content_type in ("stock_analysis", "crypto"):
        tickers = _extract_tickers(summary)
        if tickers:
            lines.append("🏷 涉及标的")
            lines.append("  " + " · ".join(tickers))
            lines.append("")

        # Per-stock detail blocks
        stocks = _extract_stock_details(summary)
        for s in stocks:
            lines.append(f"📌 {s['ticker']} — {s['name']}")
            if s["conviction"]:
                lines.append(f"  信心：{s['conviction']}")
            if s["bull"]:
                lines.append(f"  多头：{s['bull'][:80]}")
            if s["bear"]:
                lines.append(f"  空头：{s['bear'][:80]}")
            if s["target"]:
                lines.append(f"  目标价：{s['target'][:60]}")
            if s["takeaway"]:
                lines.append(f"  结论：{s['takeaway'][:80]}")
            lines.append("")

    # ── Macro indicators ────────────────────────────
    if content_type == "macro_economics":
        indicators = _extract_bullets(summary, "Key Economic Indicators", "关键经济指标")
        if indicators:
            lines.append("📈 关键经济指标")
            lines += _bullet_lines(indicators)
            lines.append("")

        policy = _extract_section(summary, "Central Bank", "Policy", "央行", "货币政策", "政策")
        if policy:
            first_line = _clean_inline(policy.split("\n")[0])
            if first_line:
                lines.append(f"🏦 央行政策")
                lines.append(f"  {first_line[:120]}")
                lines.append("")

        sector = _extract_bullets(summary, "Sector", "Asset Class", "行业", "板块", "资产类别")
        if sector:
            lines.append("🎯 板块观点")
            lines += _bullet_lines(sector)
            lines.append("")

    # ── News key facts ───────────────────────────────
    if content_type == "news":
        headline = _extract_section(summary, "Headline Summary")
        if headline:
            lines.append(_clean_inline(headline.split("\n")[0])[:150])
            lines.append("")

        facts = _extract_bullets(summary, "Key Facts", "关键事实")
        if facts:
            lines.append("📰 关键事实")
            lines += _bullet_lines(facts)
            lines.append("")

        implications = _extract_bullets(summary, "Implications", "影响", "展望")
        if implications:
            lines.append("🔮 影响与展望")
            lines += _bullet_lines(implications)
            lines.append("")

    # ── Podcast / Interview ──────────────────────────
    if content_type == "podcast_interview":
        guests = _extract_section(summary, "Guests", "Context", "嘉宾", "背景")
        if guests:
            first = _clean_inline(guests.split("\n")[0])
            if first:
                lines.append(f"🎙 嘉宾：{first[:100]}")
                lines.append("")

        contrarian = _extract_bullets(summary, "Surprising", "Contrarian",  "反常识", "出人意料")
        if contrarian:
            lines.append("💥 反常识观点")
            lines += _bullet_lines(contrarian)
            lines.append("")

    # ── Actionable Takeaways (all finance types) ─────
    actionable = _extract_bullets(
        summary, "Actionable Takeaway", "Actionable", "Key Takeaway", "可操作", "行动建议", "投资建议", "操作建议"
    )
    if actionable:
        lines.append("✅ 可操作建议")
        lines += _bullet_lines(actionable)
        lines.append("")

    # ── Macro / Other info (stock type) ─────────────
    if content_type in ("stock_analysis", "crypto"):
        other = _extract_bullets(summary, "Other Key Information", "Macro", "其他", "宏观", "催化剂")
        if other:
            lines.append("🌍 宏观与催化剂")
            lines += _bullet_lines(other)
            lines.append("")

    # ── TL;DR ────────────────────────────────────────
    tldr = _extract_tldr(summary)
    if tldr:
        lines += ["", "············ 💡 总结 ············", tldr]

    # ── Fallback: if nothing was extracted, show clean plain text ───
    content_lines = [l for l in lines if l and not l.startswith("━")]
    if len(content_lines) <= 2:
        cleaned = summary
        # Remove generic intro sentences
        cleaned = re.sub(r"^这是一份.+?(?:总结|摘要)[。.]?\n?", "", cleaned, flags=re.MULTILINE)
        cleaned = re.sub(r"^以下是.+?(?:总结|摘要)[：:。.]?\n?", "", cleaned, flags=re.MULTILINE)
        cleaned = re.sub(r"^报告[：:]\s*\n?", "", cleaned, flags=re.MULTILINE)
        # Headers
        cleaned = re.sub(r"^#{1,6}\s+TL;DR.*$", r"\n💡 总结", cleaned, flags=re.MULTILINE | re.IGNORECASE)
        cleaned = re.sub(r"^#{1,6}\s+(.+)$", r"\n▌\1", cleaned, flags=re.MULTILINE)
        # Bold/italic (including trailing **)
        cleaned = re.sub(r"\*{1,3}(.+?)\*{1,3}", r"\1", cleaned)
        cleaned = re.sub(r"\*+", "", cleaned)
        # Horizontal rules
        cleaned = re.sub(r"^-{2,}$", "", cleaned, flags=re.MULTILINE)
        # Bullet points — replace with emoji numbers per section
        def _replace_bullets(m):
            block = m.group(0)
            items = re.findall(r"^[-*•]\s+(.+)$", block, flags=re.MULTILINE)
            items = items[:3]  # max 3 per section
            result = []
            for i, item in enumerate(items):
                emoji = _EMOJI_NUMBERS[i] if i < len(_EMOJI_NUMBERS) else "•"
                result.append(f"  {emoji} {item}")
            return "\n".join(result)
        # Normalize Chinese-style bullets (3+ spaces + content) to standard bullets
        cleaned = re.sub(r"^   +(\S)", r"- \1", cleaned, flags=re.MULTILINE)
        cleaned = re.sub(r"((?:^[-*•]\s+.+\n?){1,})", _replace_bullets, cleaned, flags=re.MULTILINE)
        # Numbered list items — keep only first 3
        numbered = re.findall(r"^\d+\..+$", cleaned, flags=re.MULTILINE)
        if len(numbered) > 3:
            for item in numbered[3:]:
                cleaned = cleaned.replace(item + "\n", "")
        # Excessive blank lines
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
        lines.append(cleaned.strip()[:2500])

    return lines


def _format_message(
    video_title: str, video_id: str, summaries: dict,
    channel: str, published_at: str = "", platform: str = "youtube",
    content_type: str = "general",
) -> str:
    if platform == "bilibili":
        bvid = video_id.replace("bilibili:", "")
        video_url = f"https://www.bilibili.com/video/{bvid}"
    else:
        video_url = f"https://www.youtube.com/watch?v={video_id}"

    channel_display = f"@{channel}" if platform == "youtube" else channel
    date_display = published_at[:10] if published_at else ""

    finance_types = {"stock_analysis", "macro_economics", "crypto", "news", "podcast_interview"}

    # ── Header ──────────────────────────────────────
    lines = [
        f"【{channel_display}】",
        f"🎬 {video_title}",
    ]
    if date_display:
        lines.append(f"🕐 {date_display}  ·  🔗 {video_url}")
    else:
        lines.append(f"🔗 {video_url}")

    # ── Per-language summary ─────────────────────────
    for lang, summary in summaries.items():
        if len(summaries) > 1:
            lines += ["", f"············ 📋 摘要 ({lang}) ············"]
        else:
            lines += ["", "············ 📋 摘要 ············"]

        if content_type in finance_types:
            lines += _format_finance(
                video_title, video_url, channel_display,
                date_display, summary, lang, content_type,
            )
        else:
            # Fallback: clean plain text
            cleaned = re.sub(r"^#{1,6}\s+TL;DR.*$", r"\n💡 总结", summary, flags=re.MULTILINE | re.IGNORECASE)
            cleaned = re.sub(r"^#{1,6}\s+(.+)$", r"\n▌\1", cleaned, flags=re.MULTILINE)
            cleaned = re.sub(r"\*{1,3}(.+?)\*{1,3}", r"\1", cleaned)
            cleaned = re.sub(r"\*+", "", cleaned)
            cleaned = re.sub(r"^-{2,}$", "", cleaned, flags=re.MULTILINE)
            cleaned = re.sub(r"^[-*]\s+", "• ", cleaned, flags=re.MULTILINE)
            cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
            lines.append(cleaned.strip()[:2500])

    lines.append("")
    return "\n".join(lines)


def send_feishu_notification(
    video_title: str,
    video_id: str,
    summaries: dict,
    channel: str,
    content_type: str = "general",
    published_at: str = "",
    platform: str = "youtube",
) -> None:
    """Send a video summary notification to Feishu via webhook."""
    message_text = _format_message(
        video_title, video_id, summaries, channel,
        published_at=published_at,
        platform=platform,
        content_type=content_type,
    )

    timestamp = str(int(time.time()))
    payload = {
        "timestamp": timestamp,
        "msg_type": "text",
        "content": {"text": message_text},
    }

    feishu_secret = getattr(config, "FEISHU_SECRET", "")
    if feishu_secret:
        payload["sign"] = _sign(feishu_secret, timestamp)

    webhook_url = config.FEISHU_WEBHOOK_URL
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        webhook_url, data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=10) as resp:
        result = json.loads(resp.read().decode("utf-8"))
        if result.get("code", 0) != 0:
            raise RuntimeError(f"Feishu API error: {result}")

    logger.info("Feishu notification sent successfully")