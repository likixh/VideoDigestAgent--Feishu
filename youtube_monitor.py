"""Monitor a YouTube channel for new video uploads."""

import json
import logging
import os

from googleapiclient.discovery import build

import config

logger = logging.getLogger(__name__)


def _load_processed() -> set[str]:
    if os.path.exists(config.PROCESSED_VIDEOS_FILE):
        with open(config.PROCESSED_VIDEOS_FILE, "r") as f:
            return set(json.load(f))
    return set()


def _save_processed(video_ids: set[str]) -> None:
    with open(config.PROCESSED_VIDEOS_FILE, "w") as f:
        json.dump(sorted(video_ids), f, indent=2)


def mark_processed(video_id: str) -> None:
    processed = _load_processed()
    processed.add(video_id)
    _save_processed(processed)


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


def get_new_videos() -> list[dict]:
    """Return a list of new (unprocessed) videos from the configured channel.

    Each dict contains: video_id, title, published_at, description.
    """
    youtube = build("youtube", "v3", developerKey=config.YOUTUBE_API_KEY)

    channel_id = _resolve_channel_id(youtube, config.YOUTUBE_CHANNEL_HANDLE)

    # Get the uploads playlist
    ch_resp = youtube.channels().list(
        part="contentDetails", id=channel_id
    ).execute()
    uploads_playlist = ch_resp["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]

    # Fetch the most recent 10 uploads
    pl_resp = youtube.playlistItems().list(
        part="snippet",
        playlistId=uploads_playlist,
        maxResults=10,
    ).execute()

    processed = _load_processed()
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
        })

    logger.info("Found %d new video(s)", len(new_videos))
    return new_videos
