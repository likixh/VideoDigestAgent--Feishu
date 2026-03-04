"""Monitor Bilibili user spaces for new video uploads.

Uses the bilibili-api-python library to fetch video lists from
monitored Bilibili users, similar to how youtube_monitor.py
watches YouTube channels.
"""

import asyncio
import json
import logging
import os

import config
from history import get_processed_ids, mark_seen

logger = logging.getLogger(__name__)


def _get_credential():
    """Build a Bilibili Credential from configured cookies."""
    from bilibili_api import Credential

    sessdata = config.BILIBILI_SESSDATA
    bili_jct = config.BILIBILI_BILI_JCT
    buvid3 = config.BILIBILI_BUVID3

    if sessdata and bili_jct and buvid3:
        return Credential(sessdata=sessdata, bili_jct=bili_jct, buvid3=buvid3)

    logger.warning(
        "Bilibili cookies not fully configured. "
        "Subtitle/transcript fetching may not work."
    )
    return None


async def _fetch_user_videos(uid: int, credential, count: int = 10) -> list[dict]:
    """Fetch the latest videos from a Bilibili user's space."""
    from bilibili_api import user

    u = user.User(uid=uid, credential=credential)

    try:
        info = await u.get_user_info()
        username = info.get("name", str(uid))
    except Exception:
        username = str(uid)

    try:
        result = await u.get_videos(pn=1, ps=count)
    except Exception as e:
        logger.error("Failed to fetch videos for Bilibili user %s: %s", uid, e)
        return []

    videos = []
    vlist = result.get("list", {}).get("vlist", [])
    for v in vlist:
        bvid = v.get("bvid", "")
        if not bvid:
            continue
        videos.append({
            "video_id": f"bilibili:{bvid}",
            "bvid": bvid,
            "title": v.get("title", ""),
            "published_at": str(v.get("created", "")),
            "description": v.get("description", ""),
            "channel": username,
            "source": "bilibili",
            "platform": "bilibili",
            "thumbnail": v.get("pic", ""),
            "duration": v.get("length", ""),
        })

    return videos


def _get_new_videos_for_user(uid: int, credential, processed: set[str]) -> list[dict]:
    """Fetch new (unprocessed) videos from a single Bilibili user."""
    videos = asyncio.run(_fetch_user_videos(uid, credential))
    return [v for v in videos if v["video_id"] not in processed]


def initialize() -> None:
    """First-run setup: mark existing Bilibili videos as seen."""
    if not config.BILIBILI_ENABLED or not config.BILIBILI_USERS:
        return

    # Only initialize if there's no history file yet
    if os.path.exists(config.PROCESSED_VIDEOS_FILE):
        return

    logger.info("Bilibili first run — marking existing videos as seen...")
    credential = _get_credential()

    for uid_str in config.BILIBILI_USERS:
        try:
            uid = int(uid_str)
        except ValueError:
            logger.warning("Invalid Bilibili UID: %s (must be numeric)", uid_str)
            continue

        try:
            videos = asyncio.run(_fetch_user_videos(uid, credential, count=5))
            for v in videos:
                mark_seen(v["video_id"])
            logger.info(
                "Bilibili user %s: marked %d existing video(s) as seen",
                uid, len(videos),
            )
        except Exception:
            logger.exception("Error initializing Bilibili user %s", uid)

    logger.info("Bilibili initialization complete.")


def get_new_videos() -> list[dict]:
    """Return new (unprocessed) Bilibili videos from all monitored users.

    Each dict contains: video_id, bvid, title, published_at, description,
    channel, source, platform, thumbnail, duration.
    """
    if not config.BILIBILI_ENABLED:
        return []

    if not config.BILIBILI_USERS:
        logger.debug("Bilibili enabled but no users configured")
        return []

    initialize()

    credential = _get_credential()
    processed = get_processed_ids()
    all_new = []

    for uid_str in config.BILIBILI_USERS:
        try:
            uid = int(uid_str)
        except ValueError:
            logger.warning("Invalid Bilibili UID: %s (must be numeric)", uid_str)
            continue

        try:
            videos = _get_new_videos_for_user(uid, credential, processed)
            all_new.extend(videos)
            logger.info("Bilibili user %s: %d new video(s)", uid, len(videos))
        except Exception:
            logger.exception("Error checking Bilibili user %s", uid)

    logger.info("Bilibili total: %d new video(s) from %d user(s)",
                len(all_new), len(config.BILIBILI_USERS))
    return all_new
