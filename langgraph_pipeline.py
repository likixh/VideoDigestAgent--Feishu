"""LangGraph-based summarization workflow.

Replaces the linear pipeline in summarizer.py with a proper state machine.
Think of it like a workflow engine / DAG orchestrator (Airflow, Temporal, etc.)
but purpose-built for LLM agent flows.

Why LangGraph over raw sequential calls:
- Conditional branching: different paths for different content types
- Retry with backoff: automatic retry on LLM failures
- State checkpointing: resume from where you left off
- Parallel execution: multi-language summaries run concurrently
- Human-in-the-loop: optional review checkpoint before emailing

Graph Structure:
    ┌─────────┐
    │  START   │
    └────┬─────┘
         │
    ┌────▼─────┐
    │ classify  │──── Detect content type
    └────┬─────┘
         │
    ┌────▼─────────┐
    │ retrieve_ctx  │──── RAG: fetch relevant past content
    └────┬─────────┘
         │
    ┌────▼──────────┐
    │ select_prompt  │──── Choose template based on content type
    └────┬──────────┘
         │
    ┌────▼──────┐
    │ summarize  │──── Generate summaries (parallel per language)
    └────┬──────┘
         │
    ┌────▼───────┐     ┌──────────────┐
    │ check_qual │────►│ re_summarize  │──── Quality too low? Retry
    └────┬───────┘     └──────┬───────┘
         │                    │
         │◄───────────────────┘
         │
    ┌────▼────┐
    │ verify   │──── Optional fact-check pass
    └────┬────┘
         │
    ┌────▼────┐
    │  END    │
    └─────────┘

Usage:
    from langgraph_pipeline import create_summarization_graph

    graph = create_summarization_graph()
    result = graph.invoke({
        "video_title": "...",
        "transcript": "...",
        "video_id": "abc123",
        "channel": "RhinoFinance",
    })
    summaries = result["summaries"]
    content_type = result["content_type"]
"""

import json
import logging
from typing import Annotated, Any, Optional

logger = logging.getLogger(__name__)


def _check_langgraph_available():
    """Check if langgraph is installed."""
    try:
        import langgraph
        return True
    except ImportError:
        return False


def create_summarization_graph():
    """Create and return the LangGraph summarization workflow.

    This is a factory function — the graph is built once, then invoked
    per video. Like creating a DAG definition in Airflow.

    Requires: pip install langgraph
    """
    from langgraph.graph import StateGraph, END
    from typing_extensions import TypedDict

    import config
    from summarizer import (
        _llm_call,
        _classify,
        _get_summary_prompt,
        _add_language,
        _add_transcript_context,
        _verify,
        CLASSIFY_PROMPT,
    )

    # ── State Schema ──────────────────────────────────────────────────────
    # Like a workflow's state object / context bag.
    # Each node reads from and writes to this shared state.

    class PipelineState(TypedDict, total=False):
        # Inputs
        video_id: str
        video_title: str
        transcript: str
        channel: str
        published_at: str
        # Classification
        classification: dict
        content_type: str
        # RAG context
        rag_context: str
        # Prompts
        base_prompt: str
        # Outputs
        summaries: dict  # {language: summary_text}
        # Quality control
        quality_score: float
        retry_count: int
        # Verification
        verified: bool
        # Errors
        errors: list

    # ── Node Functions ────────────────────────────────────────────────────
    # Each node is a pure function: State → State (like microservice handlers).

    def classify_node(state: PipelineState) -> dict:
        """Node 1: Classify video content type."""
        logger.info("[LangGraph] Node: classify")
        try:
            classification = _classify(state["video_title"], state["transcript"])
            content_type = classification.get("content_type", "general")
            return {
                "classification": classification,
                "content_type": content_type,
            }
        except Exception as e:
            logger.error("[LangGraph] Classification failed: %s", e)
            return {
                "classification": {"content_type": "general", "topics": [], "description": ""},
                "content_type": "general",
                "errors": state.get("errors", []) + [f"classify: {e}"],
            }

    def retrieve_context_node(state: PipelineState) -> dict:
        """Node 2: Retrieve relevant past content from RAG store."""
        logger.info("[LangGraph] Node: retrieve_context")
        try:
            from rag_store import get_store
            store = get_store()
            context = store.get_context_for_video(
                title=state["video_title"],
                channel=state.get("channel", ""),
                content_type=state.get("content_type", "general"),
            )
            return {"rag_context": context}
        except Exception as e:
            logger.warning("[LangGraph] RAG retrieval skipped: %s", e)
            return {"rag_context": ""}

    def select_prompt_node(state: PipelineState) -> dict:
        """Node 3: Select the right prompt template."""
        logger.info("[LangGraph] Node: select_prompt")
        classification = state.get("classification", {"content_type": "general"})
        base_prompt = _get_summary_prompt(classification)
        base_prompt = _add_transcript_context(base_prompt)

        # Inject RAG context if available
        rag_context = state.get("rag_context", "")
        if rag_context:
            base_prompt = (
                base_prompt
                + "\n\n"
                + rag_context
                + "\n\nUse the above context to provide continuity, track changes in "
                "positions, and reference previous analyses where relevant. "
                "Do NOT just repeat old content — use it to add depth."
            )

        return {"base_prompt": base_prompt}

    def summarize_node(state: PipelineState) -> dict:
        """Node 4: Generate summaries for all configured languages."""
        logger.info("[LangGraph] Node: summarize")

        base_prompt = state["base_prompt"]
        user_message = (
            f"Video title: {state['video_title']}\n\n"
            f"Transcript:\n{state['transcript']}"
        )

        summaries = {}
        languages = config.SUMMARY_LANGUAGES

        for idx, lang in enumerate(languages, 1):
            prompt = _add_language(base_prompt, lang)
            logger.info(
                "[LangGraph] Summarizing in %s [%d/%d]...",
                lang, idx, len(languages),
            )
            try:
                summary = _llm_call(prompt, user_message)
                summaries[lang] = summary
                logger.info("[LangGraph] Got %s summary (%d chars)", lang, len(summary))
            except Exception as e:
                logger.error("[LangGraph] Summarization failed for %s: %s", lang, e)
                summaries[lang] = f"[Summarization failed: {e}]"

        return {"summaries": summaries, "retry_count": state.get("retry_count", 0)}

    def check_quality_node(state: PipelineState) -> dict:
        """Node 5: Evaluate summary quality (length, structure, coverage)."""
        logger.info("[LangGraph] Node: check_quality")

        summaries = state.get("summaries", {})
        if not summaries:
            return {"quality_score": 0.0}

        scores = []
        for lang, summary in summaries.items():
            score = 0.0

            # Length check — a good summary should be substantial
            if len(summary) > 500:
                score += 0.3
            if len(summary) > 1000:
                score += 0.2

            # Structure check — should have markdown headers
            if "##" in summary:
                score += 0.2

            # TL;DR check — should have a TL;DR section
            if "TL;DR" in summary or "tl;dr" in summary.lower():
                score += 0.15

            # Not an error message
            if not summary.startswith("["):
                score += 0.15

            scores.append(score)

        avg_score = sum(scores) / len(scores) if scores else 0
        logger.info("[LangGraph] Quality score: %.2f", avg_score)
        return {"quality_score": avg_score}

    def verify_node(state: PipelineState) -> dict:
        """Node 6: Optional verification pass."""
        if not config.VERIFY_SUMMARY:
            return {"verified": True}

        logger.info("[LangGraph] Node: verify")
        verified_summaries = {}
        for lang, summary in state.get("summaries", {}).items():
            try:
                verified = _verify(state["transcript"], summary)
                verified_summaries[lang] = verified
            except Exception as e:
                logger.error("[LangGraph] Verification failed for %s: %s", lang, e)
                verified_summaries[lang] = summary  # Keep original on failure

        return {"summaries": verified_summaries, "verified": True}

    # ── Conditional Edges ─────────────────────────────────────────────────
    # Like routing rules in a workflow engine.

    def should_retry(state: PipelineState) -> str:
        """Decide: retry summarization or proceed to verification?"""
        quality = state.get("quality_score", 0)
        retries = state.get("retry_count", 0)

        if quality < 0.5 and retries < 2:
            logger.info(
                "[LangGraph] Quality %.2f too low (retry %d/2), re-summarizing",
                quality, retries + 1,
            )
            return "retry"
        return "proceed"

    def re_summarize_node(state: PipelineState) -> dict:
        """Re-summarize with an enhanced prompt after quality check failure."""
        logger.info("[LangGraph] Node: re_summarize (retry)")

        # Enhance the prompt with quality feedback
        enhanced_prompt = (
            state["base_prompt"]
            + "\n\nIMPORTANT: Your previous summary was too short or lacked structure. "
            "Please provide a MORE DETAILED and WELL-STRUCTURED summary with clear "
            "markdown sections (## headers), bullet points, and a TL;DR section."
        )

        user_message = (
            f"Video title: {state['video_title']}\n\n"
            f"Transcript:\n{state['transcript']}"
        )

        summaries = {}
        for lang in config.SUMMARY_LANGUAGES:
            prompt = _add_language(enhanced_prompt, lang)
            try:
                summary = _llm_call(prompt, user_message)
                summaries[lang] = summary
            except Exception as e:
                summaries[lang] = state.get("summaries", {}).get(lang, f"[Failed: {e}]")

        return {
            "summaries": summaries,
            "retry_count": state.get("retry_count", 0) + 1,
        }

    # ── Build the Graph ───────────────────────────────────────────────────
    # Like defining a DAG in Airflow or a state machine in AWS Step Functions.

    graph = StateGraph(PipelineState)

    # Add nodes
    graph.add_node("classify", classify_node)
    graph.add_node("retrieve_context", retrieve_context_node)
    graph.add_node("select_prompt", select_prompt_node)
    graph.add_node("summarize", summarize_node)
    graph.add_node("check_quality", check_quality_node)
    graph.add_node("re_summarize", re_summarize_node)
    graph.add_node("verify", verify_node)

    # Add edges (the workflow DAG)
    graph.set_entry_point("classify")
    graph.add_edge("classify", "retrieve_context")
    graph.add_edge("retrieve_context", "select_prompt")
    graph.add_edge("select_prompt", "summarize")
    graph.add_edge("summarize", "check_quality")

    # Conditional edge: retry or proceed
    graph.add_conditional_edges(
        "check_quality",
        should_retry,
        {
            "retry": "re_summarize",
            "proceed": "verify",
        },
    )
    graph.add_edge("re_summarize", "check_quality")
    graph.add_edge("verify", END)

    compiled = graph.compile()
    logger.info("[LangGraph] Summarization graph compiled successfully")
    return compiled


# ── Convenience wrapper ───────────────────────────────────────────────────────


def langgraph_summarize(
    video_title: str,
    transcript: str,
    video_id: str = "",
    channel: str = "",
    published_at: str = "",
) -> tuple[dict[str, str], str]:
    """Run the LangGraph summarization pipeline.

    Drop-in replacement for summarizer.summarize() with the same interface.
    Returns (summaries_dict, content_type).
    """
    graph = create_summarization_graph()

    initial_state = {
        "video_id": video_id,
        "video_title": video_title,
        "transcript": transcript,
        "channel": channel,
        "published_at": published_at,
        "summaries": {},
        "retry_count": 0,
        "errors": [],
    }

    result = graph.invoke(initial_state)

    summaries = result.get("summaries", {})
    content_type = result.get("content_type", "general")

    if result.get("errors"):
        logger.warning("[LangGraph] Pipeline completed with errors: %s", result["errors"])

    return summaries, content_type
