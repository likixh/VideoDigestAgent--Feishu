"""RAG Pipeline — Embedding + Vector Store + Retrieval.

Think of this like Elasticsearch for your video summaries:
- Documents (transcripts + summaries) get chunked and embedded
- Stored in a local ChromaDB vector database
- Retrieved via semantic similarity search

This enables:
- Cross-video context: "Last week you covered AAPL, here's what changed"
- Trend detection: "NVDA sentiment has shifted from 8/10 to 4/10 over 3 weeks"
- User queries: "What did all my channels say about the Fed last month?"

Architecture (for people who know Elasticsearch):
    Transcript → Chunker (like ES analyzers)
    Chunks → Embedder (like ES dense_vector)
    Embeddings → ChromaDB (like ES index)
    Query → Semantic Search (like ES knn query)
    Results → Context Window (like ES _source filtering)
"""

import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# Directory for persistent ChromaDB storage
CHROMA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chroma_db")

# ── Chunking Strategy ─────────────────────────────────────────────────────────
# Like an ES analyzer — break documents into indexable units.
# We use overlapping windows so we don't lose context at boundaries.


def chunk_text(text: str, chunk_size: int = 1000, overlap: int = 200) -> list[str]:
    """Split text into overlapping chunks (like ES's token filters).

    Args:
        text: The raw transcript or summary text.
        chunk_size: Target characters per chunk (like max_gram).
        overlap: Characters of overlap between chunks (prevents boundary loss).

    Returns:
        List of text chunks.
    """
    if not text:
        return []

    words = text.split()
    chunks = []
    current_chunk: list[str] = []
    current_length = 0

    for word in words:
        current_chunk.append(word)
        current_length += len(word) + 1  # +1 for space

        if current_length >= chunk_size:
            chunks.append(" ".join(current_chunk))
            # Keep last N characters worth of words for overlap
            overlap_words = []
            overlap_len = 0
            for w in reversed(current_chunk):
                if overlap_len + len(w) + 1 > overlap:
                    break
                overlap_words.insert(0, w)
                overlap_len += len(w) + 1
            current_chunk = overlap_words
            current_length = overlap_len

    if current_chunk:
        chunks.append(" ".join(current_chunk))

    return chunks


# ── Vector Store ──────────────────────────────────────────────────────────────
# ChromaDB = local Elasticsearch with dense vectors.
# No external service needed — runs entirely in-process.


class VideoRAGStore:
    """Vector store for video transcripts and summaries.

    Like an ES index with two types: 'transcript_chunks' and 'summaries'.
    Each document gets metadata (channel, date, content_type) for filtering.
    """

    def __init__(self, persist_dir: str = CHROMA_DIR):
        self._persist_dir = persist_dir
        self._client = None
        self._transcript_collection = None
        self._summary_collection = None

    def _ensure_initialized(self):
        """Lazy initialization — only import chromadb when actually used."""
        if self._client is not None:
            return

        try:
            import chromadb
            from chromadb.config import Settings
        except ImportError:
            raise ImportError(
                "chromadb is required for RAG features. "
                "Install it with: pip install chromadb"
            )

        os.makedirs(self._persist_dir, exist_ok=True)

        self._client = chromadb.PersistentClient(
            path=self._persist_dir,
            settings=Settings(anonymized_telemetry=False),
        )

        # Two collections — like two ES indices
        self._transcript_collection = self._client.get_or_create_collection(
            name="transcripts",
            metadata={"description": "Chunked video transcripts for semantic search"},
        )
        self._summary_collection = self._client.get_or_create_collection(
            name="summaries",
            metadata={"description": "Full video summaries for context retrieval"},
        )

        logger.info("RAG store initialized at %s", self._persist_dir)

    # ── Indexing (like ES bulk insert) ────────────────────────────────────────

    def index_video(
        self,
        video_id: str,
        title: str,
        channel: str,
        content_type: str,
        transcript: str,
        summaries: dict[str, str],
        published_at: str = "",
    ) -> int:
        """Index a video's transcript and summaries into the vector store.

        Like ES: POST /transcripts/_bulk + POST /summaries/_doc

        Returns the number of chunks indexed.
        """
        self._ensure_initialized()

        # Check if already indexed (idempotent, like ES upsert)
        existing = self._transcript_collection.get(
            where={"video_id": video_id},
            limit=1,
        )
        if existing and existing["ids"]:
            logger.info("Video %s already indexed, skipping", video_id)
            return 0

        # Chunk the transcript
        chunks = chunk_text(transcript)
        if not chunks:
            logger.warning("No chunks generated for video %s", video_id)
            return 0

        # Shared metadata (like ES _source fields)
        base_metadata = {
            "video_id": video_id,
            "title": title,
            "channel": channel,
            "content_type": content_type,
            "published_at": published_at,
            "indexed_at": datetime.now(timezone.utc).isoformat(),
        }

        # Index transcript chunks
        chunk_ids = [f"{video_id}_chunk_{i}" for i in range(len(chunks))]
        chunk_metadatas = [
            {**base_metadata, "chunk_index": i, "total_chunks": len(chunks)}
            for i in range(len(chunks))
        ]

        self._transcript_collection.add(
            ids=chunk_ids,
            documents=chunks,
            metadatas=chunk_metadatas,
        )

        # Index summaries (one document per language)
        for lang, summary in summaries.items():
            summary_id = f"{video_id}_summary_{lang}"
            self._summary_collection.add(
                ids=[summary_id],
                documents=[summary],
                metadatas=[{**base_metadata, "language": lang}],
            )

        logger.info(
            "Indexed video %s: %d transcript chunks + %d summaries",
            video_id, len(chunks), len(summaries),
        )
        return len(chunks)

    # ── Retrieval (like ES knn search) ────────────────────────────────────────

    def search_transcripts(
        self,
        query: str,
        n_results: int = 5,
        channel: Optional[str] = None,
        content_type: Optional[str] = None,
    ) -> list[dict]:
        """Semantic search over transcript chunks.

        Like ES: POST /transcripts/_search { "knn": { "query_vector": ... } }

        Args:
            query: Natural language search query.
            n_results: Max results to return (like ES 'size').
            channel: Filter by channel (like ES 'filter' clause).
            content_type: Filter by content type.

        Returns:
            List of {text, video_id, title, channel, content_type, score}.
        """
        self._ensure_initialized()

        where_filter = {}
        if channel:
            where_filter["channel"] = channel
        if content_type:
            where_filter["content_type"] = content_type

        results = self._transcript_collection.query(
            query_texts=[query],
            n_results=n_results,
            where=where_filter if where_filter else None,
        )

        return self._format_results(results)

    def search_summaries(
        self,
        query: str,
        n_results: int = 5,
        channel: Optional[str] = None,
        language: Optional[str] = None,
    ) -> list[dict]:
        """Semantic search over video summaries.

        Like ES: POST /summaries/_search { "knn": { ... } }
        """
        self._ensure_initialized()

        where_filter = {}
        if channel:
            where_filter["channel"] = channel
        if language:
            where_filter["language"] = language

        results = self._summary_collection.query(
            query_texts=[query],
            n_results=n_results,
            where=where_filter if where_filter else None,
        )

        return self._format_results(results)

    def get_channel_history(
        self,
        channel: str,
        n_results: int = 10,
    ) -> list[dict]:
        """Get recent summaries from a specific channel.

        Like ES: POST /summaries/_search { "query": { "term": { "channel": "..." } } }
        """
        self._ensure_initialized()

        results = self._summary_collection.get(
            where={"channel": channel},
            limit=n_results,
        )

        if not results or not results["ids"]:
            return []

        items = []
        for i, doc_id in enumerate(results["ids"]):
            meta = results["metadatas"][i] if results["metadatas"] else {}
            items.append({
                "text": results["documents"][i] if results["documents"] else "",
                "video_id": meta.get("video_id", ""),
                "title": meta.get("title", ""),
                "channel": meta.get("channel", ""),
                "content_type": meta.get("content_type", ""),
                "published_at": meta.get("published_at", ""),
            })

        return items

    def get_context_for_video(
        self,
        title: str,
        channel: str,
        content_type: str,
        n_results: int = 3,
    ) -> str:
        """Build a context string from previous related videos.

        This is the key RAG function — given a new video about to be summarized,
        find relevant past content to enrich the summary prompt.

        Like building an ES "more like this" query + aggregating results.
        """
        self._ensure_initialized()

        # Search for related past summaries from the same channel
        same_channel = self.search_summaries(
            query=title,
            n_results=n_results,
            channel=channel,
        )

        # Search for related content across all channels (same topic)
        cross_channel = self.search_summaries(
            query=title,
            n_results=n_results,
        )

        # Deduplicate
        seen_ids = set()
        all_results = []
        for r in same_channel + cross_channel:
            if r["video_id"] not in seen_ids:
                seen_ids.add(r["video_id"])
                all_results.append(r)

        if not all_results:
            return ""

        # Build context string for the LLM prompt
        context_parts = [
            "CONTEXT FROM PREVIOUS VIDEOS (use this to provide continuity and comparison):"
        ]
        for r in all_results[:5]:
            context_parts.append(
                f"\n--- Previous video: \"{r['title']}\" by @{r['channel']} "
                f"({r.get('published_at', 'unknown date')}) ---\n"
                f"{r['text'][:500]}..."
            )

        return "\n".join(context_parts)

    # ── Stats (like ES _cat/indices) ──────────────────────────────────────────

    def get_stats(self) -> dict:
        """Return store statistics."""
        self._ensure_initialized()

        return {
            "transcript_chunks": self._transcript_collection.count(),
            "summaries": self._summary_collection.count(),
            "persist_dir": self._persist_dir,
        }

    # ── Internal ──────────────────────────────────────────────────────────────

    def _format_results(self, results: dict) -> list[dict]:
        """Format ChromaDB query results into a clean list."""
        if not results or not results.get("ids") or not results["ids"][0]:
            return []

        items = []
        for i, doc_id in enumerate(results["ids"][0]):
            meta = results["metadatas"][0][i] if results["metadatas"] else {}
            distance = results["distances"][0][i] if results.get("distances") else 0
            items.append({
                "text": results["documents"][0][i] if results["documents"] else "",
                "video_id": meta.get("video_id", ""),
                "title": meta.get("title", ""),
                "channel": meta.get("channel", ""),
                "content_type": meta.get("content_type", ""),
                "published_at": meta.get("published_at", ""),
                "score": 1 - distance,  # Convert distance to similarity score
            })

        return items


# ── Module-level singleton ────────────────────────────────────────────────────
# Like a shared ES client — one instance for the whole app.

_store: Optional[VideoRAGStore] = None


def get_store() -> VideoRAGStore:
    """Get or create the global RAG store instance."""
    global _store
    if _store is None:
        _store = VideoRAGStore()
    return _store
