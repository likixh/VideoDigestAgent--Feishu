"""Summarize YouTube video transcripts using an agent-based approach.

Pipeline:
  1. Classify — detect content type from first ~500 words
  2. Prompt — select/generate the right summarization prompt
  3. Summarize — run the main summarization call
  4. Verify (optional) — check summary accuracy against transcript
"""

import json
import logging

import config

logger = logging.getLogger(__name__)

# ── Step 1: Classification ──────────────────────────────────────────────────

CLASSIFY_PROMPT = """\
You are a content classifier. Given the title and the first portion of a \
video transcript, classify the content type and identify key topics.

Respond in EXACTLY this JSON format (no markdown, no extra text):
{
  "content_type": "one of: stock_analysis, macro_economics, crypto, \
tech_review, podcast_interview, educational, news, cooking, fitness, general",
  "topics": ["topic1", "topic2"],
  "description": "one sentence describing what this video is about"
}
"""

# ── Step 2: Prompt library ──────────────────────────────────────────────────

PROMPT_TEMPLATES = {
    "stock_analysis": """\
You are an expert stock market analyst summarizing a video about stock analysis.

The transcript may come from speech-to-text and contain errors — infer correct \
stock ticker names and financial terms from context.

Your summary MUST include:

## Overall Market Sentiment
The creator's outlook. Include: "**Sentiment: X/10 — Label**" \
(Bullish 8-10, Slightly Bullish 6-7, Neutral 5, Slightly Bearish 3-4, Bearish 1-2)

## Stock Tickers Mentioned
- **TICKER (Company Name)** [Actionable / Informational] — one-line description

## Detailed Stock Analysis
For EACH stock discussed in detail:
### TICKER — Company Name
- **Conviction:** High / Medium / Low
- **Bull Thesis:** reasons bullish
- **Bear Thesis:** reasons bearish / risks
- **Price Target:** specific targets, support/resistance, valuation metrics
- **Key Takeaway:** bottom-line view

## Other Key Information
Macro data, sector trends, catalysts, earnings dates, Fed commentary.

## TL;DR
3-5 sentence executive summary.\
""",

    "macro_economics": """\
You are an expert economist summarizing a video about macroeconomics/markets.

Your summary MUST include:

## Market & Economic Outlook
Overall sentiment and direction. Include: "**Outlook: X/10 — Label**" \
(Very Bullish 8-10, Slightly Bullish 6-7, Neutral 5, Slightly Bearish 3-4, Very Bearish 1-2)

## Key Economic Indicators Discussed
- **Indicator** — what was said, latest data points, trend direction

## Central Bank & Policy
Any Fed/ECB/central bank commentary, interest rate expectations, policy shifts.

## Sector & Asset Class Views
Which sectors/asset classes the creator is bullish or bearish on and why.

## Actionable Takeaways
Specific positioning ideas or warnings mentioned.

## TL;DR
3-5 sentence executive summary.\
""",

    "crypto": """\
You are a crypto market analyst summarizing a video about cryptocurrency.

The transcript may contain speech-to-text errors — infer correct token/project \
names from context.

Your summary MUST include:

## Market Sentiment
Overall crypto market outlook. "**Sentiment: X/10 — Label**"

## Tokens/Projects Mentioned
- **TOKEN (Project Name)** [Actionable / Informational] — one-line description

## Detailed Analysis
For each token discussed in depth:
### TOKEN — Project Name
- **Conviction:** High / Medium / Low
- **Bull Case:** why it could go up
- **Bear Case:** risks and concerns
- **Price Levels:** targets, support, resistance
- **Key Takeaway:** bottom-line view

## On-Chain / Technical Signals
Any on-chain data, chart patterns, or technical indicators mentioned.

## TL;DR
3-5 sentence executive summary.\
""",

    "podcast_interview": """\
You are an expert content analyst summarizing a podcast/interview video.

Your summary MUST include:

## Guests & Context
Who is being interviewed, their background, why this conversation matters.

## Key Topics Discussed
Bullet list of major topics covered with one-line descriptions.

## Detailed Discussion Points
For each major topic:
### Topic Name
- **Key Arguments:** main points made
- **Notable Quotes:** direct or paraphrased standout quotes
- **Areas of Agreement/Disagreement:** between host and guest(s)
- **Takeaway:** the most important insight from this segment

## Surprising or Contrarian Views
Anything unexpected or against conventional wisdom.

## TL;DR
3-5 sentence executive summary.\
""",

    "tech_review": """\
You are a tech analyst summarizing a technology review video.

Your summary MUST include:

## Product Overview
What product/technology is being reviewed, key specs and features.

## Pros
- Bullet list of positives mentioned

## Cons
- Bullet list of negatives/issues mentioned

## Comparisons
How it compares to competitors or previous versions mentioned.

## Who Is This For?
The creator's view on the target audience.

## Verdict
The creator's final recommendation and rating (if given).

## TL;DR
3-5 sentence executive summary.\
""",

    "educational": """\
You are an expert educator summarizing an educational/tutorial video.

Your summary MUST include:

## Topic & Prerequisites
What is being taught and what prior knowledge is assumed.

## Core Concepts
For each concept taught:
### Concept Name
- **Explanation:** clear, concise explanation
- **Key Details:** important specifics, formulas, frameworks
- **Examples Given:** examples or analogies the creator used

## Step-by-Step Process
If the video teaches a process, list the steps in order.

## Common Mistakes / Pitfalls
Warnings or mistakes the creator highlighted.

## Key Takeaways
Bullet list of the most important things to remember.

## TL;DR
3-5 sentence summary of what was taught.\
""",

    "news": """\
You are a journalist summarizing a news/current events video.

Your summary MUST include:

## Headline Summary
One paragraph overview of the main story/stories.

## Key Facts
- Bullet list of the most important facts and data points, in chronological order

## Perspectives Presented
Different viewpoints or sides of the story as presented by the creator.

## Implications
What this means going forward — consequences, next steps, what to watch.

## TL;DR
3-5 sentence executive summary.\
""",
}

GENERAL_PROMPT = """\
You are an expert content analyst. Summarize this video comprehensively.

The transcript may come from speech-to-text and contain errors — infer correct \
names, terms, and references from context.

Structure your summary with clear markdown sections that fit the content. Include:

## Overview
What this video is about, who created it, and the main purpose.

## Key Points
The most important points made, organized by topic with subsections.

## Notable Details
Specific data, quotes, examples, or demonstrations worth highlighting.

## TL;DR
3-5 sentence executive summary.

Be thorough — capture all important information. Use the creator's actual \
statements, not generic filler.\
"""

# ── Step 4: Verification ────────────────────────────────────────────────────

VERIFY_PROMPT = """\
You are a fact-checking editor. You will receive a video transcript and a \
summary of that transcript. Your job is to verify accuracy.

Check for:
1. **Hallucinations** — claims in the summary NOT supported by the transcript
2. **Missing key points** — important information in the transcript that the summary skipped
3. **Misattributions** — opinions or data attributed to the wrong person/source
4. **Incorrect numbers** — wrong prices, percentages, dates, or statistics

If you find issues, output a corrected version of the FULL summary with the \
fixes applied. Mark each correction with [CORRECTED] inline.

If the summary is accurate and comprehensive, output it unchanged with a note \
at the top: "✓ Verified — no corrections needed."
"""

# ── LLM call wrappers ───────────────────────────────────────────────────────


def _gemini_generate(client, model: str, system_prompt: str, user_message: str) -> str:
    """Call a single Gemini model. Raises on quota/rate-limit errors."""
    from google.genai import types

    response = client.models.generate_content(
        model=model,
        contents=user_message,
        config=types.GenerateContentConfig(
            system_instruction=system_prompt,
        ),
    )
    return response.text


def _is_quota_error(exc: Exception) -> bool:
    """Return True if the exception looks like a Gemini quota / rate-limit error."""
    try:
        from google.api_core.exceptions import ResourceExhausted, TooManyRequests
        if isinstance(exc, (ResourceExhausted, TooManyRequests)):
            return True
    except ImportError:
        pass
    # Catch generic ClientError / ServerError whose message mentions quota or rate
    msg = str(exc).lower()
    return any(kw in msg for kw in ("quota", "resource exhausted", "rate limit", "429"))


def _llm_call(system_prompt: str, user_message: str) -> str:
    """Generic LLM call using the configured provider."""
    provider = config.LLM_PROVIDER

    if provider == "gemini":
        from google import genai

        client = genai.Client(api_key=config.GEMINI_API_KEY)
        models_to_try = [config.GEMINI_MODEL] + getattr(
            config, "GEMINI_FALLBACK_MODELS", []
        )

        last_err: Exception | None = None
        for model in models_to_try:
            try:
                result = _gemini_generate(client, model, system_prompt, user_message)
                if model != config.GEMINI_MODEL:
                    logger.info("Gemini fallback succeeded with model: %s", model)
                return result
            except Exception as exc:
                if _is_quota_error(exc) and model != models_to_try[-1]:
                    logger.warning(
                        "Model %s hit quota limit: %s — trying next fallback",
                        model, exc,
                    )
                    last_err = exc
                    continue
                raise

        # All models exhausted (shouldn't normally reach here, but just in case)
        raise last_err  # type: ignore[misc]

    elif provider == "openai":
        from openai import OpenAI

        client = OpenAI(api_key=config.OPENAI_API_KEY)
        response = client.chat.completions.create(
            model=config.OPENAI_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
        )
        return response.choices[0].message.content

    elif provider == "anthropic":
        import anthropic

        client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
        response = client.messages.create(
            model=config.ANTHROPIC_MODEL,
            max_tokens=4096,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
        )
        return response.content[0].text

    elif provider == "openrouter":
        from openai import OpenAI

        client = OpenAI(
            api_key=config.OPENROUTER_API_KEY,
            base_url="https://openrouter.ai/api/v1",
        )
        try:
            response = client.chat.completions.create(
                model=config.OPENROUTER_MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
            )
            return response.choices[0].message.content
        except Exception as exc:
            logger.error("OpenRouter API call failed: %s", exc)
            raise


# ── Agent pipeline ───────────────────────────────────────────────────────────


def _classify(title: str, transcript: str) -> dict:
    """Step 1: Classify video content type."""
    # Use first ~500 words for classification (cheap & fast)
    words = transcript.split()
    preview = " ".join(words[:500])

    user_msg = f"Video title: {title}\n\nTranscript preview:\n{preview}"

    logger.info("Step 1: Classifying content type...")
    raw = _llm_call(CLASSIFY_PROMPT, user_msg)

    # Parse JSON from response (handle markdown code blocks)
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1].rsplit("```", 1)[0]

    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Classification parse failed, defaulting to 'general'")
        result = {
            "content_type": "general",
            "topics": [],
            "description": title,
        }

    logger.info(
        "Classified as: %s (topics: %s)",
        result.get("content_type", "general"),
        ", ".join(result.get("topics", [])),
    )
    return result


def _get_summary_prompt(classification: dict) -> str:
    """Step 2: Select the right prompt based on content type."""
    content_type = classification.get("content_type", "general")
    prompt = PROMPT_TEMPLATES.get(content_type, GENERAL_PROMPT)
    logger.info("Step 2: Using '%s' prompt template", content_type)
    return prompt


def _add_language(prompt: str, language: str) -> str:
    """Add language instruction to a prompt."""
    return (
        prompt
        + f"\n\nIMPORTANT: Write the ENTIRE summary in {language}. "
        f"Keep proper nouns, tickers, and technical terms in their original form "
        f"but write all analysis, headings, and descriptions in {language}."
    )


def _add_transcript_context(prompt: str) -> str:
    """Add speech-to-text awareness to all prompts."""
    return (
        prompt
        + "\n\nNote: This transcript may come from speech-to-text and contain "
        "errors. Infer correct names and terms from context. If you are unsure "
        "about a specific name, number, or term, mark it with [?]."
    )


def _verify(transcript: str, summary: str) -> str:
    """Step 4: Verify summary accuracy against transcript."""
    user_msg = (
        f"TRANSCRIPT:\n{transcript}\n\n"
        f"SUMMARY TO VERIFY:\n{summary}"
    )
    logger.info("Step 4: Verifying summary accuracy...")
    return _llm_call(VERIFY_PROMPT, user_msg)


def summarize(video_title: str, transcript: str) -> tuple[dict[str, str], str]:
    """Agent-based summarization pipeline.

    Returns (summaries_dict, content_type) where summaries_dict is {language: summary}.
    """
    provider = config.LLM_PROVIDER

    # Step 1: Classify
    classification = _classify(video_title, transcript)
    content_type = classification.get("content_type", "general")

    # Step 2: Get the right prompt
    base_prompt = _get_summary_prompt(classification)
    base_prompt = _add_transcript_context(base_prompt)

    user_message = (
        f"Video title: {video_title}\n\n"
        f"Transcript:\n{transcript}"
    )

    summaries = {}
    total_langs = len(config.SUMMARY_LANGUAGES)

    for idx, lang in enumerate(config.SUMMARY_LANGUAGES, 1):
        # Step 3: Summarize
        prompt = _add_language(base_prompt, lang)
        logger.info(
            "Step 3: Summarizing in %s via %s [%d/%d] (%d chars transcript)...",
            lang, provider, idx, total_langs, len(transcript),
        )
        summary = _llm_call(prompt, user_message)
        logger.info("Received %s summary (%d chars)", lang, len(summary))

        # Log a brief preview of the summary
        preview_lines = summary.strip().splitlines()[:3]
        preview = "\n  ".join(preview_lines)
        logger.info("Summary preview (%s):\n  %s", lang, preview)

        # Step 4: Verify (optional)
        if config.VERIFY_SUMMARY:
            logger.info("Step 4: Verifying %s summary accuracy...", lang)
            summary = _verify(transcript, summary)
            logger.info("Verified %s summary (%d chars)", lang, len(summary))

        summaries[lang] = summary

    return summaries, content_type
