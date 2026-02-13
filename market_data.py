"""Market Data Agent — fetch actual stock/crypto prices.

This is the "data engineering" agent. It fetches real market data
from free APIs and caches results in the prediction database.

Data sources:
    yfinance  — Stocks, ETFs, indices (free, no API key, unofficial Yahoo Finance)
    CoinGecko — Crypto prices (free tier, no API key, 30 calls/min)

Architecture analogy: This is a data ingestion microservice.
It reads from external APIs and writes to the price_cache table.

Usage:
    from market_data import MarketDataAgent
    agent = MarketDataAgent()
    price = agent.get_price("NVDA", "2024-01-15")
    agent.update_all_open_predictions()  # Batch update
"""

import logging
import time
from datetime import datetime, timezone, timedelta
from typing import Optional

from prediction_db import get_db

logger = logging.getLogger(__name__)

# ── Ticker-to-CoinGecko ID mapping for common crypto ─────────────────────────
# yfinance handles stocks/ETFs natively, but crypto needs CoinGecko IDs.
CRYPTO_COINGECKO_MAP = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "SOL": "solana",
    "BNB": "binancecoin",
    "XRP": "ripple",
    "ADA": "cardano",
    "AVAX": "avalanche-2",
    "DOT": "polkadot",
    "MATIC": "matic-network",
    "LINK": "chainlink",
    "UNI": "uniswap",
    "ATOM": "cosmos",
    "LTC": "litecoin",
    "NEAR": "near",
    "ARB": "arbitrum",
    "OP": "optimism",
    "DOGE": "dogecoin",
    "SHIB": "shiba-inu",
    "PEPE": "pepe",
}


class MarketDataAgent:
    """Fetches and caches market prices from yfinance and CoinGecko.

    Uses the price_cache table in PredictionDB to avoid redundant API calls.
    """

    def __init__(self):
        self._db = get_db()

    # ── Public API ────────────────────────────────────────────────────────

    def get_price(
        self,
        ticker: str,
        date: str,
        asset_type: str = "stock",
    ) -> Optional[float]:
        """Get the closing price for a ticker on a specific date.

        Checks cache first, fetches from API if not found.

        Args:
            ticker: Stock ticker (NVDA), crypto symbol (BTC), or ETF (SPY).
            date: Date string in YYYY-MM-DD format.
            asset_type: "stock" | "crypto" | "etf" | "commodity"

        Returns:
            Closing price as float, or None if not available.
        """
        ticker = ticker.upper()

        # Check cache first
        cached = self._db.get_cached_price(ticker, date)
        if cached:
            return cached["close"]

        # Fetch from appropriate source
        if asset_type == "crypto" or ticker in CRYPTO_COINGECKO_MAP:
            price = self._fetch_crypto_price(ticker, date)
        else:
            price = self._fetch_stock_price(ticker, date)

        return price

    def get_current_price(
        self,
        ticker: str,
        asset_type: str = "stock",
    ) -> Optional[float]:
        """Get the most recent closing price for a ticker."""
        ticker = ticker.upper()
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        # Try cache first (today or yesterday)
        cached = self._db.get_latest_cached_price(ticker)
        if cached:
            cached_date = cached["date"]
            days_old = (datetime.now(timezone.utc) - datetime.fromisoformat(cached_date + "T00:00:00+00:00")).days
            if days_old <= 1:
                return cached["close"]

        # Fetch fresh
        if asset_type == "crypto" or ticker in CRYPTO_COINGECKO_MAP:
            return self._fetch_crypto_current(ticker)
        else:
            return self._fetch_stock_current(ticker)

    def update_all_open_predictions(self) -> dict:
        """Batch update: fetch current prices for all tickers with open predictions.

        Returns stats about what was fetched.
        """
        tickers = self._db.get_unique_open_tickers()
        if not tickers:
            logger.info("No open predictions to update prices for.")
            return {"updated": 0, "failed": 0}

        logger.info("Updating prices for %d tickers: %s", len(tickers), ", ".join(tickers))

        updated = 0
        failed = 0
        for ticker in tickers:
            # Determine asset type from the prediction
            predictions = self._db.get_predictions_for_ticker(ticker)
            asset_type = predictions[0]["asset_type"] if predictions else "stock"

            try:
                price = self.get_current_price(ticker, asset_type)
                if price is not None:
                    updated += 1
                else:
                    failed += 1
                    logger.warning("Could not fetch price for %s", ticker)
            except Exception as e:
                failed += 1
                logger.error("Error fetching %s: %s", ticker, e)

            # Rate limiting — be nice to free APIs
            time.sleep(0.5)

        logger.info("Price update complete: %d updated, %d failed", updated, failed)
        return {"updated": updated, "failed": failed}

    def backfill_prediction_prices(self) -> int:
        """Backfill price_at_prediction for predictions that don't have it."""
        predictions = self._db.get_open_predictions()
        backfilled = 0

        for pred in predictions:
            if pred["price_at_prediction"] is not None:
                continue

            date = pred["predicted_at"][:10]  # YYYY-MM-DD
            if not date or len(date) < 10:
                continue

            price = self.get_price(pred["ticker"], date, pred["asset_type"])
            if price is not None:
                self._db.update_price_at_prediction(pred["id"], price)
                backfilled += 1
                logger.info(
                    "Backfilled %s price at prediction: $%.2f on %s",
                    pred["ticker"], price, date,
                )

            time.sleep(0.3)

        return backfilled

    # ── yfinance (Stocks, ETFs) ───────────────────────────────────────────

    def _fetch_stock_price(self, ticker: str, date: str) -> Optional[float]:
        """Fetch historical stock price from yfinance and cache it."""
        try:
            import yfinance as yf
        except ImportError:
            logger.error("yfinance not installed. Run: pip install yfinance")
            return None

        try:
            # Fetch a small window around the target date
            target = datetime.fromisoformat(date)
            start = (target - timedelta(days=5)).strftime("%Y-%m-%d")
            end = (target + timedelta(days=2)).strftime("%Y-%m-%d")

            stock = yf.Ticker(ticker)
            hist = stock.history(start=start, end=end)

            if hist.empty:
                logger.warning("No yfinance data for %s around %s", ticker, date)
                return None

            # Find the closest available date
            hist.index = hist.index.tz_localize(None)
            target_dt = datetime.fromisoformat(date)

            # Get the row closest to but not after the target date
            available = hist[hist.index <= target_dt]
            if available.empty:
                available = hist  # Use whatever we have

            row = available.iloc[-1]
            actual_date = available.index[-1].strftime("%Y-%m-%d")

            # Cache all fetched prices
            for idx, r in hist.iterrows():
                self._db.cache_price(
                    ticker=ticker,
                    date=idx.strftime("%Y-%m-%d"),
                    close=float(r["Close"]),
                    open_=float(r["Open"]) if "Open" in r else None,
                    high=float(r["High"]) if "High" in r else None,
                    low=float(r["Low"]) if "Low" in r else None,
                    volume=float(r["Volume"]) if "Volume" in r else None,
                    source="yfinance",
                )

            return float(row["Close"])

        except Exception as e:
            logger.error("yfinance fetch failed for %s: %s", ticker, e)
            return None

    def _fetch_stock_current(self, ticker: str) -> Optional[float]:
        """Fetch the current/latest stock price."""
        try:
            import yfinance as yf
        except ImportError:
            logger.error("yfinance not installed. Run: pip install yfinance")
            return None

        try:
            stock = yf.Ticker(ticker)
            hist = stock.history(period="5d")

            if hist.empty:
                logger.warning("No yfinance data for %s", ticker)
                return None

            row = hist.iloc[-1]
            date = hist.index[-1]
            if hasattr(date, 'tz_localize'):
                date = date.tz_localize(None)
            date_str = date.strftime("%Y-%m-%d")

            # Cache it
            self._db.cache_price(
                ticker=ticker,
                date=date_str,
                close=float(row["Close"]),
                open_=float(row["Open"]) if "Open" in row else None,
                high=float(row["High"]) if "High" in row else None,
                low=float(row["Low"]) if "Low" in row else None,
                volume=float(row["Volume"]) if "Volume" in row else None,
                source="yfinance",
            )

            return float(row["Close"])

        except Exception as e:
            logger.error("yfinance current price failed for %s: %s", ticker, e)
            return None

    # ── CoinGecko (Crypto) ────────────────────────────────────────────────

    def _fetch_crypto_price(self, ticker: str, date: str) -> Optional[float]:
        """Fetch historical crypto price from CoinGecko and cache it."""
        coingecko_id = CRYPTO_COINGECKO_MAP.get(ticker.upper())
        if not coingecko_id:
            logger.warning("Unknown crypto ticker %s — not in CoinGecko map", ticker)
            return None

        try:
            import urllib.request
            import json

            # CoinGecko wants dd-mm-yyyy
            parts = date.split("-")
            cg_date = f"{parts[2]}-{parts[1]}-{parts[0]}"

            url = (
                f"https://api.coingecko.com/api/v3/coins/{coingecko_id}"
                f"/history?date={cg_date}&localization=false"
            )

            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())

            price = data.get("market_data", {}).get("current_price", {}).get("usd")
            if price is None:
                logger.warning("No CoinGecko price for %s on %s", ticker, date)
                return None

            # Cache it
            self._db.cache_price(
                ticker=ticker,
                date=date,
                close=float(price),
                source="coingecko",
            )

            return float(price)

        except Exception as e:
            logger.error("CoinGecko fetch failed for %s: %s", ticker, e)
            return None

    def _fetch_crypto_current(self, ticker: str) -> Optional[float]:
        """Fetch current crypto price from CoinGecko."""
        coingecko_id = CRYPTO_COINGECKO_MAP.get(ticker.upper())
        if not coingecko_id:
            logger.warning("Unknown crypto ticker %s", ticker)
            return None

        try:
            import urllib.request
            import json

            url = (
                f"https://api.coingecko.com/api/v3/simple/price"
                f"?ids={coingecko_id}&vs_currencies=usd"
            )

            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())

            price = data.get(coingecko_id, {}).get("usd")
            if price is None:
                return None

            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            self._db.cache_price(
                ticker=ticker,
                date=today,
                close=float(price),
                source="coingecko",
            )

            return float(price)

        except Exception as e:
            logger.error("CoinGecko current price failed for %s: %s", ticker, e)
            return None
