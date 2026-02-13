"""Prediction Tracker Orchestrator — the pipeline that ties everything together.

This is the "main controller" for the prediction tracking subsystem.
It wires together:
    1. Extraction (prediction_extractor.py) — LLM-powered
    2. Market Data (market_data.py) — yfinance + CoinGecko
    3. Scoring (prediction_scorer.py) — deterministic math
    4. Database (prediction_db.py) — SQLite persistence

Architecture analogy: This is the saga orchestrator (like a K8s controller
or Temporal workflow). It runs the pipeline steps in the right order and
handles failures gracefully.

Two modes of operation:
    1. Per-video (called from process_video in main.py after summarization)
    2. Batch (called via CLI for scoring, backfill, reports)
"""

import logging
from typing import Optional

from prediction_db import get_db
from prediction_extractor import extract_predictions
from market_data import MarketDataAgent
from prediction_scorer import PredictionScorer

logger = logging.getLogger(__name__)


# ── Per-Video Pipeline ────────────────────────────────────────────────────────


def track_predictions_for_video(
    video_id: str,
    title: str,
    channel: str,
    content_type: str,
    summaries: dict[str, str],
    predicted_at: str = "",
) -> int:
    """Extract and store predictions from a newly summarized video.

    Called from process_video() in main.py after summarization completes.
    This is the "real-time ingestion" path.

    Returns number of predictions extracted and stored.
    """
    db = get_db()
    market = MarketDataAgent()
    stored_count = 0

    # Use the first (primary) language summary for extraction
    primary_summary = next(iter(summaries.values()), "")
    if not primary_summary:
        return 0

    # Step 1: Extract predictions from summary
    predictions = extract_predictions(
        summary_text=primary_summary,
        video_title=title,
        channel=channel,
        content_type=content_type,
    )

    if not predictions:
        logger.info("No predictions extracted from @%s: %s", channel, title)
        return 0

    # Step 2: For each prediction, fetch the baseline price and store
    for pred in predictions:
        # Try to get the current price as the "price at prediction"
        price_at_pred = None
        try:
            price_at_pred = market.get_current_price(
                pred["ticker"], pred["asset_type"]
            )
        except Exception as e:
            logger.warning("Could not fetch price for %s: %s", pred["ticker"], e)

        # Step 3: Store in database
        row_id = db.insert_prediction(
            video_id=video_id,
            channel=channel,
            ticker=pred["ticker"],
            asset_type=pred["asset_type"],
            direction=pred["direction"],
            conviction=pred["conviction"],
            price_target=pred["price_target"],
            timeframe=pred["timeframe"],
            condition=pred.get("condition"),
            verbatim_quote=pred.get("verbatim_quote"),
            predicted_at=predicted_at or _now_str(),
            price_at_prediction=price_at_pred,
        )

        if row_id is not None:
            stored_count += 1

    logger.info(
        "Tracked %d predictions from @%s: %s",
        stored_count, channel, title,
    )
    return stored_count


# ── Batch Operations ──────────────────────────────────────────────────────────


def run_score_update() -> dict:
    """Run the full scoring pipeline.

    Steps:
        1. Update market prices for all open predictions
        2. Backfill any missing baseline prices
        3. Score all predictions that have enough time elapsed

    Returns combined stats.
    """
    logger.info("Running prediction score update...")

    market = MarketDataAgent()
    scorer = PredictionScorer()

    # Step 1: Update prices
    logger.info("Step 1: Updating market prices...")
    price_stats = market.update_all_open_predictions()

    # Step 2: Backfill baseline prices
    logger.info("Step 2: Backfilling baseline prices...")
    backfilled = market.backfill_prediction_prices()

    # Step 3: Score predictions
    logger.info("Step 3: Scoring predictions...")
    score_stats = scorer.score_all_open()

    stats = {
        "prices_updated": price_stats["updated"],
        "prices_failed": price_stats["failed"],
        "baselines_backfilled": backfilled,
        **score_stats,
    }

    logger.info("Score update complete: %s", stats)
    return stats


def run_backfill_from_history() -> dict:
    """Scan all saved summaries and extract predictions from them.

    This is a one-time backfill operation for existing summaries
    that were created before prediction tracking was enabled.

    Returns stats about what was extracted.
    """
    from prediction_extractor import extract_from_saved_summaries

    logger.info("Backfilling predictions from saved summaries...")

    all_preds = extract_from_saved_summaries()
    if not all_preds:
        logger.info("No predictions found in saved summaries.")
        return {"extracted": 0, "stored": 0}

    db = get_db()
    market = MarketDataAgent()
    stored = 0

    for pred in all_preds:
        # Fetch baseline price
        price_at_pred = None
        date = pred.get("predicted_at", "")[:10]
        if date and len(date) >= 10:
            try:
                price_at_pred = market.get_price(
                    pred["ticker"], date, pred.get("asset_type", "stock")
                )
            except Exception:
                pass

        row_id = db.insert_prediction(
            video_id=pred.get("video_id", ""),
            channel=pred.get("channel", ""),
            ticker=pred["ticker"],
            asset_type=pred.get("asset_type", "stock"),
            direction=pred["direction"],
            conviction=pred.get("conviction", "medium"),
            price_target=pred.get("price_target"),
            timeframe=pred.get("timeframe", "medium_term"),
            condition=pred.get("condition"),
            verbatim_quote=pred.get("verbatim_quote"),
            predicted_at=pred.get("predicted_at", ""),
            price_at_prediction=price_at_pred,
        )

        if row_id is not None:
            stored += 1

    logger.info("Backfill complete: %d extracted, %d stored", len(all_preds), stored)
    return {"extracted": len(all_preds), "stored": stored}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _now_str() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
