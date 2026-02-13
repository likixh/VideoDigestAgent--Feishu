"""CrewAI Multi-Agent Summarization.

Multi-Agent orchestration = microservices architecture but for AI.
Each agent is a specialized microservice with its own role, tools, and LLM.
The Crew is the orchestrator (like Kubernetes / service mesh).

Agents in the crew:
    1. Researcher    — Queries RAG store for historical context (like a data service)
    2. Analyst       — Deep content analysis based on content type (like a domain service)
    3. Writer        — Crafts the structured summary (like a presentation service)
    4. FactChecker   — Verifies accuracy against transcript (like a QA service)

Communication pattern:
    Sequential with context passing (like a saga pattern in microservices):
    Researcher → Analyst → Writer → FactChecker

    Each agent passes its output to the next, building up richer context.

Why CrewAI over raw sequential LLM calls:
    - Role specialization: each agent has a focused system prompt
    - Tool access: agents can use tools (RAG search, web search, etc.)
    - Delegation: agents can ask other agents for help
    - Memory: agents share a working memory across the pipeline
    - Process control: sequential, hierarchical, or consensus modes

Usage:
    from crew_summarizer import crew_summarize
    summaries, content_type = crew_summarize(title, transcript, video_id, channel)
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


def _check_crewai_available() -> bool:
    """Check if crewai is installed."""
    try:
        import crewai
        return True
    except ImportError:
        return False


def crew_summarize(
    video_title: str,
    transcript: str,
    video_id: str = "",
    channel: str = "",
    published_at: str = "",
) -> tuple[dict[str, str], str]:
    """Run the CrewAI multi-agent summarization pipeline.

    Drop-in replacement for summarizer.summarize().
    Returns (summaries_dict, content_type).
    """
    from crewai import Agent, Task, Crew, Process

    import config
    from summarizer import _classify, _get_summary_prompt, _add_language

    # ── Step 1: Classify (we reuse the existing classifier) ───────────────
    classification = _classify(video_title, transcript)
    content_type = classification.get("content_type", "general")
    topics = classification.get("topics", [])

    logger.info("[CrewAI] Content type: %s, Topics: %s", content_type, topics)

    # ── Build the LLM config ─────────────────────────────────────────────
    # CrewAI supports multiple LLM backends — we map from our config.
    llm_config = _build_crewai_llm_config()

    # ── Define Agents (each is a specialized microservice) ────────────────

    researcher = Agent(
        role="Research Analyst",
        goal=(
            f"Find relevant historical context about {', '.join(topics)} "
            f"from previous videos by @{channel} and other channels."
        ),
        backstory=(
            "You are a meticulous research analyst who tracks financial markets, "
            "tech trends, and creator content over time. You have access to a "
            "database of previously summarized videos and can identify relevant "
            "context, trend changes, and contradictions."
        ),
        verbose=True,
        allow_delegation=False,
        **llm_config,
    )

    analyst = Agent(
        role=f"Senior {_role_for_content_type(content_type)} Analyst",
        goal=(
            f"Perform deep analysis of this {content_type.replace('_', ' ')} "
            f"video transcript, identifying key insights, data points, and "
            f"actionable information."
        ),
        backstory=(
            f"You are a world-class {_role_for_content_type(content_type).lower()} "
            f"with 15+ years of experience. You excel at extracting nuanced "
            f"insights that casual viewers miss. You understand the difference "
            f"between noise and signal."
        ),
        verbose=True,
        allow_delegation=False,
        **llm_config,
    )

    writer = Agent(
        role="Technical Writer",
        goal=(
            "Transform the analyst's findings into a clear, well-structured "
            "summary using proper markdown formatting with headers, bullet "
            "points, and a TL;DR section."
        ),
        backstory=(
            "You are an expert technical writer who specializes in translating "
            "complex analyses into readable, actionable summaries. You follow "
            "strict formatting guidelines and never add information that wasn't "
            "in the original analysis."
        ),
        verbose=True,
        allow_delegation=False,
        **llm_config,
    )

    fact_checker = Agent(
        role="Fact Checker & Editor",
        goal=(
            "Verify every claim in the summary against the original transcript. "
            "Flag any hallucinations, misattributions, or missing key points."
        ),
        backstory=(
            "You are a rigorous fact-checker with zero tolerance for inaccuracy. "
            "You cross-reference every claim against source material and ensure "
            "no important information is omitted. You mark corrections with "
            "[CORRECTED] inline."
        ),
        verbose=True,
        allow_delegation=False,
        **llm_config,
    )

    # ── Define Tasks (the work each agent does) ──────────────────────────

    # Task 1: Research context
    research_task = Task(
        description=(
            f"Research historical context for a new video:\n"
            f"Title: {video_title}\n"
            f"Channel: @{channel}\n"
            f"Content Type: {content_type}\n"
            f"Topics: {', '.join(topics)}\n\n"
            f"Provide any relevant context from previous videos that could "
            f"enrich the analysis. Note changes in positions, sentiment shifts, "
            f"or contradictions with previous content.\n\n"
            f"If no historical context is available, state that this appears "
            f"to be the first video on these topics."
        ),
        expected_output=(
            "A research brief with relevant historical context, trend changes, "
            "and comparisons to previous content. Or a note that no prior "
            "context exists."
        ),
        agent=researcher,
    )

    # Task 2: Deep analysis
    template = _get_summary_prompt(classification)
    analysis_task = Task(
        description=(
            f"Analyze the following video transcript using the research context "
            f"provided by the researcher.\n\n"
            f"Video title: {video_title}\n"
            f"Content type: {content_type}\n\n"
            f"Transcript:\n{transcript}\n\n"
            f"Analysis guidelines:\n{template}\n\n"
            f"Focus on extracting ALL key data points, opinions, and actionable "
            f"information. Be thorough — don't miss anything important."
        ),
        expected_output=(
            "A comprehensive analysis covering all key points, data, opinions, "
            "and actionable information from the transcript."
        ),
        agent=analyst,
    )

    # Task 3: Write summary (per language)
    summaries = {}
    for lang in config.SUMMARY_LANGUAGES:
        write_task = Task(
            description=(
                f"Transform the analysis into a polished, well-structured "
                f"summary in {lang}.\n\n"
                f"Requirements:\n"
                f"- Use proper markdown formatting (## headers, bullet points)\n"
                f"- Include ALL key information from the analysis\n"
                f"- End with a TL;DR section (3-5 sentences)\n"
                f"- Keep proper nouns, tickers, and technical terms in original form\n"
                f"- Write all analysis and descriptions in {lang}\n"
                f"- The transcript may have speech-to-text errors — infer correct "
                f"names from context, mark uncertain items with [?]"
            ),
            expected_output=(
                f"A complete, well-formatted markdown summary in {lang} with "
                f"clear sections, data points, and a TL;DR."
            ),
            agent=writer,
        )

        # Task 4: Fact-check (optional but recommended)
        if config.VERIFY_SUMMARY:
            verify_task = Task(
                description=(
                    f"Verify the {lang} summary against the original transcript.\n\n"
                    f"Original transcript:\n{transcript[:3000]}...\n\n"
                    f"Check for:\n"
                    f"1. Hallucinations — claims NOT in the transcript\n"
                    f"2. Missing key points — important info that was skipped\n"
                    f"3. Misattributions — wrong person/source credited\n"
                    f"4. Incorrect numbers — wrong prices, dates, percentages\n\n"
                    f"Output the corrected summary (or confirm accuracy)."
                ),
                expected_output=(
                    "The verified and corrected summary, or confirmation that "
                    "the summary is accurate."
                ),
                agent=fact_checker,
            )
            tasks = [research_task, analysis_task, write_task, verify_task]
        else:
            tasks = [research_task, analysis_task, write_task]

        # ── Assemble and Run the Crew ────────────────────────────────────
        crew = Crew(
            agents=[researcher, analyst, writer] + ([fact_checker] if config.VERIFY_SUMMARY else []),
            tasks=tasks,
            process=Process.sequential,  # Like a saga: each step feeds the next
            verbose=True,
        )

        logger.info("[CrewAI] Running crew for %s summary...", lang)
        result = crew.kickoff()
        summaries[lang] = str(result)
        logger.info("[CrewAI] %s summary complete (%d chars)", lang, len(summaries[lang]))

    return summaries, content_type


# ── Helpers ───────────────────────────────────────────────────────────────────


def _build_crewai_llm_config() -> dict:
    """Build LLM configuration for CrewAI agents.

    CrewAI uses litellm under the hood, so we map our provider config
    to litellm format.
    """
    import config

    provider = config.LLM_PROVIDER

    if provider == "gemini":
        return {"llm": f"gemini/{config.GEMINI_MODEL}"}
    elif provider == "openai":
        return {"llm": f"openai/{config.OPENAI_MODEL}"}
    elif provider == "anthropic":
        return {"llm": f"anthropic/{config.ANTHROPIC_MODEL}"}
    else:
        return {}


def _role_for_content_type(content_type: str) -> str:
    """Map content type to a specialist role name."""
    roles = {
        "stock_analysis": "Stock Market",
        "macro_economics": "Macroeconomic",
        "crypto": "Cryptocurrency",
        "podcast_interview": "Media & Content",
        "tech_review": "Technology",
        "educational": "Education",
        "news": "News & Current Affairs",
        "cooking": "Culinary",
        "fitness": "Health & Fitness",
        "general": "Content",
    }
    return roles.get(content_type, "Content")
