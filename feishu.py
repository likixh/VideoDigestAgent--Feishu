"""Send summary notifications via Feishu (Lark) webhook."""

import logging
import time
import hmac
import hashlib
import base64
import json
import urllib.request

import config

logger = logging.getLogger(__name__)


def _sign(secret: str, timestamp: str) -> str:
    """Generate Feishu webhook signature."""
    key = f"{timestamp}\n{secret}".encode("utf-8")
    msg = hmac.new(key, b"", hashlib.sha256).digest()
    return base64.b64encode(msg).decode("utf-8")


def send_feishu_notification(
    video_title: str,
    video_id: str,
    summaries: dict,
    channel: str,
    content_type: str = "general",
    published_at: str = "",
    platform: str = "youtube",
    transcript: str = "",
) -> None:
    """Send a video summary notification to Feishu via webhook."""

    if platform == "bilibili":
        bvid = video_id.replace("bilibili:", "")
        video_url = f"https://www.bilibili.com/video/{bvid}"
    else:
        video_url = f"https://www.youtube.com/watch?v={video_id}"

    channel_display = f"@{channel}" if platform == "youtube" else channel

    # Build message text
    lines = [
        f"📹 新视频摘要 | New Video Summary",
        f"",
        f"频道 Channel: {channel_display}",
        f"标题 Title: {video_title}",
        f"链接 Link: {video_url}",
    ]

    if published_at:
        lines.append(f"发布时间 Published: {published_at[:10]}")

    lines.append("")
    lines.append("─" * 40)

    for lang, summary in summaries.items():
        lines.append(f"")
        lines.append(f"【{lang}】")
        lines.append(summary[:2000])  # Feishu has message length limits

    message_text = "\n".join(lines)

    # Build payload
    timestamp = str(int(time.time()))
    payload = {
        "timestamp": timestamp,
        "msg_type": "text",
        "content": {"text": message_text},
    }

    # Add signature if secret is configured
    feishu_secret = getattr(config, "FEISHU_SECRET", "")
    if feishu_secret:
        payload["sign"] = _sign(feishu_secret, timestamp)

    webhook_url = config.FEISHU_WEBHOOK_URL
    data = json.dumps(payload).encode("utf-8")

    req = urllib.request.Request(
        webhook_url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=10) as resp:
        result = json.loads(resp.read().decode("utf-8"))
        if result.get("code", 0) != 0:
            raise RuntimeError(f"Feishu API error: {result}")

    logger.info("Feishu notification sent successfully")