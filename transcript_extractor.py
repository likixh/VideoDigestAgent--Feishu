"""Extract transcripts from YouTube videos.

Strategy:
  1. Try YouTube's built-in captions (free, instant)
  2. If no captions → download audio with yt-dlp and transcribe with Whisper (free, ~2 min)
"""

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


def _transcribe_with_whisper(video_id: str) -> str:
    """Download audio with yt-dlp and transcribe with Whisper."""
    import whisper
    import yt_dlp

    os.makedirs(AUDIO_TMP_DIR, exist_ok=True)
    audio_path = os.path.join(AUDIO_TMP_DIR, f"{video_id}.mp3")

    try:
        # Download audio
        logger.info("Downloading audio for %s...", video_id)
        ydl_opts = {
            "format": "bestaudio/best",
            "outtmpl": os.path.join(AUDIO_TMP_DIR, f"{video_id}.%(ext)s"),
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "64",
            }],
            "quiet": True,
            "no_warnings": True,
        }
        url = f"https://www.youtube.com/watch?v={video_id}"
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
