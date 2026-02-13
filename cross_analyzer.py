"""Cross-Video Analysis & Trend Detection.

This is the "data warehouse analytics" layer — like running aggregation
queries across your Elasticsearch indices. It sits on top of the RAG
store and provides higher-level insights:

1. Cross-Channel Comparison
   "What did RhinoFinance, MeetKevin, and StockMoe all say about NVDA this week?"
   → Like an ES aggregation query grouped by channel

2. Sentiment Trend Tracking
   "How has @RhinoFinance's market sentiment changed over the past month?"
   → Like an ES date_histogram + avg aggregation

3. Topic Clustering
   "Which topics are all my channels talking about right now?"
   → Like an ES significant_terms aggregation

4. Contradiction Detection
   "Are any of my channels disagreeing about the same stock?"
   → Like a cross-index join + comparison

Usage:
    from cross_analyzer import CrossVideoAnalyzer

    analyzer = CrossVideoAnalyzer()
    report = analyzer.compare_channels_on_topic("NVDA")
    trends = analyzer.get_sentiment_trends("RhinoFinance")
    digest = analyzer.generate_cross_analysis_report()
"""

import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

import config
from summarizer import _llm_call

logger = logging.getLogger(__name__)


class CrossVideoAnalyzer:
    """Analyze patterns and trends across multiple video summaries.

    Think of this as the analytics / BI layer on top of the RAG data warehouse.
    """

    def __init__(self):
        from rag_store import get_store
        self._store = get_store()

    # ── 1. Cross-Channel Comparison ───────────────────────────────────────

    def compare_channels_on_topic(
        self,
        topic: str,
        channels: Optional[list[str]] = None,
        n_results: int = 3,
    ) -> str:
        """Compare what different channels say about the same topic.

        Like: SELECT channel, summary FROM videos WHERE topic LIKE '%NVDA%'
              GROUP BY channel

        Args:
            topic: The topic to compare (e.g., "NVDA", "interest rates", "Bitcoin")
            channels: Specific channels to compare (default: all configured)
            n_results: Max results per channel

        Returns:
            A formatted comparison report.
        """
        channels = channels or config.YOUTUBE_CHANNELS

        channel_views = {}
        for ch in channels:
            results = self._store.search_summaries(
                query=topic,
                n_results=n_results,
                channel=ch,
            )
            if results:
                channel_views[ch] = results

        if not channel_views:
            return f"No videos found discussing '{topic}' across any channels."

        # Use LLM to synthesize a comparison
        comparison_prompt = (
            "You are a financial analyst comparing views from different YouTube channels "
            "on the same topic. Highlight agreements, disagreements, and unique insights.\n\n"
            "Format your response as:\n"
            "## Cross-Channel Analysis: {topic}\n"
            "### Points of Agreement\n"
            "### Points of Disagreement\n"
            "### Unique Insights by Channel\n"
            "### Synthesis\n"
        )

        context_parts = [f"Topic: {topic}\n"]
        for ch, results in channel_views.items():
            context_parts.append(f"\n=== @{ch} ===")
            for r in results:
                context_parts.append(
                    f"Video: \"{r['title']}\" ({r.get('published_at', 'unknown date')})\n"
                    f"{r['text'][:800]}\n"
                )

        user_msg = "\n".join(context_parts)
        return _llm_call(comparison_prompt, user_msg)

    # ── 2. Sentiment Trend Tracking ───────────────────────────────────────

    def get_sentiment_trends(
        self,
        channel: str,
        n_recent: int = 10,
    ) -> str:
        """Track how a channel's sentiment has changed over time.

        Like: SELECT date, avg(sentiment) FROM videos
              WHERE channel = '...' GROUP BY date ORDER BY date

        Returns a trend analysis report.
        """
        results = self._store.get_channel_history(channel, n_results=n_recent)

        if not results:
            return f"No history found for @{channel}."

        trend_prompt = (
            "You are a sentiment analysis expert. Analyze how this YouTube channel's "
            "views, sentiment, and positions have evolved over their recent videos.\n\n"
            "Look for:\n"
            "- Sentiment shifts (becoming more bullish/bearish over time)\n"
            "- Changed positions (stocks they were bullish on but now aren't)\n"
            "- Consistent themes (topics they keep coming back to)\n"
            "- Prediction accuracy (did their past predictions come true?)\n\n"
            "Format:\n"
            "## Sentiment Trend: @{channel}\n"
            "### Overall Direction\n"
            "### Key Position Changes\n"
            "### Consistent Themes\n"
            "### Notable Prediction Track Record\n"
        )

        context_parts = [f"Channel: @{channel}\n\nRecent videos (oldest to newest):"]
        for r in reversed(results):
            context_parts.append(
                f"\n--- \"{r['title']}\" ({r.get('published_at', '?')}) ---\n"
                f"Type: {r.get('content_type', '?')}\n"
                f"{r['text'][:600]}\n"
            )

        user_msg = "\n".join(context_parts)
        return _llm_call(trend_prompt, user_msg)

    # ── 3. Topic Clustering ───────────────────────────────────────────────

    def get_hot_topics(self, n_results: int = 20) -> str:
        """Identify the most discussed topics across all channels.

        Like: SELECT topic, count(*) FROM videos GROUP BY topic ORDER BY count DESC

        Returns a hot topics report.
        """
        # Search for recent content across common financial/tech topics
        hot_topics_prompt = (
            "You are a trend analyst. Based on the recent video summaries below, "
            "identify the HOTTEST topics that multiple channels are discussing.\n\n"
            "Format:\n"
            "## Hot Topics This Week\n\n"
            "For each topic:\n"
            "### {Topic Name}\n"
            "- **Channels discussing:** @channel1, @channel2\n"
            "- **Consensus view:** What most channels agree on\n"
            "- **Contrarian takes:** Any disagreements\n"
            "- **Key data points:** Specific numbers/facts mentioned\n"
        )

        # Get recent summaries across all channels
        all_summaries = []
        for ch in config.YOUTUBE_CHANNELS:
            results = self._store.get_channel_history(ch, n_results=5)
            all_summaries.extend(results)

        if not all_summaries:
            return "No video history available for topic analysis."

        context_parts = ["Recent videos across all channels:\n"]
        for r in all_summaries:
            context_parts.append(
                f"@{r.get('channel', '?')}: \"{r['title']}\" "
                f"[{r.get('content_type', '?')}]\n"
                f"{r['text'][:400]}\n"
            )

        user_msg = "\n".join(context_parts)
        return _llm_call(hot_topics_prompt, user_msg)

    # ── 4. Contradiction Detection ────────────────────────────────────────

    def find_contradictions(self, topic: str) -> str:
        """Find contradicting views across channels on a topic.

        Like a cross-index join looking for conflicting data.
        """
        results = self._store.search_summaries(query=topic, n_results=10)

        if len(results) < 2:
            return f"Not enough data to find contradictions about '{topic}'."

        contradiction_prompt = (
            "You are a critical analyst. Examine these video summaries about the "
            "same topic and identify any CONTRADICTIONS between different channels.\n\n"
            "Focus on:\n"
            "- Opposing price targets or predictions\n"
            "- Contradicting sentiment (one bullish, one bearish)\n"
            "- Disagreements on facts or data interpretation\n"
            "- Different conclusions from the same evidence\n\n"
            "Format:\n"
            "## Contradiction Analysis: {topic}\n\n"
            "For each contradiction:\n"
            "### {What they disagree on}\n"
            "- **@channel1 says:** ...\n"
            "- **@channel2 says:** ...\n"
            "- **Who has stronger evidence:** ...\n"
        )

        context_parts = [f"Topic: {topic}\n"]
        for r in results:
            context_parts.append(
                f"\n@{r.get('channel', '?')}: \"{r['title']}\"\n"
                f"{r['text'][:600]}\n"
            )

        user_msg = "\n".join(context_parts)
        return _llm_call(contradiction_prompt, user_msg)

    # ── 5. Ask Any Question ───────────────────────────────────────────────

    def ask(self, question: str) -> str:
        """Ask any natural language question about past videos.

        This is the RAG-powered Q&A interface — like a chatbot over
        your video knowledge base.

        Examples:
            "What did RhinoFinance say about TSLA last week?"
            "Which channels are most bullish on crypto right now?"
            "Has anyone mentioned the Fed's next meeting?"
        """
        # Search both transcripts and summaries
        transcript_results = self._store.search_transcripts(query=question, n_results=5)
        summary_results = self._store.search_summaries(query=question, n_results=5)

        if not transcript_results and not summary_results:
            return "I don't have any relevant video content to answer that question."

        qa_prompt = (
            "You are a knowledgeable assistant with access to a database of "
            "YouTube video transcripts and summaries. Answer the user's question "
            "based ONLY on the context provided. If the answer isn't in the "
            "context, say so.\n\n"
            "Cite your sources: mention the video title and channel for each "
            "piece of information you reference."
        )

        context_parts = [f"User question: {question}\n\nRelevant content:\n"]

        if summary_results:
            context_parts.append("=== From Summaries ===")
            for r in summary_results:
                context_parts.append(
                    f"Video: \"{r['title']}\" by @{r.get('channel', '?')}\n"
                    f"{r['text'][:500]}\n"
                )

        if transcript_results:
            context_parts.append("\n=== From Transcripts ===")
            for r in transcript_results:
                context_parts.append(
                    f"Video: \"{r['title']}\" by @{r.get('channel', '?')}\n"
                    f"{r['text'][:500]}\n"
                )

        user_msg = "\n".join(context_parts)
        return _llm_call(qa_prompt, user_msg)

    # ── 6. Generate Full Cross-Analysis Report ────────────────────────────

    def generate_cross_analysis_report(self) -> str:
        """Generate a comprehensive cross-channel analysis report.

        This is the "executive dashboard" — aggregates all analyses
        into a single report.
        """
        sections = []

        # Hot topics
        try:
            sections.append(self.get_hot_topics())
        except Exception as e:
            logger.warning("Hot topics analysis failed: %s", e)

        # Per-channel sentiment trends
        for ch in config.YOUTUBE_CHANNELS[:3]:  # Limit to avoid too many API calls
            try:
                sections.append(self.get_sentiment_trends(ch))
            except Exception as e:
                logger.warning("Sentiment trend for @%s failed: %s", ch, e)

        return "\n\n---\n\n".join(sections) if sections else "No data available for analysis."
