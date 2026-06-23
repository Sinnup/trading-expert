"""
Prefilter — fast sentiment scoring and keyword detection.

Uses VADER sentiment analysis to quickly score articles before deciding
whether to send them to DeepSeek for deep reasoning. This saves API costs
by filtering out neutral/low-signal articles.

VADER is chosen over FinBERT because:
- No PyTorch dependency (PyTorch has no x86_64 macOS wheels)
- 100x faster — scores hundreds of articles per second
- Good enough for financial text pre-filtering
"""

import logging
from dataclasses import dataclass, field
from typing import Optional

from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

from trading_expert.fetchers.base import RawArticle

logger = logging.getLogger(__name__)


@dataclass
class PrefilterResult:
    """Result of pre-filtering an article."""

    article_id: str
    vader_score: float  # -1.0 to 1.0 (compound score)
    vader_pos: float = 0.0
    vader_neg: float = 0.0
    vader_neu: float = 0.0
    matched_tickers: list[str] = field(default_factory=list)
    keyword_triggers: list[str] = field(default_factory=list)
    is_extreme: bool = False  # |vader_score| > threshold
    source_tier: int = 2
    should_escalate: bool = False  # Send to DeepSeek?


class Prefilter:
    """Fast sentiment pre-filter using VADER + keyword matching.

    Decides which articles are worth sending to DeepSeek for deep analysis.
    """

    def __init__(
        self,
        tickers_map: dict[str, list[str]],
        urgency_keywords: dict[str, list[str]],
        sentiment_threshold: float = 0.6,
    ):
        """
        Args:
            tickers_map: {ticker: [name_variants]} for company matching.
            urgency_keywords: {"critical": [...], "high": [...]} keyword triggers.
            sentiment_threshold: |compound| above this → extreme sentiment.
        """
        self.vader = SentimentIntensityAnalyzer()
        self.tickers_map = tickers_map
        self.urgency_keywords = urgency_keywords
        self.sentiment_threshold = sentiment_threshold

        # Flatten all urgency keywords for fast lookup
        self._all_keywords: dict[str, str] = {}  # keyword → severity
        for severity, keywords in urgency_keywords.items():
            for kw in keywords:
                self._all_keywords[kw.lower()] = severity

    def score(self, article: RawArticle) -> PrefilterResult:
        """Score a single article with VADER + keyword detection."""
        text = article.text or article.title
        text = f"{article.title}. {text}"  # Title carries weight

        # VADER sentiment
        scores = self.vader.polarity_scores(text)

        # Keyword triggers
        keywords = self._extract_keywords(text)

        # Ticker matching
        from trading_expert.fetchers.base import BaseFetcher
        tickers = BaseFetcher.match_tickers(text, self.tickers_map)

        # Is sentiment extreme?
        is_extreme = abs(scores["compound"]) >= self.sentiment_threshold

        # Escalate if: extreme sentiment OR critical keywords OR tier-1 source
        has_critical = any(
            self._all_keywords.get(kw.lower()) == "critical"
            for kw in keywords
        )
        should_escalate = (
            is_extreme
            or has_critical
            or article.source_tier == 1  # Reuters/Bloomberg/WSJ always escalate
            or (bool(keywords) and article.source_tier <= 2)
        )

        return PrefilterResult(
            article_id=article.article_id,
            vader_score=scores["compound"],
            vader_pos=scores["pos"],
            vader_neg=scores["neg"],
            vader_neu=scores["neu"],
            matched_tickers=tickers,
            keyword_triggers=keywords,
            is_extreme=is_extreme,
            source_tier=article.source_tier,
            should_escalate=should_escalate,
        )

    def score_batch(self, articles: list[RawArticle]) -> list[PrefilterResult]:
        """Score multiple articles and return only those that should escalate."""
        results: list[PrefilterResult] = []
        for article in articles:
            result = self.score(article)
            results.append(result)

        escalated = [r for r in results if r.should_escalate]
        total = len(results)
        logger.info(
            f"Prefilter: {total} articles scored, "
            f"{len(escalated)} escalated to DeepSeek "
            f"({len(escalated)/total*100:.0f}%)"
        )
        return results

    def _extract_keywords(self, text: str) -> list[str]:
        """Find urgency keywords in text."""
        text_lower = text.lower()
        found: list[str] = []
        for keyword in self._all_keywords:
            if keyword in text_lower:
                found.append(keyword)
        return found
