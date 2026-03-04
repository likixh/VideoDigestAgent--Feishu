"""Extract transcripts from YouTube and Bilibili videos.

Strategy:
  YouTube:
    1. Try YouTube's built-in captions (free, instant)
    2. If no captions → download audio with yt-dlp and transcribe with Whisper (free, ~2 min)

  Bilibili:
    1. Try Bilibili's subtitle API via bilibili-api-python (requires cookies)
    2. If no subtitles → download audio with yt-dlp and transcribe with Whisper
"""

import asyncio
import logging
import os
import shutil
import tempfile

from youtube_transcript_api import YouTubeTranscriptApi

logger = logging.getLogger(__name__)

AUDIO_TMP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "audio_tmp")


def _get_youtube_captions(video_id: str) -> str | None:
    """Try to get captions from YouTube. Returns None if unavailable."""
    try:
        ytt = YouTubeTranscriptApi()
        transcript_list = ytt.list(video_id)

        try:
            transcript = transcript_list.find_transcript(["en"])
        except Exception:
            transcript = transcript_list.find_generated_transcript(["en"])

        entries = transcript.fetch()
        text = " ".join(entry.text for entry in entries)
        logger.info(
            "Got YouTube captions for %s (%d characters)", video_id, len(text)
        )
        return text
    except Exception as e:
        logger.warning("No YouTube captions for %s: %s", video_id, e)
        return None


def _transcribe_with_whisper(video_id: str, url: str | None = None) -> str:
    """Download audio with yt-dlp and transcribe with Whisper.

    Args:
        video_id: Unique identifier for the video (used for temp file naming).
        url: Direct URL to download. Defaults to YouTube URL if not provided.
    """
    import whisper
    import yt_dlp

    os.makedirs(AUDIO_TMP_DIR, exist_ok=True)
    # Sanitize video_id for filesystem (replace colons from bilibili:BVxxx)
    safe_id = video_id.replace(":", "_")
    audio_path = os.path.join(AUDIO_TMP_DIR, f"{safe_id}.mp3")

    if url is None:
        url = f"https://www.youtube.com/watch?v={video_id}"

    try:
        # Download audio
        logger.info("Downloading audio for %s...", video_id)
        ydl_opts = {
            "format": "bestaudio/best",
            "outtmpl": os.path.join(AUDIO_TMP_DIR, f"{safe_id}.%(ext)s"),
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "64",
            }],
            "quiet": True,
            "no_warnings": True,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        if not os.path.exists(audio_path):
            raise RuntimeError(f"Audio download failed for {video_id}")

        # Transcribe with Whisper (using "base" model — good balance of speed & accuracy)
        logger.info("Transcribing with Whisper (this may take a few minutes)...")
        model = whisper.load_model("base")
        result = model.transcribe(audio_path)
        text = result["text"]

        logger.info(
            "Whisper transcription for %s (%d characters)", video_id, len(text)
        )
        return text

    finally:
        # Clean up audio file
        if os.path.exists(audio_path):
            os.remove(audio_path)


def get_transcript(video_id: str) -> str:
    """Fetch the transcript for a YouTube video.

    Tries YouTube captions first, falls back to Whisper transcription.
    """
    # Strategy 1: YouTube captions (instant)
    text = _get_youtube_captions(video_id)
    if text:
        return text

    # Strategy 2: Whisper fallback (slower but works on any video with audio)
    logger.info("Falling back to Whisper transcription for %s", video_id)
    try:
        return _transcribe_with_whisper(video_id)
    except Exception as e:
        logger.error("Whisper transcription failed for %s: %s", video_id, e)
        raise RuntimeError(
            f"Could not extract transcript for video {video_id}. "
            "Neither YouTube captions nor Whisper transcription succeeded."
        ) from e


# ── Bilibili transcript extraction ─────────────────────────────────────────


async def _get_bilibili_subtitles(bvid: str) -> str | None:
    """Try to get subtitles from Bilibili's subtitle API.

    Requires BILIBILI_SESSDATA, BILIBILI_BILI_JCT, and BILIBILI_BUVID3 cookies.
    Returns None if no subtitles are available.
    """
    try:
        import config
        from bilibili_api import video, Credential

        credential = None
        if config.BILIBILI_SESSDATA and config.BILIBILI_BILI_JCT and config.BILIBILI_BUVID3:
            credential = Credential(
                sessdata=config.BILIBILI_SESSDATA,
                bili_jct=config.BILIBILI_BILI_JCT,
                buvid3=config.BILIBILI_BUVID3,
            )

        v = video.Video(bvid=bvid, credential=credential)
        info = await v.get_info()

        # Get subtitle list from video info
        subtitle_info = info.get("subtitle", {})
        subtitle_list = subtitle_info.get("list", [])

        if not subtitle_list:
            logger.info("No subtitles available for Bilibili video %s", bvid)
            return None

        # Prefer Chinese subtitles, then any available
        selected = None
        for sub in subtitle_list:
            lang = sub.get("lan", "")
            if "zh" in lang or "cn" in lang:
                selected = sub
                break
        if selected is None:
            selected = subtitle_list[0]

        subtitle_url = selected.get("subtitle_url", "")
        if not subtitle_url:
            return None

        # Ensure URL has protocol
        if subtitle_url.startswith("//"):
            subtitle_url = "https:" + subtitle_url

        # Fetch the subtitle JSON
        import httpx
        async with httpx.AsyncClient() as client:
            resp = await client.get(subtitle_url)
            resp.raise_for_status()
            subtitle_data = resp.json()

        # Parse subtitle entries into plain text
        body = subtitle_data.get("body", [])
        text = " ".join(entry.get("content", "") for entry in body)

        if text:
            logger.info(
                "Got Bilibili subtitles for %s (%d characters, lang: %s)",
                bvid, len(text), selected.get("lan", "?"),
            )
            return text

        return None

    except Exception as e:
        logger.warning("Failed to get Bilibili subtitles for %s: %s", bvid, e)
        return None


def get_bilibili_transcript(bvid: str) -> str:
    """Fetch the transcript for a Bilibili video.

    Tries Bilibili's subtitle API first, falls back to Whisper transcription.
    """
    # Strategy 1: Bilibili subtitle API (instant, requires cookies)
    text = asyncio.run(_get_bilibili_subtitles(bvid))
    if text:
        return text

    # Strategy 2: Whisper fallback (download audio from Bilibili via yt-dlp)
    bilibili_url = f"https://www.bilibili.com/video/{bvid}"
    video_id = f"bilibili:{bvid}"
    logger.info("Falling back to Whisper transcription for Bilibili %s", bvid)
    try:
        return _transcribe_with_whisper(video_id, url=bilibili_url)
    except Exception as e:
        logger.error("Whisper transcription failed for Bilibili %s: %s", bvid, e)
        raise RuntimeError(
            f"Could not extract transcript for Bilibili video {bvid}. "
            "Neither Bilibili subtitles nor Whisper transcription succeeded."
        ) from e
