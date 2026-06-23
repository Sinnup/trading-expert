"""SQLAlchemy model for fetched news articles."""

from datetime import datetime
from sqlalchemy import Column, String, Integer, Float, Boolean, Text, JSON, DateTime
from . import Base


class Article(Base):
    """Every fetched and processed news article."""
    __tablename__ = "articles"

    id = Column(String, primary_key=True)  # SHA256 of title+source
    title = Column(String, nullable=False)
    source = Column(String, nullable=False)
    source_tier = Column(Integer, default=2)  # 1=Reuters/Bloomberg, 2=industry, 3=social
    url = Column(String)
    published_at = Column(DateTime)
    fetched_at = Column(DateTime, default=datetime.utcnow)
    raw_text = Column(Text)
    matched_tickers = Column(JSON)  # ["NVDA", "TSM"]
    vader_score = Column(Float)  # -1.0 to 1.0
    finbert_score = Column(Float)  # -1.0 to 1.0
    keyword_triggers = Column(JSON)  # ["guidance_raised", "deal_announced"]
    passed_prefilter = Column(Boolean, default=False)
    deepseek_analysis = Column(JSON)  # Full structured response or NULL
    deepseek_model = Column(String)  # "deepseek-chat" or "deepseek-reasoner"
    created_at = Column(DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<Article(id={self.id[:12]}..., tickers={self.matched_tickers})>"
