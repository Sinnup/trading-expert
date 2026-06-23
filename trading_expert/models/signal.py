"""SQLAlchemy model for trading signals."""

from datetime import datetime
from sqlalchemy import Column, Integer, String, Float, Boolean, Text, JSON, DateTime, ForeignKey
from . import Base


class Signal(Base):
    """Every generated buy/sell/hold signal per ticker."""
    __tablename__ = "signals"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ticker = Column(String, nullable=False, index=True)  # "NVDA"
    score = Column(Float, nullable=False)  # -1.0 to +1.0 (combined)
    deepseek_sentiment = Column(Float)  # DeepSeek's sentiment component
    deepseek_confidence = Column(String)  # "high" "medium" "low"
    action = Column(String, nullable=False)  # "buy" "sell" "hold"
    suggested_timeframe = Column(String)  # "3-6 months"
    reasoning = Column(Text)  # Natural-language thesis
    risk_factors = Column(JSON)  # ["Export controls", "Competition"]
    cascade_source = Column(Boolean, default=False)  # True if from another ticker's cascade
    cascade_parent = Column(String)  # Which ticker triggered this
    source_article_ids = Column(JSON)  # ["abc123", "def456"]
    urgency = Column(String)  # "high" "medium" "low"
    signal_type = Column(String, default="intraday")  # "intraday" or "daily_summary"
    price_at_signal = Column(Float)  # Stock price when signal was generated
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    is_active = Column(Boolean, default=True)  # False if superseded

    def __repr__(self):
        return f"<Signal(ticker={self.ticker}, action={self.action}, score={self.score:.2f})>"
