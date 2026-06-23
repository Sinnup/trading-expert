"""Stock price data fetcher using yfinance."""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import yfinance as yf
from .base import PriceData

logger = logging.getLogger(__name__)


class PriceFetcher:
    """Fetches current and historical price data from Yahoo Finance."""

    def __init__(self):
        self._cache: dict[str, PriceData] = {}

    async def get_current(self, ticker: str) -> Optional[PriceData]:
        """Get latest price data for a single ticker."""
        try:
            stock = yf.Ticker(ticker)
            info = stock.info

            # Get latest price from fast_info (1d data)
            hist = stock.history(period="2d")
            if hist.empty:
                logger.warning(f"No price data for {ticker}")
                return None

            latest = hist.iloc[-1]
            prev = hist.iloc[-2] if len(hist) > 1 else latest
            change_pct = ((latest["Close"] - prev["Close"]) / prev["Close"]) * 100

            price = PriceData(
                ticker=ticker,
                date=latest.name.to_pydatetime() if hasattr(latest.name, "to_pydatetime") else datetime.now(timezone.utc),
                open=float(latest["Open"]),
                high=float(latest["High"]),
                low=float(latest["Low"]),
                close=float(latest["Close"]),
                volume=int(latest["Volume"]),
                pe_ratio=info.get("trailingPE"),
                market_cap=info.get("marketCap"),
                change_pct=round(change_pct, 2),
            )
            self._cache[ticker] = price
            return price

        except Exception as e:
            logger.error(f"Failed to fetch price for {ticker}: {e}")
            return None

    async def get_batch(self, tickers: list[str]) -> dict[str, Optional[PriceData]]:
        """Get price data for multiple tickers."""
        results: dict[str, Optional[PriceData]] = {}
        for ticker in tickers:
            results[ticker] = await self.get_current(ticker)
        return results

    async def get_historical(
        self, ticker: str, days: int = 30
    ) -> list[PriceData]:
        """Get historical price data for a ticker."""
        prices: list[PriceData] = []
        try:
            stock = yf.Ticker(ticker)
            hist = stock.history(period=f"{days}d")

            for idx, row in hist.iterrows():
                date = idx.to_pydatetime() if hasattr(idx, "to_pydatetime") else datetime.now(timezone.utc)
                prices.append(PriceData(
                    ticker=ticker,
                    date=date,
                    open=float(row["Open"]),
                    high=float(row["High"]),
                    low=float(row["Low"]),
                    close=float(row["Close"]),
                    volume=int(row["Volume"]),
                ))
        except Exception as e:
            logger.error(f"Failed to fetch history for {ticker}: {e}")

        return prices

    def get_cached(self, ticker: str) -> Optional[PriceData]:
        """Get price from in-memory cache."""
        return self._cache.get(ticker)
