"""Repository classes for querying models with universe-aware filtering.

SignalRepository wraps SQLAlchemy queries for signals and supports
filtering by signal_type to keep semiconductor and BMV universes separate.
"""

from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.orm import Session

from .signal import Signal


class SignalRepository:
    """Query interface for trading signals with optional universe filtering.

    Usage:
        repo = SignalRepository(session)
        bmv_signals = repo.get_recent(days=1, signal_type="intraday_bmv")
        nvda_signals = repo.get_for_ticker("NVDA", limit=5)
    """

    def __init__(self, session: Session):
        self.session = session

    def get_recent(
        self,
        days: int = 1,
        signal_type: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> list[Signal]:
        """Get recent signals, optionally filtered by signal_type.

        Args:
            days: Look back this many days from now.
            signal_type: Filter by signal type (e.g., "intraday", "intraday_bmv").
                         None returns all types.
            limit: Max signals to return. None returns all.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        query = (
            self.session.query(Signal)
            .filter(Signal.created_at >= cutoff)
            .filter(Signal.cascade_source == False)
        )
        if signal_type:
            query = query.filter(Signal.signal_type == signal_type)

        query = query.order_by(Signal.score.desc())
        if limit:
            query = query.limit(limit)
        return query.all()

    def get_for_ticker(
        self,
        ticker: str,
        limit: int = 5,
        signal_type: Optional[str] = None,
    ) -> list[Signal]:
        """Get recent signals for a specific ticker.

        Args:
            ticker: Ticker symbol (e.g., "NVDA", "BIMBOA.MX").
            limit: Max signals to return.
            signal_type: Filter by signal type. None returns all types.
                         Auto-detected from .MX suffix if not provided.
        """
        # Auto-detect BMV universe from ticker suffix
        if signal_type is None and ticker.upper().endswith(".MX"):
            signal_type = "intraday_bmv"

        query = (
            self.session.query(Signal)
            .filter(Signal.ticker == ticker.upper())
        )
        if signal_type:
            query = query.filter(Signal.signal_type == signal_type)

        return query.order_by(Signal.created_at.desc()).limit(limit).all()
