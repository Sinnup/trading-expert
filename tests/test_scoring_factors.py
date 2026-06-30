"""Tests for the scoring factors wired into the intraday pipeline.

Covers the pieces that were previously dead/zeroed in production:
- news-volume z-scores (batch-relative coverage)
- price confirmation (does the price move confirm the sentiment?)
- min_confidence gating (config said "medium" but code only blocked "low")
- the shared signal_label helper
"""

import pytest

from trading_expert.analysis.deepseek import DeepSeekSignal
from trading_expert.analysis.news_volume import (
    compute_batch_volume_zscores,
    count_articles_per_ticker,
)
from trading_expert.analysis.signals import (
    SignalScorer,
    compute_price_confirmation,
    signal_label,
)


def _scorer(min_confidence: str = "medium", strong_alert: float = 0.5) -> SignalScorer:
    return SignalScorer(
        {
            "weights": {
                "deepseek_sentiment": 0.50,
                "news_volume": 0.20,
                "source_credibility": 0.15,
                "price_confirmation": 0.15,
            },
            "thresholds": {
                "strong_alert": strong_alert,
                "watch_list": 0.4,
                "min_confidence": min_confidence,
            },
        }
    )


def _ds(**overrides) -> DeepSeekSignal:
    defaults = {
        "article_id": "x",
        "primary_ticker": "NVDA",
        "sentiment": 0.9,
        "confidence": "medium",
        "reasoning": "r",
    }
    defaults.update(overrides)
    return DeepSeekSignal(**defaults)


# ── News volume ──────────────────────────────────────────────────────────────

class TestNewsVolume:
    def test_count_articles_per_ticker(self):
        counts = count_articles_per_ticker([["NVDA", "TSM"], ["NVDA"], ["AAPL"]])
        assert counts == {"NVDA": 2, "TSM": 1, "AAPL": 1}

    def test_single_ticker_is_neutral(self):
        assert compute_batch_volume_zscores({"NVDA": 5}) == {"NVDA": 0.0}

    def test_equal_counts_are_neutral(self):
        z = compute_batch_volume_zscores({"NVDA": 3, "TSM": 3})
        assert z == {"NVDA": 0.0, "TSM": 0.0}

    def test_spike_is_positive_quiet_is_negative(self):
        z = compute_batch_volume_zscores({"NVDA": 10, "TSM": 1, "AAPL": 1})
        assert z["NVDA"] > 0
        assert z["TSM"] < 0
        assert z["AAPL"] < 0

    def test_empty(self):
        assert compute_batch_volume_zscores({}) == {}


# ── Price confirmation ───────────────────────────────────────────────────────

class TestPriceConfirmation:
    def test_bullish_with_price_up_confirms(self):
        assert compute_price_confirmation(0.8, 1.5) > 0

    def test_bullish_with_price_down_contradicts(self):
        assert compute_price_confirmation(0.8, -1.5) < 0

    def test_bearish_with_price_down_confirms(self):
        assert compute_price_confirmation(-0.8, -1.5) > 0

    def test_none_change_is_neutral(self):
        assert compute_price_confirmation(0.8, None) == 0.0

    def test_zero_sentiment_is_neutral(self):
        assert compute_price_confirmation(0.0, 5.0) == 0.0

    def test_clamped_to_unit_range(self):
        assert compute_price_confirmation(1.0, 100.0) == pytest.approx(1.0)
        assert compute_price_confirmation(-1.0, 100.0) == pytest.approx(-1.0)


# ── min_confidence gating ────────────────────────────────────────────────────

class TestMinConfidenceGating:
    def test_medium_confidence_alerts_at_medium_floor(self):
        result = _scorer(min_confidence="medium").score(
            _ds(confidence="medium"), source_credibility=1.0
        )
        assert result.score >= 0.5
        assert result.should_alert

    def test_low_confidence_blocked(self):
        result = _scorer(min_confidence="medium").score(
            _ds(confidence="low"), source_credibility=1.0
        )
        assert result.score >= 0.5
        assert not result.should_alert

    def test_medium_blocked_when_floor_is_high(self):
        result = _scorer(min_confidence="high").score(
            _ds(confidence="medium"), source_credibility=1.0
        )
        assert result.score >= 0.5
        assert not result.should_alert


# ── Labels ───────────────────────────────────────────────────────────────────

class TestSignalLabel:
    @pytest.mark.parametrize(
        "score,expected",
        [
            (0.9, "🟢 STRONG BUY"),
            (0.5, "🟢 BUY"),
            (0.3, "🟡 WEAK BUY"),
            (0.0, "⚪ HOLD"),
            (-0.3, "🟡 WEAK SELL"),
            (-0.5, "🔴 SELL"),
            (-0.9, "🔴 STRONG SELL"),
        ],
    )
    def test_labels(self, score, expected):
        assert signal_label(score) == expected
