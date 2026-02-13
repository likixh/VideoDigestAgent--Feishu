"""Prediction Extraction Agent — LLM-powered structured extraction.

This is the hardest part of the prediction tracker. A summary says
things like "NVDA is a strong buy at $120, I see it going to $150"
and we need to turn that into a structured database record.

The extraction prompt is essentially an API contract — it tells the
LLM exactly what JSON schema to return (like writing an OpenAPI spec).

Challenges handled:
- Vague calls ("I like tech here") → low conviction directional bet
- Conditional predictions ("If CPI > 3%...") → stored with condition
- Sector/macro calls ("bonds will sell off") → mapped to ETF proxies
- Deduplication — same ticker in same video = one prediction
- Speech-to-text errors — infer correct ticker from context
"""

import json
import logging
from typing import Optional

from summarizer import _llm_call

logger = logging.getLogger(__name__)

# ── Extraction Prompt ─────────────────────────────────────────────────────────
# This is the "API contract" — the LLM's interface specification.

EXTRACTION_PROMPT = """\
You are a financial prediction extraction system. Given a video summary
about stock analysis, crypto, or macro economics, extract ALL specific
predictions, stock picks, and directional calls made by the creator.

Extract EVERY actionable statement — even vague ones like "I like this stock"
or "I'm cautious on tech." These are still directional bets.

For each prediction, output a JSON object with these fields:

```json
{
  "predictions": [
    {
      "ticker": "NVDA",
      "asset_type": "stock",
      "direction": "bullish",
      "conviction": "high",
      "price_target": 150.00,
      "timeframe": "medium_term",
      "condition": null,
      "verbatim_quote": "I see NVDA going to $150 by Q2"
    }
  ]
}
```

Field specifications:

- **ticker**: The stock ticker, crypto symbol, or ETF. Use standard tickers:
  - Stocks: AAPL, NVDA, TSLA, etc.
  - Crypto: BTC, ETH, SOL, etc.
  - For sector calls, map to the standard ETF:
    - "tech" → QQQ, "bonds/treasuries" → TLT, "S&P/market" → SPY
    - "gold" → GLD, "oil" → USO, "real estate" → VNQ
    - "small caps" → IWM, "emerging markets" → EEM
  - For macro calls about interest rates, inflation, etc. use:
    - "rates going up" → TLT with direction "bearish"
    - "dollar strengthening" → UUP with direction "bullish"

- **asset_type**: "stock" | "crypto" | "etf" | "commodity"

- **direction**: "bullish" | "bearish" | "neutral"
  - "buy", "long", "like", "strong", "undervalued" → bullish
  - "sell", "short", "avoid", "overvalued", "risky" → bearish
  - "hold", "wait", "watching" → neutral

- **conviction**: "high" | "medium" | "low"
  - "strong buy", "love this", "all in", "high conviction" → high
  - "like", "think it could", "looks good" → medium
  - "might", "watching", "could be interesting", "cautious" → low

- **price_target**: Numeric price target if mentioned. null if not stated.
  Only include if the creator gives a specific number.

- **timeframe**: "short_term" | "medium_term" | "long_term"
  - "this week", "next few days", "short term" → short_term (eval at 1 week)
  - "this month", "next quarter", "medium term" → medium_term (eval at 1 month)
  - "this year", "long term", "next few years" → long_term (eval at 3 months)
  - If no timeframe mentioned, default to "medium_term"

- **condition**: Any condition attached to the prediction. null if unconditional.
  Examples: "if CPI comes in hot", "if they beat earnings", "assuming no recession"

- **verbatim_quote**: The closest thing to a direct quote from the summary.
  This is for audit trail — helps verify the extraction was correct.

RULES:
1. Extract ALL predictions, not just the main ones
2. If the same ticker appears multiple times with different directions, include both
3. Do NOT invent predictions that aren't in the summary
4. If a sentiment score is given (e.g., "Sentiment: 8/10"), that's a market-level
   prediction — extract it as SPY/BTC bullish/bearish based on the score
5. Output ONLY valid JSON — no markdown, no commentary

If there are NO extractable predictions, return: {"predictions": []}
"""


# ── Extraction Function ──────────────────────────────────────────────────────

def extract_predictions(
    summary_text: str,
    video_title: str,
    channel: str,
    content_type: str,
) -> list[dict]:
    """Extract structured predictions from a video summary.

    Args:
        summary_text: The full summary text (any language — the LLM handles it).
        video_title: Video title for context.
        channel: Channel handle for attribution.
        content_type: The classified content type (stock_analysis, crypto, etc.)

    Returns:
        List of prediction dicts matching the schema above.
    """
    # Only extract from content types that contain actionable predictions
    extractable_types = {
        "stock_analysis", "macro_economics", "crypto",
        "podcast_interview", "news",
    }
    if content_type not in extractable_types:
        logger.info(
            "Skipping prediction extraction for content type '%s' (not financial)",
            content_type,
        )
        return []

    user_msg = (
        f"Video title: {video_title}\n"
        f"Channel: @{channel}\n"
        f"Content type: {content_type}\n\n"
        f"Summary to extract predictions from:\n\n{summary_text}"
    )

    logger.info("Extracting predictions from @%s: %s", channel, video_title)

    try:
        raw = _llm_call(EXTRACTION_PROMPT, user_msg)
    except Exception as e:
        logger.error("LLM call failed during prediction extraction: %s", e)
        return []

    # Parse JSON response
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1].rsplit("```", 1)[0]

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Failed to parse prediction extraction response as JSON")
        logger.debug("Raw response: %s", raw[:500])
        return []

    predictions = data.get("predictions", [])

    # Validate and clean each prediction
    cleaned = []
    for pred in predictions:
        validated = _validate_prediction(pred)
        if validated:
            cleaned.append(validated)

    logger.info(
        "Extracted %d predictions from @%s: %s",
        len(cleaned), channel,
        ", ".join(f"{p['direction']} {p['ticker']}" for p in cleaned),
    )
    return cleaned


def _validate_prediction(pred: dict) -> Optional[dict]:
    """Validate and normalize a single prediction dict."""
    # Required fields
    ticker = pred.get("ticker", "").strip().upper()
    if not ticker or len(ticker) > 10:
        return None

    direction = pred.get("direction", "").lower()
    if direction not in ("bullish", "bearish", "neutral"):
        return None

    # Normalize optional fields
    asset_type = pred.get("asset_type", "stock").lower()
    if asset_type not in ("stock", "crypto", "etf", "commodity"):
        asset_type = "stock"

    conviction = pred.get("conviction", "medium").lower()
    if conviction not in ("high", "medium", "low"):
        conviction = "medium"

    timeframe = pred.get("timeframe", "medium_term").lower()
    if timeframe not in ("short_term", "medium_term", "long_term"):
        timeframe = "medium_term"

    price_target = pred.get("price_target")
    if price_target is not None:
        try:
            price_target = float(price_target)
            if price_target <= 0:
                price_target = None
        except (ValueError, TypeError):
            price_target = None

    return {
        "ticker": ticker,
        "asset_type": asset_type,
        "direction": direction,
        "conviction": conviction,
        "price_target": price_target,
        "timeframe": timeframe,
        "condition": pred.get("condition"),
        "verbatim_quote": pred.get("verbatim_quote"),
    }


# ── Batch Extraction from History ─────────────────────────────────────────────

def extract_from_saved_summaries() -> list[dict]:
    """Scan saved summary files and extract predictions from each.

    This is for backfilling — run once to extract predictions from
    all previously summarized videos.
    """
    import os
    from history import get_history, SUMMARIES_DIR

    history = get_history()
    all_predictions = []

    for item in history:
        if item.get("status") != "sent":
            continue

        video_id = item.get("video_id", "")
        channel = item.get("channel", "unknown")
        title = item.get("title", "")

        # Try to load the saved summary
        summary_text = _load_summary_file(video_id, title, channel, SUMMARIES_DIR)
        if not summary_text:
            continue

        predictions = extract_predictions(
            summary_text=summary_text,
            video_title=title,
            channel=channel,
            content_type="stock_analysis",  # Assume financial for backfill
        )

        for pred in predictions:
            pred["video_id"] = video_id
            pred["channel"] = channel
            pred["predicted_at"] = item.get("date", "")

        all_predictions.extend(predictions)

    return all_predictions


def _load_summary_file(
    video_id: str, title: str, channel: str, summaries_dir: str
) -> str:
    """Load a saved summary markdown file."""
    import os

    if not os.path.exists(summaries_dir):
        return ""

    safe_title_prefix = "".join(
        c if c.isalnum() or c in " -_" else "" for c in title
    )[:30].strip()

    for filename in os.listdir(summaries_dir):
        if channel in filename and (safe_title_prefix in filename or video_id in filename):
            filepath = os.path.join(summaries_dir, filename)
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    return f.read()
            except Exception:
                continue

    return ""
