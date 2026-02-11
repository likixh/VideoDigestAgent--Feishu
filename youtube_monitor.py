"""Monitor YouTube channels for new video uploads."""

import logging
import os

from googleapiclient.discovery import build

import config
from history import get_processed_ids, mark_seen

logger = logging.getLogger(__name__)


def _resolve_channel_id(youtube, handle: str) -> str:
    """Resolve a channel handle (e.g. 'RhinoFinance') to a channel ID."""
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
    logger.info("Resolved @%s -> %s", handle, channel_id)
    return channel_id


def _get_new_videos_for_channel(youtube, handle: str, processed: set[str]) -> list[dict]:
    """Fetch new (unprocessed) videos from a single channel."""
    channel_id = _resolve_channel_id(youtube, handle)

    # Get the uploads playlist
    ch_resp = youtube.channels().list(
        part="contentDetails", id=channel_id
    ).execute()
    uploads_playlist = ch_resp["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]

    # Fetch the most recent upload only
    pl_resp = youtube.playlistItems().list(
        part="snippet",
        playlistId=uploads_playlist,
        maxResults=1,
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
        })

    return new_videos


def initialize() -> None:
    """First-run setup: mark all existing videos as processed so we only
    pick up truly new uploads going forward."""
    if os.path.exists(config.PROCESSED_VIDEOS_FILE):
        return  # Already initialized

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


def get_new_videos() -> list[dict]:
    """Return new (unprocessed) videos from all configured channels.

    Each dict contains: video_id, title, published_at, description, channel.
    """
    initialize()

    youtube = build("youtube", "v3", developerKey=config.YOUTUBE_API_KEY)
    processed = get_processed_ids()
    all_new = []

    for handle in config.YOUTUBE_CHANNELS:
        try:
            videos = _get_new_videos_for_channel(youtube, handle, processed)
            all_new.extend(videos)
            logger.info("@%s: %d new video(s)", handle, len(videos))
        except Exception:
            logger.exception("Error checking channel @%s", handle)

    logger.info("Total: %d new video(s) across %d channel(s)",
                len(all_new), len(config.YOUTUBE_CHANNELS))
    return all_new
