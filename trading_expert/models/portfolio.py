"""SQLAlchemy models for portfolio tracking — paper trades, outcomes, snapshots."""

from datetime import datetime, date
from sqlalchemy import Column, Integer, String, Float, Boolean, JSON, DateTime, Date, ForeignKey
from . import Base


class SignalOutcome(Base):
    """Tracks whether a signal was correct after 7/30/90 days."""
    __tablename__ = "signal_outcomes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    signal_id = Column(Integer, ForeignKey("signals.id"))
    ticker = Column(String, nullable=False, index=True)
    signal_score = Column(Float, nullable=False)
    price_at_signal = Column(Float, nullable=False)
    price_7d = Column(Float, nullable=True)  # NULL until time passes
    price_30d = Column(Float, nullable=True)
    price_90d = Column(Float, nullable=True)
    return_7d_pct = Column(Float, nullable=True)
    return_30d_pct = Column(Float, nullable=True)
    return_90d_pct = Column(Float, nullable=True)
    was_correct = Column(Boolean, nullable=True)  # direction matched?
    outcome_checked_at = Column(DateTime)


class PaperTrade(Base):
    """Virtual trade for paper trading portfolio."""
    __tablename__ = "paper_trades"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ticker = Column(String, nullable=False, index=True)
    action = Column(String, nullable=False)  # "buy" or "sell"
    quantity = Column(Float, nullable=False)
    price = Column(Float, nullable=False)
    signal_id = Column(Integer, ForeignKey("signals.id"))
    executed_at = Column(DateTime, default=datetime.utcnow)
    closed_at = Column(DateTime, nullable=True)  # NULL if still open
    close_price = Column(Float, nullable=True)
    pnl_realized = Column(Float, nullable=True)


class PortfolioSnapshot(Base):
    """Daily portfolio value snapshot."""
    __tablename__ = "portfolio_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(Date, nullable=False, index=True)
    total_value = Column(Float, nullable=False)
    cash = Column(Float, nullable=False)
    holdings = Column(JSON, nullable=False)  # {ticker: {quantity, price, value}}
    created_at = Column(DateTime, default=datetime.utcnow)
