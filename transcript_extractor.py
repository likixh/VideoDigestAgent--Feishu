"""Extract transcripts from YouTube videos."""

import logging

from youtube_transcript_api import YouTubeTranscriptApi

logger = logging.getLogger(__name__)


def get_transcript(video_id: str) -> str:
    """Fetch the transcript for a YouTube video and return it as plain text.

    Tries English captions first, then falls back to any available language.
    """
    try:
        ytt = YouTubeTranscriptApi()
        transcript_list = ytt.list(video_id)

        # Try English first
        try:
            transcript = transcript_list.find_transcript(["en"])
        except Exception:
            # Fall back to auto-generated or any available transcript
            transcript = transcript_list.find_generated_transcript(["en"])

        entries = transcript.fetch()
        text = " ".join(entry.text for entry in entries)
        logger.info(
            "Extracted transcript for %s (%d characters)", video_id, len(text)
        )
        return text

    except Exception as e:
        logger.error("Failed to get transcript for %s: %s", video_id, e)
        raise RuntimeError(
            f"Could not extract transcript for video {video_id}. "
            "The video may not have captions enabled."
        ) from e
