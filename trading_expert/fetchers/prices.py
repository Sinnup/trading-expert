"""Stock price data fetcher using yfinance."""

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import yfinance as yf
from .base import PriceData

logger = logging.getLogger(__name__)

# Yahoo quotes some exchanges in the minor unit (pence, cents, agorot).
_MINOR_CURRENCIES = {"GBp": "GBP", "GBX": "GBP", "ZAc": "ZAR", "ILA": "ILS"}


@dataclass
class DailyHistory:
    """Daily closes plus split events for one ticker.

    ``closes`` are split-adjusted (Yahoo's convention) but NOT dividend
    adjusted, so returns computed from them are price returns.
    """
    ticker: str
    closes: list[tuple[date, float]] = field(default_factory=list)
    splits: list[tuple[date, float]] = field(default_factory=list)


class PriceFetcher:
    """Fetches current and historical price data from Yahoo Finance."""

    def __init__(self):
        self._cache: dict[str, PriceData] = {}
        self._fx_cache: dict[str, Optional[float]] = {}

    async def get_current(self, ticker: str) -> Optional[PriceData]:
        """Get latest price data for a single ticker.

        ``close`` is the latest bar's close (the live price during the
        session); ``change_pct`` is that close vs the previous session's close.
        """
        try:
            stock = yf.Ticker(ticker)
            # 5d instead of 2d so weekends/holidays still leave a previous session.
            hist = stock.history(period="5d").dropna(subset=["Close"])
            if hist.empty:
                logger.warning(f"No price data for {ticker}")
                return None

            latest = hist.iloc[-1]
            prev_close = float(hist.iloc[-2]["Close"]) if len(hist) > 1 else None
            close = float(latest["Close"])
            change_pct = (
                (close - prev_close) / prev_close * 100
                if prev_close and prev_close > 0 else 0.0
            )
            currency = (stock.history_metadata or {}).get("currency") or "USD"

            # Fundamentals are a separate, slower Yahoo call — never let them
            # sink the price itself.
            try:
                info = stock.info
            except Exception as e:
                logger.debug(f"No fundamentals for {ticker}: {e}")
                info = {}

            volume = latest["Volume"]
            price = PriceData(
                ticker=ticker,
                date=latest.name.to_pydatetime() if hasattr(latest.name, "to_pydatetime") else datetime.now(timezone.utc),
                open=float(latest["Open"]),
                high=float(latest["High"]),
                low=float(latest["Low"]),
                close=close,
                volume=int(volume) if volume == volume else 0,  # NaN-safe
                pe_ratio=info.get("trailingPE"),
                market_cap=info.get("marketCap"),
                change_pct=round(change_pct, 2),
                currency=currency,
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

    async def get_daily_history(self, ticker: str, start: date) -> Optional[DailyHistory]:
        """Daily closes and splits from ``start`` (inclusive) to today.

        Used to grade signal outcomes at fixed horizons: the close on the
        horizon date, not whatever the price happens to be when the grader runs.
        """
        try:
            hist = yf.Ticker(ticker).history(
                start=start.isoformat(),
                end=(date.today() + timedelta(days=1)).isoformat(),
                auto_adjust=False,
                actions=True,
            ).dropna(subset=["Close"])
        except Exception as e:
            logger.error(f"Failed to fetch daily history for {ticker}: {e}")
            return None

        history = DailyHistory(ticker=ticker)
        for idx, row in hist.iterrows():
            session_date = idx.date() if hasattr(idx, "date") else idx
            history.closes.append((session_date, float(row["Close"])))
            ratio = float(row.get("Stock Splits", 0.0) or 0.0)
            if ratio > 0:
                history.splits.append((session_date, ratio))
        return history

    async def usd_rate(self, currency: str) -> Optional[float]:
        """USD value of one unit of ``currency`` (1.0 for USD). Cached per instance."""
        divisor = 1.0
        if currency in _MINOR_CURRENCIES:
            currency, divisor = _MINOR_CURRENCIES[currency], 100.0
        if currency == "USD":
            return 1.0 / divisor
        if currency not in self._fx_cache:
            fx = await self.get_current(f"{currency}USD=X")
            self._fx_cache[currency] = fx.close if fx and fx.close > 0 else None
        rate = self._fx_cache[currency]
        return rate / divisor if rate else None

    async def to_usd(self, price: PriceData) -> Optional[float]:
        """Convert a quote's close to USD (None if the FX rate is unavailable)."""
        rate = await self.usd_rate(price.currency)
        return price.close * rate if rate else None

    def get_cached(self, ticker: str) -> Optional[PriceData]:
        """Get price from in-memory cache."""
        return self._cache.get(ticker)
