"""Summarize YouTube video transcripts using a configurable LLM provider."""

import logging

import config

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are an expert stock market analyst. You will be given a transcript from a \
YouTube video about stock analysis. Your job is to produce a comprehensive, \
well-structured summary.

Your summary MUST include ALL of the following sections:

## Overall Market Sentiment
A brief overview of the creator's general outlook on the market.

## Stock Tickers Mentioned
A bullet list of every stock ticker/company discussed, formatted as:
- **TICKER (Company Name)** — one-line description of what was said

## Detailed Stock Analysis
For EACH stock discussed in meaningful detail, provide:
### TICKER — Company Name
- **Bull Thesis:** reasons the creator is bullish (if any)
- **Bear Thesis:** reasons the creator is bearish / risks mentioned (if any)
- **Price Target:** any specific price targets, support/resistance levels, or \
valuation metrics mentioned
- **Key Takeaway:** the creator's bottom-line view on this stock

## Other Key Information
Any other important points, macro-economic data, sector trends, catalysts, \
earnings dates, or actionable insights mentioned in the video.

## TL;DR
A 3-5 sentence executive summary of the entire video.

Be thorough — do not skip stocks or details. Use the creator's actual opinions \
and data points, not generic filler.\
"""


def _summarize_gemini(user_message: str) -> str:
    import google.generativeai as genai

    genai.configure(api_key=config.GEMINI_API_KEY)
    model = genai.GenerativeModel(
        model_name=config.GEMINI_MODEL,
        system_instruction=SYSTEM_PROMPT,
    )
    response = model.generate_content(user_message)
    return response.text


def _summarize_openai(user_message: str) -> str:
    from openai import OpenAI

    client = OpenAI(api_key=config.OPENAI_API_KEY)
    response = client.chat.completions.create(
        model=config.OPENAI_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
    )
    return response.choices[0].message.content


def _summarize_anthropic(user_message: str) -> str:
    import anthropic

    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    response = client.messages.create(
        model=config.ANTHROPIC_MODEL,
        max_tokens=4096,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )
    return response.content[0].text


_PROVIDERS = {
    "gemini": _summarize_gemini,
    "openai": _summarize_openai,
    "anthropic": _summarize_anthropic,
}


def summarize(video_title: str, transcript: str) -> str:
    """Send the transcript to the configured LLM and return a structured summary."""
    user_message = (
        f"Video title: {video_title}\n\n"
        f"Transcript:\n{transcript}"
    )

    provider = config.LLM_PROVIDER
    logger.info(
        "Sending transcript to %s for summarization (%d chars)",
        provider, len(transcript),
    )

    summarize_fn = _PROVIDERS[provider]
    summary = summarize_fn(user_message)

    logger.info("Received summary (%d chars)", len(summary))
    return summary
