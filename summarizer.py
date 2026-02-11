"""Summarize YouTube video transcripts using a configurable LLM provider."""

import logging

import config

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are an expert stock market analyst. You will be given a transcript from a \
YouTube video about stock/finance analysis. The transcript may come from \
speech-to-text and contain errors — infer correct stock ticker names and \
financial terms from context (e.g. "in video" likely means "NVIDIA", \
"tesler" means "TSLA/Tesla").

If the video is not about specific stocks (e.g. macro-only, crypto, general \
finance advice), adapt the sections below accordingly — skip sections that \
don't apply rather than forcing irrelevant content.

Your summary MUST include the following sections (where applicable):

## Overall Market Sentiment
The creator's general outlook on the market. Include a sentiment score: \
Bullish (8-10), Slightly Bullish (6-7), Neutral (5), Slightly Bearish (3-4), \
Bearish (1-2). Format: "**Sentiment: 7/10 — Slightly Bullish**"

## Stock Tickers Mentioned
A bullet list of every stock ticker/company discussed, formatted as:
- **TICKER (Company Name)** [Actionable / Informational] — one-line description
Mark "Actionable" if the creator gives a clear buy/sell/hold signal, \
"Informational" if they're just discussing it.

## Detailed Stock Analysis
For EACH stock discussed in meaningful detail, provide:
### TICKER — Company Name
- **Conviction:** High / Medium / Low (how strongly does the creator feel)
- **Bull Thesis:** reasons the creator is bullish (if any)
- **Bear Thesis:** reasons the creator is bearish / risks mentioned (if any)
- **Price Target:** any specific price targets, support/resistance levels, or \
valuation metrics mentioned
- **Key Takeaway:** the creator's bottom-line view on this stock

## Other Key Information
Any other important points: macro-economic data, sector trends, catalysts, \
earnings dates, Fed/interest rate commentary, or actionable insights.

## TL;DR
A 3-5 sentence executive summary of the entire video.

Be thorough — do not skip stocks or details. Use the creator's actual opinions \
and data points, not generic filler.\
"""


def _build_system_prompt(language: str) -> str:
    lang_instruction = (
        f"\n\nIMPORTANT: Write the ENTIRE summary in {language}. "
        f"Keep stock tickers in their original form (e.g. AAPL, TSLA) "
        f"but write all analysis, headings, and descriptions in {language}."
    )
    return SYSTEM_PROMPT + lang_instruction


def _summarize_gemini(user_message: str, language: str) -> str:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=config.GEMINI_API_KEY)
    response = client.models.generate_content(
        model=config.GEMINI_MODEL,
        contents=user_message,
        config=types.GenerateContentConfig(
            system_instruction=_build_system_prompt(language),
        ),
    )
    return response.text


def _summarize_openai(user_message: str, language: str) -> str:
    from openai import OpenAI

    client = OpenAI(api_key=config.OPENAI_API_KEY)
    response = client.chat.completions.create(
        model=config.OPENAI_MODEL,
        messages=[
            {"role": "system", "content": _build_system_prompt(language)},
            {"role": "user", "content": user_message},
        ],
    )
    return response.choices[0].message.content


def _summarize_anthropic(user_message: str, language: str) -> str:
    import anthropic

    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    response = client.messages.create(
        model=config.ANTHROPIC_MODEL,
        max_tokens=4096,
        system=_build_system_prompt(language),
        messages=[{"role": "user", "content": user_message}],
    )
    return response.content[0].text


_PROVIDERS = {
    "gemini": _summarize_gemini,
    "openai": _summarize_openai,
    "anthropic": _summarize_anthropic,
}


def summarize(video_title: str, transcript: str) -> dict[str, str]:
    """Summarize in each configured language. Returns {language: summary}."""
    user_message = (
        f"Video title: {video_title}\n\n"
        f"Transcript:\n{transcript}"
    )

    provider = config.LLM_PROVIDER
    summarize_fn = _PROVIDERS[provider]
    summaries = {}

    for lang in config.SUMMARY_LANGUAGES:
        logger.info(
            "Sending transcript to %s for %s summary (%d chars)",
            provider, lang, len(transcript),
        )
        summary = summarize_fn(user_message, lang)
        logger.info("Received %s summary (%d chars)", lang, len(summary))
        summaries[lang] = summary

    return summaries
