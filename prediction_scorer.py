"""Prediction Scoring Engine — compare predictions against actual market data.

This is the deterministic scoring module — no LLM needed here,
just math. It reads predictions + market data from the database
and computes accuracy scores.

Scoring dimensions:
    1. Direction accuracy — did the stock move the way they said?
    2. Target accuracy   — did it hit their price target?
    3. Relative performance — did their pick beat SPY/BTC benchmark?
    4. Composite score   — weighted combination of the above

Eval windows:
    1W  — 1 week after prediction
    1M  — 1 month after prediction
    3M  — 3 months after prediction

Analog: This is a backtesting engine, like what quant funds use
to evaluate trading strategy performance.
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from prediction_db import get_db
from market_data import MarketDataAgent

logger = logging.getLogger(__name__)

# Eval window definitions (name → days)
EVAL_WINDOWS = {
    "1W": 7,
    "1M": 30,
    "3M": 90,
}

# Map prediction timeframes to their "natural" eval window
TIMEFRAME_WINDOW = {
    "short_term": "1W",
    "medium_term": "1M",
    "long_term": "3M",
}

# Benchmark tickers by asset type
BENCHMARK_MAP = {
    "stock": "SPY",
    "etf": "SPY",
    "crypto": "BTC",
    "commodity": "SPY",
}


class PredictionScorer:
    """Score predictions against actual market performance."""

    def __init__(self):
        self._db = get_db()
        self._market = MarketDataAgent()

    def score_all_open(self) -> dict:
        """Score all open predictions that have enough time elapsed.

        This is the main batch job — run it daily or weekly.

        Returns stats: {scored, skipped, errors}
        """
        predictions = self._db.get_open_predictions()
        logger.info("Scoring %d open predictions...", len(predictions))

        stats = {"scored": 0, "skipped": 0, "errors": 0}
        now = datetime.now(timezone.utc)

        for pred in predictions:
            predicted_at = pred.get("predicted_at", "")
            if not predicted_at or len(predicted_at) < 10:
                stats["skipped"] += 1
                continue

            # Parse prediction date
            try:
                pred_date = datetime.fromisoformat(
                    predicted_at[:10] + "T00:00:00+00:00"
                )
            except ValueError:
                stats["skipped"] += 1
                continue

            # Check each eval window
            any_scored = False
            for window_name, window_days in EVAL_WINDOWS.items():
                eval_date = pred_date + timedelta(days=window_days)

                # Only score if enough time has passed
                if now < eval_date:
                    continue

                # Check if already scored for this window
                existing = self._db.get_scores_for_prediction(pred["id"])
                if any(s["eval_window"] == window_name for s in existing):
                    continue

                try:
                    scored = self._score_prediction(pred, window_name, eval_date)
                    if scored:
                        any_scored = True
                        stats["scored"] += 1
                except Exception as e:
                    logger.error(
                        "Error scoring prediction %d (%s): %s",
                        pred["id"], pred["ticker"], e,
                    )
                    stats["errors"] += 1

            # Check if prediction should be resolved (all windows scored or expired)
            natural_window = TIMEFRAME_WINDOW.get(pred["timeframe"], "1M")
            natural_days = EVAL_WINDOWS[natural_window]
            if (now - pred_date).days > natural_days * 2:
                self._db.resolve_prediction(pred["id"], "expired")

        logger.info(
            "Scoring complete: %d scored, %d skipped, %d errors",
            stats["scored"], stats["skipped"], stats["errors"],
        )
        return stats

    def _score_prediction(
        self,
        pred: dict,
        window_name: str,
        eval_date: datetime,
    ) -> bool:
        """Score a single prediction at a specific eval window.

        Returns True if scoring was successful.
        """
        ticker = pred["ticker"]
        asset_type = pred["asset_type"]
        direction = pred["direction"]
        pred_date_str = pred["predicted_at"][:10]
        eval_date_str = eval_date.strftime("%Y-%m-%d")

        # Get price at prediction time
        price_at_pred = pred.get("price_at_prediction")
        if price_at_pred is None:
            price_at_pred = self._market.get_price(ticker, pred_date_str, asset_type)
            if price_at_pred is not None:
                self._db.update_price_at_prediction(pred["id"], price_at_pred)

        if price_at_pred is None:
            logger.warning("Cannot score %s — no price at prediction date %s", ticker, pred_date_str)
            return False

        # Get price at eval date
        actual_price = self._market.get_price(ticker, eval_date_str, asset_type)
        if actual_price is None:
            # Try current price if eval date is recent
            actual_price = self._market.get_current_price(ticker, asset_type)

        if actual_price is None:
            logger.warning("Cannot score %s — no price at eval date %s", ticker, eval_date_str)
            return False

        # Calculate price change
        price_change_pct = ((actual_price - price_at_pred) / price_at_pred) * 100

        # Direction accuracy
        if direction == "bullish":
            direction_correct = price_change_pct > 0
        elif direction == "bearish":
            direction_correct = price_change_pct < 0
        else:
            direction_correct = abs(price_change_pct) < 5  # Neutral = didn't move much

        # Benchmark comparison
        benchmark_ticker = BENCHMARK_MAP.get(asset_type, "SPY")
        benchmark_change_pct = None
        relative_return = None

        if benchmark_ticker != ticker:
            bench_at_pred = self._market.get_price(benchmark_ticker, pred_date_str, "etf")
            bench_at_eval = self._market.get_price(benchmark_ticker, eval_date_str, "etf")
            if bench_at_eval is None:
                bench_at_eval = self._market.get_current_price(benchmark_ticker, "etf")

            if bench_at_pred and bench_at_eval:
                benchmark_change_pct = ((bench_at_eval - bench_at_pred) / bench_at_pred) * 100
                # For bullish picks, relative return = stock - benchmark
                # For bearish picks, relative return = benchmark - stock (we gain when it drops)
                if direction == "bullish":
                    relative_return = price_change_pct - benchmark_change_pct
                elif direction == "bearish":
                    relative_return = -price_change_pct - benchmark_change_pct
                else:
                    relative_return = 0

        # Target accuracy
        target_hit = None
        if pred.get("price_target") is not None:
            target = pred["price_target"]
            if direction == "bullish":
                target_hit = actual_price >= target
            elif direction == "bearish":
                target_hit = actual_price <= target
            else:
                target_hit = abs(actual_price - target) / target < 0.05

        # Composite score
        composite = self._compute_composite_score(
            direction_correct=direction_correct,
            price_change_pct=price_change_pct,
            direction=direction,
            target_hit=target_hit,
            relative_return=relative_return,
            conviction=pred.get("conviction", "medium"),
        )

        # Store the score
        self._db.insert_score(
            prediction_id=pred["id"],
            eval_window=window_name,
            eval_date=eval_date_str,
            actual_price=actual_price,
            price_change_pct=price_change_pct,
            direction_correct=direction_correct,
            composite_score=composite,
            benchmark_ticker=benchmark_ticker,
            benchmark_change_pct=benchmark_change_pct,
            target_hit=target_hit,
            relative_return=relative_return,
        )

        logger.info(
            "Scored %s %s @ %s: %s %.1f%% (composite: %.2f)",
            direction, ticker, window_name,
            "correct" if direction_correct else "WRONG",
            price_change_pct, composite,
        )
        return True

    def _compute_composite_score(
        self,
        direction_correct: bool,
        price_change_pct: float,
        direction: str,
        target_hit: Optional[bool],
        relative_return: Optional[float],
        conviction: str,
    ) -> float:
        """Compute a weighted composite score from 0.0 to 1.0.

        Scoring formula:
            Base: direction correct (0 or 0.5)
            + Target hit bonus (0 or 0.2)
            + Beat benchmark bonus (up to 0.2)
            + Magnitude bonus (up to 0.1)
            × Conviction multiplier
        """
        score = 0.0

        # Base: direction correct (50% of total)
        if direction_correct:
            score += 0.5

        # Target hit (20% of total)
        if target_hit is True:
            score += 0.2
        elif target_hit is None:
            score += 0.1  # No target = partial credit for direction

        # Beat benchmark (20% of total)
        if relative_return is not None:
            if relative_return > 5:
                score += 0.2
            elif relative_return > 0:
                score += 0.1

        # Magnitude — bigger correct moves score higher (10% of total)
        if direction_correct:
            magnitude = abs(price_change_pct)
            if magnitude > 20:
                score += 0.1
            elif magnitude > 10:
                score += 0.07
            elif magnitude > 5:
                score += 0.04

        # Conviction multiplier: high conviction calls matter more
        conviction_multiplier = {
            "high": 1.2,
            "medium": 1.0,
            "low": 0.8,
        }.get(conviction, 1.0)

        # Cap at 1.0
        return min(score * conviction_multiplier, 1.0)


# ── Report Generation ─────────────────────────────────────────────────────────


def generate_scorecard(channel: str, eval_window: str = "1M") -> str:
    """Generate a formatted scorecard for a channel.

    Returns markdown string.
    """
    db = get_db()

    accuracy = db.get_channel_accuracy(channel, eval_window)
    if not accuracy or accuracy.get("total_predictions", 0) == 0:
        return f"No scored predictions found for @{channel} (window: {eval_window})."

    by_conviction = db.get_channel_accuracy_by_conviction(channel, eval_window)
    best, worst = db.get_best_and_worst_calls(channel, eval_window)

    total = accuracy["total_predictions"]
    correct = accuracy["correct_directions"] or 0
    dir_pct = accuracy["direction_accuracy_pct"] or 0
    avg_score = accuracy["avg_composite_score"] or 0
    avg_return = accuracy["avg_return_pct"] or 0
    avg_relative = accuracy["avg_relative_return"]
    targets_hit = accuracy["targets_hit"] or 0
    total_targets = accuracy["total_with_targets"] or 0

    lines = [
        f"## @{channel} — Prediction Scorecard",
        f"**Evaluation window: {eval_window}** | Predictions tracked: {total}\n",
        f"### Overall Accuracy",
        f"- Direction correct: **{correct}/{total} ({dir_pct:.0f}%)**",
        f"- Avg composite score: **{avg_score:.2f}** / 1.00",
        f"- Avg return: **{avg_return:+.1f}%**",
    ]

    if avg_relative is not None:
        lines.append(f"- Avg return vs benchmark: **{avg_relative:+.1f}%**")
    if total_targets > 0:
        lines.append(f"- Price targets hit: **{targets_hit}/{total_targets}**")

    if by_conviction:
        lines.append(f"\n### Accuracy by Conviction")
        for row in by_conviction:
            lines.append(
                f"- **{row['conviction'].title()}** conviction: "
                f"{row['correct']}/{row['total']} ({row['accuracy_pct']:.0f}%)"
            )

    if best:
        lines.append(f"\n### Best Calls")
        for b in best:
            change = b["price_change_pct"]
            effective = change if b["direction"] == "bullish" else -change
            lines.append(
                f"- **{b['ticker']}** ({b['direction']}) — "
                f"{'+'if effective > 0 else ''}{effective:.1f}% "
                f"| {b['predicted_at'][:10]}"
            )

    if worst:
        lines.append(f"\n### Worst Calls")
        for w in worst:
            change = w["price_change_pct"]
            effective = change if w["direction"] == "bullish" else -change
            lines.append(
                f"- **{w['ticker']}** ({w['direction']}) — "
                f"{'+'if effective > 0 else ''}{effective:.1f}% "
                f"| {w['predicted_at'][:10]}"
            )

    return "\n".join(lines)


def generate_leaderboard(eval_window: str = "1M") -> str:
    """Generate a cross-channel leaderboard.

    Returns markdown string.
    """
    db = get_db()
    rows = db.get_leaderboard(eval_window)

    if not rows:
        return "No channels have enough scored predictions for a leaderboard (minimum: 3)."

    lines = [
        f"## Channel Leaderboard ({eval_window})\n",
        f"| Rank | Channel | Predictions | Direction Accuracy | Avg Score | vs Benchmark |",
        f"|------|---------|-------------|-------------------|-----------|-------------|",
    ]

    for i, row in enumerate(rows, 1):
        rel_ret = row["avg_relative_return"]
        rel_str = f"{rel_ret:+.1f}%" if rel_ret is not None else "—"
        lines.append(
            f"| {i} | @{row['channel']} | {row['total_predictions']} "
            f"| {row['direction_accuracy_pct']:.0f}% "
            f"| {row['avg_score']:.2f} "
            f"| {rel_str} |"
        )

    return "\n".join(lines)


def format_predictions_table(predictions: list[dict]) -> str:
    """Format a list of predictions as a readable table."""
    if not predictions:
        return "No predictions found."

    lines = [
        f"| Date | Channel | Ticker | Direction | Conviction | Target | Status |",
        f"|------|---------|--------|-----------|------------|--------|--------|",
    ]

    for p in predictions[:50]:  # Limit display
        date = p.get("predicted_at", "—")[:10]
        target = f"${p['price_target']:.2f}" if p.get("price_target") else "—"
        lines.append(
            f"| {date} | @{p['channel']} | {p['ticker']} "
            f"| {p['direction']} | {p['conviction']} "
            f"| {target} | {p['status']} |"
        )

    total = len(predictions)
    if total > 50:
        lines.append(f"\n*...and {total - 50} more*")

    return "\n".join(lines)
