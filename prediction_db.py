"""Prediction Database — SQLite storage for stock/crypto predictions.

Think of this as the data model layer (like Django ORM models or
SQLAlchemy schemas). We use raw SQLite because:
- Zero external dependencies
- File-based (like processed_videos.json, but relational)
- ACID transactions (no corrupted JSON)
- SQL queries for aggregations (leaderboard, trends)

Tables:
    predictions  — Every stock/crypto call extracted from video summaries
    price_cache  — Cached price data from yfinance/CoinGecko
    scores       — Computed accuracy scores per prediction per eval window
"""

import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

DB_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "predictions.db"
)


class PredictionDB:
    """SQLite database for prediction tracking.

    Like a mini data warehouse — normalized tables with foreign keys,
    indexed for fast lookups.
    """

    def __init__(self, db_path: str = DB_PATH):
        self._db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self._db_path)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._create_tables()
        return self._conn

    def _create_tables(self) -> None:
        conn = self._conn
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS predictions (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                video_id        TEXT NOT NULL,
                channel         TEXT NOT NULL,
                ticker          TEXT NOT NULL,
                asset_type      TEXT NOT NULL DEFAULT 'stock',
                direction       TEXT NOT NULL,
                conviction      TEXT NOT NULL DEFAULT 'medium',
                price_target    REAL,
                timeframe       TEXT NOT NULL DEFAULT 'medium_term',
                condition       TEXT,
                verbatim_quote  TEXT,
                predicted_at    TEXT NOT NULL,
                price_at_prediction REAL,
                status          TEXT NOT NULL DEFAULT 'open',
                created_at      TEXT NOT NULL DEFAULT (datetime('now')),

                UNIQUE(video_id, ticker, direction)
            );

            CREATE INDEX IF NOT EXISTS idx_predictions_channel
                ON predictions(channel);
            CREATE INDEX IF NOT EXISTS idx_predictions_ticker
                ON predictions(ticker);
            CREATE INDEX IF NOT EXISTS idx_predictions_status
                ON predictions(status);
            CREATE INDEX IF NOT EXISTS idx_predictions_predicted_at
                ON predictions(predicted_at);

            CREATE TABLE IF NOT EXISTS price_cache (
                ticker      TEXT NOT NULL,
                date        TEXT NOT NULL,
                open        REAL,
                high        REAL,
                low         REAL,
                close       REAL NOT NULL,
                volume      REAL,
                source      TEXT NOT NULL DEFAULT 'yfinance',
                fetched_at  TEXT NOT NULL DEFAULT (datetime('now')),

                PRIMARY KEY (ticker, date)
            );

            CREATE TABLE IF NOT EXISTS scores (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                prediction_id   INTEGER NOT NULL REFERENCES predictions(id),
                eval_window     TEXT NOT NULL,
                eval_date       TEXT NOT NULL,
                actual_price    REAL NOT NULL,
                price_change_pct REAL NOT NULL,
                benchmark_ticker TEXT DEFAULT 'SPY',
                benchmark_change_pct REAL,
                direction_correct INTEGER NOT NULL,
                target_hit      INTEGER,
                relative_return REAL,
                composite_score REAL NOT NULL,
                scored_at       TEXT NOT NULL DEFAULT (datetime('now')),

                UNIQUE(prediction_id, eval_window)
            );
        """)
        conn.commit()

    # ── Predictions CRUD ──────────────────────────────────────────────────

    def insert_prediction(
        self,
        video_id: str,
        channel: str,
        ticker: str,
        asset_type: str,
        direction: str,
        conviction: str,
        price_target: Optional[float],
        timeframe: str,
        condition: Optional[str],
        verbatim_quote: Optional[str],
        predicted_at: str,
        price_at_prediction: Optional[float] = None,
    ) -> Optional[int]:
        """Insert a new prediction. Returns the row ID, or None if duplicate."""
        conn = self._get_conn()
        try:
            cursor = conn.execute(
                """
                INSERT INTO predictions
                    (video_id, channel, ticker, asset_type, direction, conviction,
                     price_target, timeframe, condition, verbatim_quote,
                     predicted_at, price_at_prediction)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (video_id, channel, ticker.upper(), asset_type, direction,
                 conviction, price_target, timeframe, condition, verbatim_quote,
                 predicted_at, price_at_prediction),
            )
            conn.commit()
            logger.info(
                "Inserted prediction: %s %s %s (from @%s)",
                direction, ticker, f"→ ${price_target}" if price_target else "",
                channel,
            )
            return cursor.lastrowid
        except sqlite3.IntegrityError:
            logger.debug("Duplicate prediction skipped: %s %s from %s", direction, ticker, video_id)
            return None

    def get_open_predictions(self) -> list[dict]:
        """Get all predictions that haven't been resolved yet."""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM predictions WHERE status = 'open' ORDER BY predicted_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    def get_predictions_for_channel(self, channel: str) -> list[dict]:
        """Get all predictions from a specific channel."""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM predictions WHERE channel = ? ORDER BY predicted_at DESC",
            (channel,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_predictions_for_ticker(self, ticker: str) -> list[dict]:
        """Get all predictions for a specific ticker across all channels."""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM predictions WHERE ticker = ? ORDER BY predicted_at DESC",
            (ticker.upper(),),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_all_predictions(self, limit: int = 100) -> list[dict]:
        """Get recent predictions across all channels."""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM predictions ORDER BY predicted_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def resolve_prediction(self, prediction_id: int, status: str = "resolved") -> None:
        """Mark a prediction as resolved or expired."""
        conn = self._get_conn()
        conn.execute(
            "UPDATE predictions SET status = ? WHERE id = ?",
            (status, prediction_id),
        )
        conn.commit()

    def update_price_at_prediction(self, prediction_id: int, price: float) -> None:
        """Backfill the price at time of prediction."""
        conn = self._get_conn()
        conn.execute(
            "UPDATE predictions SET price_at_prediction = ? WHERE id = ?",
            (price, prediction_id),
        )
        conn.commit()

    # ── Price Cache ───────────────────────────────────────────────────────

    def cache_price(
        self,
        ticker: str,
        date: str,
        close: float,
        open_: Optional[float] = None,
        high: Optional[float] = None,
        low: Optional[float] = None,
        volume: Optional[float] = None,
        source: str = "yfinance",
    ) -> None:
        """Cache a price data point."""
        conn = self._get_conn()
        conn.execute(
            """
            INSERT OR REPLACE INTO price_cache
                (ticker, date, open, high, low, close, volume, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (ticker.upper(), date, open_, high, low, close, volume, source),
        )
        conn.commit()

    def get_cached_price(self, ticker: str, date: str) -> Optional[dict]:
        """Get a cached price for a ticker on a specific date."""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM price_cache WHERE ticker = ? AND date = ?",
            (ticker.upper(), date),
        ).fetchone()
        return dict(row) if row else None

    def get_latest_cached_price(self, ticker: str) -> Optional[dict]:
        """Get the most recent cached price for a ticker."""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM price_cache WHERE ticker = ? ORDER BY date DESC LIMIT 1",
            (ticker.upper(),),
        ).fetchone()
        return dict(row) if row else None

    # ── Scores ────────────────────────────────────────────────────────────

    def insert_score(
        self,
        prediction_id: int,
        eval_window: str,
        eval_date: str,
        actual_price: float,
        price_change_pct: float,
        direction_correct: bool,
        composite_score: float,
        benchmark_ticker: str = "SPY",
        benchmark_change_pct: Optional[float] = None,
        target_hit: Optional[bool] = None,
        relative_return: Optional[float] = None,
    ) -> Optional[int]:
        """Insert a score for a prediction at a specific eval window."""
        conn = self._get_conn()
        try:
            cursor = conn.execute(
                """
                INSERT INTO scores
                    (prediction_id, eval_window, eval_date, actual_price,
                     price_change_pct, benchmark_ticker, benchmark_change_pct,
                     direction_correct, target_hit, relative_return, composite_score)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (prediction_id, eval_window, eval_date, actual_price,
                 price_change_pct, benchmark_ticker, benchmark_change_pct,
                 int(direction_correct), int(target_hit) if target_hit is not None else None,
                 relative_return, composite_score),
            )
            conn.commit()
            return cursor.lastrowid
        except sqlite3.IntegrityError:
            return None

    def get_scores_for_prediction(self, prediction_id: int) -> list[dict]:
        """Get all scores for a specific prediction."""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM scores WHERE prediction_id = ? ORDER BY eval_window",
            (prediction_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_scores_for_channel(self, channel: str) -> list[dict]:
        """Get all scores for a channel's predictions (join)."""
        conn = self._get_conn()
        rows = conn.execute(
            """
            SELECT s.*, p.ticker, p.direction, p.conviction, p.channel,
                   p.price_target, p.predicted_at, p.video_id
            FROM scores s
            JOIN predictions p ON s.prediction_id = p.id
            WHERE p.channel = ?
            ORDER BY p.predicted_at DESC
            """,
            (channel,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ── Aggregation Queries (the "BI layer") ──────────────────────────────

    def get_channel_accuracy(self, channel: str, eval_window: str = "1M") -> dict:
        """Compute overall accuracy metrics for a channel.

        Like: SELECT avg(direction_correct), avg(composite_score)
              FROM scores JOIN predictions WHERE channel = ?
        """
        conn = self._get_conn()
        row = conn.execute(
            """
            SELECT
                COUNT(*) as total_predictions,
                SUM(s.direction_correct) as correct_directions,
                AVG(s.direction_correct) * 100 as direction_accuracy_pct,
                AVG(s.composite_score) as avg_composite_score,
                AVG(s.price_change_pct) as avg_return_pct,
                AVG(s.relative_return) as avg_relative_return,
                SUM(CASE WHEN s.target_hit = 1 THEN 1 ELSE 0 END) as targets_hit,
                SUM(CASE WHEN p.price_target IS NOT NULL THEN 1 ELSE 0 END) as total_with_targets
            FROM scores s
            JOIN predictions p ON s.prediction_id = p.id
            WHERE p.channel = ? AND s.eval_window = ?
            """,
            (channel, eval_window),
        ).fetchone()
        return dict(row) if row else {}

    def get_channel_accuracy_by_conviction(
        self, channel: str, eval_window: str = "1M"
    ) -> list[dict]:
        """Accuracy broken down by conviction level."""
        conn = self._get_conn()
        rows = conn.execute(
            """
            SELECT
                p.conviction,
                COUNT(*) as total,
                SUM(s.direction_correct) as correct,
                AVG(s.direction_correct) * 100 as accuracy_pct,
                AVG(s.composite_score) as avg_score
            FROM scores s
            JOIN predictions p ON s.prediction_id = p.id
            WHERE p.channel = ? AND s.eval_window = ?
            GROUP BY p.conviction
            ORDER BY accuracy_pct DESC
            """,
            (channel, eval_window),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_best_and_worst_calls(
        self, channel: str, eval_window: str = "1M", limit: int = 3
    ) -> tuple[list[dict], list[dict]]:
        """Get the best and worst calls for a channel."""
        conn = self._get_conn()

        best = conn.execute(
            """
            SELECT p.ticker, p.direction, p.conviction, p.predicted_at,
                   s.price_change_pct, s.composite_score, p.price_at_prediction,
                   s.actual_price
            FROM scores s
            JOIN predictions p ON s.prediction_id = p.id
            WHERE p.channel = ? AND s.eval_window = ?
            ORDER BY s.price_change_pct * CASE WHEN p.direction = 'bullish' THEN 1 ELSE -1 END DESC
            LIMIT ?
            """,
            (channel, eval_window, limit),
        ).fetchall()

        worst = conn.execute(
            """
            SELECT p.ticker, p.direction, p.conviction, p.predicted_at,
                   s.price_change_pct, s.composite_score, p.price_at_prediction,
                   s.actual_price
            FROM scores s
            JOIN predictions p ON s.prediction_id = p.id
            WHERE p.channel = ? AND s.eval_window = ?
            ORDER BY s.price_change_pct * CASE WHEN p.direction = 'bullish' THEN 1 ELSE -1 END ASC
            LIMIT ?
            """,
            (channel, eval_window, limit),
        ).fetchall()

        return [dict(r) for r in best], [dict(r) for r in worst]

    def get_leaderboard(self, eval_window: str = "1M") -> list[dict]:
        """Rank all channels by prediction accuracy.

        Like: SELECT channel, avg(accuracy) FROM ... GROUP BY channel ORDER BY avg DESC
        """
        conn = self._get_conn()
        rows = conn.execute(
            """
            SELECT
                p.channel,
                COUNT(*) as total_predictions,
                AVG(s.direction_correct) * 100 as direction_accuracy_pct,
                AVG(s.composite_score) as avg_score,
                AVG(s.price_change_pct) as avg_return_pct,
                AVG(s.relative_return) as avg_relative_return
            FROM scores s
            JOIN predictions p ON s.prediction_id = p.id
            WHERE s.eval_window = ?
            GROUP BY p.channel
            HAVING total_predictions >= 3
            ORDER BY direction_accuracy_pct DESC
            """,
            (eval_window,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_unique_open_tickers(self) -> list[str]:
        """Get all tickers that have open predictions (need price updates)."""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT DISTINCT ticker FROM predictions WHERE status = 'open'"
        ).fetchall()
        return [r["ticker"] for r in rows]

    # ── Stats ─────────────────────────────────────────────────────────────

    def get_stats(self) -> dict:
        conn = self._get_conn()
        total = conn.execute("SELECT COUNT(*) as n FROM predictions").fetchone()["n"]
        open_ = conn.execute("SELECT COUNT(*) as n FROM predictions WHERE status='open'").fetchone()["n"]
        scored = conn.execute("SELECT COUNT(DISTINCT prediction_id) as n FROM scores").fetchone()["n"]
        prices = conn.execute("SELECT COUNT(*) as n FROM price_cache").fetchone()["n"]
        channels = conn.execute("SELECT COUNT(DISTINCT channel) as n FROM predictions").fetchone()["n"]
        tickers = conn.execute("SELECT COUNT(DISTINCT ticker) as n FROM predictions").fetchone()["n"]

        return {
            "total_predictions": total,
            "open_predictions": open_,
            "scored_predictions": scored,
            "cached_prices": prices,
            "channels_tracked": channels,
            "tickers_tracked": tickers,
            "db_path": self._db_path,
        }

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None


# ── Module-level singleton ────────────────────────────────────────────────────

_db: Optional[PredictionDB] = None


def get_db() -> PredictionDB:
    global _db
    if _db is None:
        _db = PredictionDB()
    return _db
