"""Monitor YouTube channels and search for new videos."""

import json
import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone

from googleapiclient.discovery import build

import config
from history import get_processed_ids, mark_seen

logger = logging.getLogger(__name__)


# ── Channel ID cache ────────────────────────────────────────────────────────

def _load_channel_cache() -> dict:
    if not os.path.exists(config.CHANNEL_CACHE_FILE):
        return {}
    with open(config.CHANNEL_CACHE_FILE, "r") as f:
        return json.load(f)


def _save_channel_cache(cache: dict) -> None:
    with open(config.CHANNEL_CACHE_FILE, "w") as f:
        json.dump(cache, f, indent=2)


def _resolve_channel_id(youtube, handle: str) -> str:
    """Resolve a channel handle to a channel ID, with disk caching."""
    cache = _load_channel_cache()
    if handle in cache:
        logger.debug("Channel cache hit: @%s -> %s", handle, cache[handle])
        return cache[handle]

    # Try the cheap channels.list forHandle first (1 unit)
    try:
        resp = youtube.channels().list(part="id", forHandle=handle).execute()
        items = resp.get("items", [])
        if items:
            channel_id = items[0]["id"]
            cache[handle] = channel_id
            _save_channel_cache(cache)
            logger.info("Resolved @%s -> %s (via channels.list)", handle, channel_id)
            return channel_id
    except Exception:
        pass

    # Fallback to search (100 units)
    resp = youtube.search().list(
        part="snippet",
        q=f"@{handle}",
        type="channel",
        maxResults=1,
    ).execute()

    items = resp.get("items", [])
    if not items:
        raise RuntimeError(f"Could not find channel for handle @{handle}")

    channel_id = items[0]["snippet"]["channelId"]
    cache[handle] = channel_id
    _save_channel_cache(cache)
    logger.info("Resolved @%s -> %s (via search)", handle, channel_id)
    return channel_id


# ── Channel monitoring ──────────────────────────────────────────────────────

def _get_new_videos_for_channel(youtube, handle: str, processed: set[str]) -> list[dict]:
    """Fetch new (unprocessed) videos from a single channel."""
    channel_id = _resolve_channel_id(youtube, handle)

    ch_resp = youtube.channels().list(
        part="contentDetails", id=channel_id
    ).execute()
    uploads_playlist = ch_resp["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]

    pl_resp = youtube.playlistItems().list(
        part="snippet",
        playlistId=uploads_playlist,
        maxResults=5,
    ).execute()

    new_videos = []
    for item in pl_resp.get("items", []):
        snippet = item["snippet"]
        vid_id = snippet["resourceId"]["videoId"]
        if vid_id in processed:
            continue
        new_videos.append({
            "video_id": vid_id,
            "title": snippet["title"],
            "published_at": snippet["publishedAt"],
            "description": snippet.get("description", ""),
            "channel": handle,
            "source": "channel",
        })

    return new_videos


def initialize() -> None:
    """First-run setup: mark all existing videos as processed so we only
    pick up truly new uploads going forward."""
    if os.path.exists(config.PROCESSED_VIDEOS_FILE):
        return

    logger.info("First run detected — marking existing videos as seen...")
    youtube = build("youtube", "v3", developerKey=config.YOUTUBE_API_KEY)

    for handle in config.YOUTUBE_CHANNELS:
        try:
            channel_id = _resolve_channel_id(youtube, handle)
            ch_resp = youtube.channels().list(
                part="contentDetails", id=channel_id
            ).execute()
            uploads_playlist = ch_resp["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]

            pl_resp = youtube.playlistItems().list(
                part="snippet",
                playlistId=uploads_playlist,
                maxResults=1,
            ).execute()

            for item in pl_resp.get("items", []):
                vid_id = item["snippet"]["resourceId"]["videoId"]
                mark_seen(vid_id)

            logger.info("@%s: marked %d existing video(s) as seen",
                        handle, len(pl_resp.get("items", [])))
        except Exception:
            logger.exception("Error initializing channel @%s", handle)

    logger.info("Initialization complete. Will only process new uploads from now on.")


# ── Search quota tracking ───────────────────────────────────────────────────

def _today_pacific() -> str:
    """Return today's date in Pacific Time (YouTube quota resets at midnight PT)."""
    pacific_offset = timedelta(hours=-8)
    now_pacific = datetime.now(timezone(pacific_offset))
    return now_pacific.date().isoformat()


def _load_search_state() -> dict:
    if not os.path.exists(config.SEARCH_STATE_FILE):
        return {"date": "", "quota_used": 0, "last_search_time": 0}
    with open(config.SEARCH_STATE_FILE, "r") as f:
        return json.load(f)


def _save_search_state(state: dict) -> None:
    with open(config.SEARCH_STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def _track_quota(units: int) -> None:
    state = _load_search_state()
    today = _today_pacific()
    if state.get("date") != today:
        state["date"] = today
        state["quota_used"] = 0
    state["quota_used"] += units
    _save_search_state(state)


def _quota_remaining() -> int:
    state = _load_search_state()
    today = _today_pacific()
    if state.get("date") != today:
        return config.YOUTUBE_SEARCH_QUOTA_BUDGET
    return max(0, config.YOUTUBE_SEARCH_QUOTA_BUDGET - state.get("quota_used", 0))


def _is_search_due() -> bool:
    state = _load_search_state()
    last_time = state.get("last_search_time", 0)
    return (time.time() - last_time) >= config.YOUTUBE_SEARCH_INTERVAL


def _mark_search_done() -> None:
    state = _load_search_state()
    state["last_search_time"] = time.time()
    _save_search_state(state)


# ── Pre-filtering ───────────────────────────────────────────────────────────

def _pre_filter_video(video: dict) -> bool:
    """Quick relevance check on title + description. Returns True if relevant."""
    if not config.YOUTUBE_SEARCH_RELEVANCE_KEYWORDS:
        return True
    text = (video.get("title", "") + " " + video.get("description", "")).lower()
    return any(kw in text for kw in config.YOUTUBE_SEARCH_RELEVANCE_KEYWORDS)


def _filter_by_duration(youtube, videos: list[dict]) -> list[dict]:
    """Remove videos shorter than YOUTUBE_SEARCH_MIN_DURATION minutes."""
    if not videos or config.YOUTUBE_SEARCH_MIN_DURATION <= 0:
        return videos

    video_ids = [v["video_id"] for v in videos]
    durations = {}

    for i in range(0, len(video_ids), 50):
        batch = video_ids[i : i + 50]
        try:
            resp = youtube.videos().list(
                part="contentDetails", id=",".join(batch)
            ).execute()
            _track_quota(1)
            for item in resp.get("items", []):
                dur = item["contentDetails"]["duration"]
                match = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", dur)
                if match:
                    hours = int(match.group(1) or 0)
                    minutes = int(match.group(2) or 0)
                    seconds = int(match.group(3) or 0)
                    durations[item["id"]] = hours * 60 + minutes + seconds / 60
        except Exception as e:
            logger.warning("Duration check failed: %s", e)

    min_dur = config.YOUTUBE_SEARCH_MIN_DURATION
    filtered = []
    for v in videos:
        dur = durations.get(v["video_id"], 999)
        if dur >= min_dur:
            filtered.append(v)
        else:
            logger.debug("Skipping short video (%.1f min): %s", dur, v["title"])

    skipped = len(videos) - len(filtered)
    if skipped > 0:
        logger.info("Filtered out %d short video(s) (< %d min)", skipped, min_dur)
    return filtered


# ── YouTube search ──────────────────────────────────────────────────────────

def _search_youtube(youtube, query: str, published_after: str,
                    processed: set[str], max_results: int) -> list[dict]:
    """Execute a single YouTube search query and return new, relevant videos."""
    remaining = _quota_remaining()
    if remaining < 100:
        logger.warning("Search quota exhausted (%d units remaining). Skipping.", remaining)
        return []

    try:
        resp = youtube.search().list(
            part="snippet",
            q=query,
            type="video",
            order="relevance",
            publishedAfter=published_after,
            maxResults=max_results,
            videoDuration="medium",
        ).execute()
        _track_quota(100)
    except Exception as e:
        logger.error("Search query '%s' failed: %s", query, e)
        return []

    new_videos = []
    for item in resp.get("items", []):
        snippet = item["snippet"]
        vid_id = item["id"]["videoId"]

        if vid_id in processed:
            continue

        video = {
            "video_id": vid_id,
            "title": snippet["title"],
            "published_at": snippet["publishedAt"],
            "description": snippet.get("description", ""),
            "channel": snippet.get("channelTitle", "search"),
            "source": "search",
            "search_query": query,
        }

        if not _pre_filter_video(video):
            logger.debug("Skipping irrelevant result: %s", video["title"])
            continue

        new_videos.append(video)

    return new_videos


def get_search_videos() -> list[dict]:
    """Return new videos from keyword search queries.

    Respects quota budget and search interval.
    """
    if not config.YOUTUBE_SEARCH_ENABLED:
        return []

    if not _is_search_due():
        logger.debug("Search not due yet (interval: %ds)", config.YOUTUBE_SEARCH_INTERVAL)
        return []

    youtube = build("youtube", "v3", developerKey=config.YOUTUBE_API_KEY)
    processed = get_processed_ids()

    # Time window: since last search (with 1h overlap), or last 24h for first run
    state = _load_search_state()
    last_search = state.get("last_search_time", 0)
    if last_search > 0:
        since = datetime.fromtimestamp(last_search - 3600, tz=timezone.utc)
    else:
        since = datetime.now(timezone.utc) - timedelta(hours=24)
    published_after = since.strftime("%Y-%m-%dT%H:%M:%SZ")

    all_new = []
    seen_ids = set()

    for query in config.YOUTUBE_SEARCH_QUERIES:
        if _quota_remaining() < 100:
            logger.warning("Quota budget reached. Stopping search early.")
            break

        results = _search_youtube(
            youtube, query, published_after, processed,
            config.YOUTUBE_SEARCH_MAX_RESULTS,
        )
        for video in results:
            if video["video_id"] not in seen_ids:
                seen_ids.add(video["video_id"])
                all_new.append(video)

        logger.info("Search '%s': %d new result(s)", query, len(results))

    # Filter out shorts / very short clips
    if all_new:
        all_new = _filter_by_duration(youtube, all_new)

    _mark_search_done()
    logger.info(
        "Search total: %d new video(s) from %d queries (quota remaining: %d)",
        len(all_new), len(config.YOUTUBE_SEARCH_QUERIES), _quota_remaining(),
    )
    return all_new


# ── Main entry point ────────────────────────────────────────────────────────

def get_new_videos() -> list[dict]:
    """Return new (unprocessed) videos from all channels AND search queries.

    Each dict contains: video_id, title, published_at, description, channel, source.
    Search results additionally have: search_query.
    """
    initialize()

    youtube = build("youtube", "v3", developerKey=config.YOUTUBE_API_KEY)
    processed = get_processed_ids()
    all_new = []
    seen_ids = set()

    # 1. Channel monitoring (cheap, every poll)
    for handle in config.YOUTUBE_CHANNELS:
        try:
            videos = _get_new_videos_for_channel(youtube, handle, processed)
            for v in videos:
                seen_ids.add(v["video_id"])
            all_new.extend(videos)
            logger.info("@%s: %d new video(s)", handle, len(videos))
        except Exception:
            logger.exception("Error checking channel @%s", handle)

    # 2. Keyword search (only when due, respects quota)
    search_videos = get_search_videos()
    for v in search_videos:
        if v["video_id"] not in seen_ids:
            all_new.append(v)
            seen_ids.add(v["video_id"])
        else:
            logger.debug("Dedup: search result %s already found via channel", v["video_id"])

    channel_count = len(config.YOUTUBE_CHANNELS)
    search_count = len(config.YOUTUBE_SEARCH_QUERIES) if config.YOUTUBE_SEARCH_ENABLED else 0
    logger.info(
        "Total: %d new video(s) across %d channel(s) and %d search queries",
        len(all_new), channel_count, search_count,
    )
    return all_new
