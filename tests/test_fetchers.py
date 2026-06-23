"""Tests for the news fetchers — dedup, ticker matching, RSS parsing."""

import pytest
from datetime import datetime, timedelta, timezone

from trading_expert.fetchers.base import RawArticle, BaseFetcher


class TestRawArticle:
    """Tests for the RawArticle dataclass."""

    def test_article_id_is_deterministic(self):
        """Same title+source+url → same ID every time."""
        a1 = RawArticle(
            title="NVIDIA announces new GPU",
            source="reuters",
            source_tier=1,
            url="https://reuters.com/nvda-gpu",
        )
        a2 = RawArticle(
            title="NVIDIA announces new GPU",
            source="reuters",
            source_tier=1,
            url="https://reuters.com/nvda-gpu",
        )
        assert a1.article_id == a2.article_id
        assert len(a1.article_id) == 64  # SHA256 hex

    def test_article_id_different_for_different_title(self):
        """Different title → different ID."""
        a1 = RawArticle(title="NVIDIA rises", source="reuters")
        a2 = RawArticle(title="NVIDIA falls", source="reuters")
        assert a1.article_id != a2.article_id

    def test_article_id_different_for_different_source(self):
        """Different source → different ID."""
        a1 = RawArticle(title="Same title", source="reuters")
        a2 = RawArticle(title="Same title", source="bloomberg")
        assert a1.article_id != a2.article_id


class TestDeduplicate:
    """Tests for the deduplication logic."""

    def test_removes_duplicates(self):
        """Duplicate articles (same ID) are removed."""
        a1 = RawArticle(title="Test", source="src1")
        a2 = RawArticle(title="Test", source="src1")  # Same as a1
        a3 = RawArticle(title="Test2", source="src1")  # Different

        result = BaseFetcher.deduplicate([a1, a2, a3])
        assert len(result) == 2
        ids = [a.article_id for a in result]
        assert a1.article_id in ids
        assert a3.article_id in ids

    def test_empty_list(self):
        """Empty input → empty output."""
        assert BaseFetcher.deduplicate([]) == []

    def test_all_unique(self):
        """No duplicates → same count returned."""
        articles = [
            RawArticle(title=f"Test {i}", source="src")
            for i in range(10)
        ]
        assert len(BaseFetcher.deduplicate(articles)) == 10


class TestTickerMatching:
    """Tests for ticker name matching."""

    TICKERS_MAP = {
        "NVDA": ["NVIDIA", "NVDA", "Nvidia Corporation"],
        "INTC": ["Intel", "INTC", "Intel Corporation"],
        "TSM": ["TSMC", "TSM", "Taiwan Semiconductor"],
        "AVGO": ["Broadcom", "AVGO"],
        "AAPL": ["Apple", "AAPL"],
    }

    def test_matches_exact_ticker(self):
        """Text containing ticker symbol is matched."""
        text = "NVDA stock rose 5% today on strong earnings."
        matched = BaseFetcher.match_tickers(text, self.TICKERS_MAP)
        assert "NVDA" in matched

    def test_matches_company_name(self):
        """Text containing company name (not ticker) is matched."""
        text = "NVIDIA announced a new partnership with Tesla."
        matched = BaseFetcher.match_tickers(text, self.TICKERS_MAP)
        assert "NVDA" in matched

    def test_matches_multiple_tickers(self):
        """Text mentioning multiple companies matches all."""
        text = "NVIDIA and TSMC signed a deal, while Intel struggles."
        matched = BaseFetcher.match_tickers(text, self.TICKERS_MAP)
        assert "NVDA" in matched
        assert "TSM" in matched
        assert "INTC" in matched

    def test_no_match(self):
        """Text with no tracked companies returns empty."""
        text = "The weather is nice today."
        matched = BaseFetcher.match_tickers(text, self.TICKERS_MAP)
        assert len(matched) == 0

    def test_case_insensitive(self):
        """Matching is case-insensitive."""
        text = "nvidia and intel had a great quarter."
        matched = BaseFetcher.match_tickers(text, self.TICKERS_MAP)
        assert "NVDA" in matched
        assert "INTC" in matched

    def test_empty_text(self):
        """Empty text returns no matches."""
        matched = BaseFetcher.match_tickers("", self.TICKERS_MAP)
        assert len(matched) == 0
