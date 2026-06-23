"""Abstract base class for all data fetchers."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class RawArticle:
    """Standardized article object regardless of source."""
    title: str
    source: str
    source_tier: int = 2  # 1=Reuters/Bloomberg/WSJ, 2=industry, 3=blog/social
    url: Optional[str] = None
    published_at: Optional[datetime] = None
    text: Optional[str] = None
    article_id: str = ""  # SHA256 hash set in __post_init__

    def __post_init__(self):
        if not self.article_id:
            import hashlib
            raw = f"{self.title}|{self.source}|{self.url or ''}"
            self.article_id = hashlib.sha256(raw.encode()).hexdigest()


@dataclass
class PriceData:
    """Standardized price data object."""
    ticker: str
    date: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int
    pe_ratio: Optional[float] = None
    market_cap: Optional[float] = None
    change_pct: float = 0.0  # Day-over-day change %


class BaseFetcher(ABC):
    """Abstract fetcher with common dedup and ticker matching logic."""

    source_name: str = "base"
    source_tier: int = 2

    @abstractmethod
    async def fetch(
        self, tickers: list[str], since: Optional[datetime] = None
    ) -> list[RawArticle]:
        """Fetch articles from the source. Implement in subclasses."""
        ...

    @staticmethod
    def deduplicate(articles: list[RawArticle]) -> list[RawArticle]:
        """Remove duplicate articles by article_id hash."""
        seen: set[str] = set()
        unique: list[RawArticle] = []
        for article in articles:
            if article.article_id not in seen:
                seen.add(article.article_id)
                unique.append(article)
        return unique

    @staticmethod
    def match_tickers(
        text: str, tickers_map: dict[str, list[str]]
    ) -> list[str]:
        """Find which tracked tickers are mentioned in text.

        Args:
            text: Article text to search.
            tickers_map: {ticker: [name_variants]} mapping.
                Example: {"NVDA": ["NVIDIA", "NVDA", "Nvidia Corporation"]}

        Returns:
            List of matched ticker symbols.
        """
        matched: list[str] = []
        text_lower = text.lower() if text else ""
        for ticker, variants in tickers_map.items():
            for variant in variants:
                if variant.lower() in text_lower:
                    matched.append(ticker)
                    break
        return matched
