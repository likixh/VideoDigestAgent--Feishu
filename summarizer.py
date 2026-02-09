"""Summarize YouTube video transcripts using Google Gemini."""

import logging

import google.generativeai as genai

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


def summarize(video_title: str, transcript: str) -> str:
    """Send the transcript to Gemini and return a structured stock analysis summary."""
    genai.configure(api_key=config.GEMINI_API_KEY)

    model = genai.GenerativeModel(
        model_name="gemini-2.0-flash",
        system_instruction=SYSTEM_PROMPT,
    )

    user_message = (
        f"Video title: {video_title}\n\n"
        f"Transcript:\n{transcript}"
    )

    logger.info("Sending transcript to Gemini for summarization (%d chars)", len(transcript))

    response = model.generate_content(user_message)

    summary = response.text
    logger.info("Received summary (%d chars)", len(summary))
    return summary
