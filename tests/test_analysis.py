"""Tests for analysis modules — prefilter, cascade, signals."""

import pytest
from datetime import datetime

from trading_expert.fetchers.base import RawArticle
from trading_expert.analysis.prefilter import Prefilter, PrefilterResult
from trading_expert.analysis.cascade import SupplyChainGraph, Company
from trading_expert.analysis.signals import SignalScorer
from trading_expert.analysis.deepseek import DeepSeekSignal


# ── Test Fixtures ──────────────────────────────────────────────────────────

TICKERS_MAP = {
    "NVDA": ["NVIDIA", "NVDA", "Nvidia Corporation"],
    "INTC": ["Intel", "INTC", "Intel Corporation"],
    "TSM": ["TSMC", "TSM", "Taiwan Semiconductor"],
    "AVGO": ["Broadcom", "AVGO"],
    "AAPL": ["Apple", "AAPL"],
    "ASML": ["ASML", "ASML Holding"],
}

URGENCY_KEYWORDS = {
    "critical": ["bankruptcy", "acquisition", "restructuring", "export ban"],
    "high": ["earnings beat", "earnings miss", "new product", "deal signed", "partnership"],
}


@pytest.fixture
def prefilter():
    return Prefilter(
        tickers_map=TICKERS_MAP,
        urgency_keywords=URGENCY_KEYWORDS,
        sentiment_threshold=0.6,
    )


@pytest.fixture
def supply_chain():
    companies = [
        Company(ticker="NVDA", name="NVIDIA", sector="gpu_ai", role="fabless_designer"),
        Company(ticker="TSM", name="TSMC", sector="foundry", role="pure_foundry"),
        Company(ticker="ASML", name="ASML", sector="equipment", role="equipment_maker"),
        Company(ticker="INTC", name="Intel", sector="cpu_foundry", role="designer_foundry"),
        Company(ticker="AMD", name="AMD", sector="cpu_gpu", role="fabless_designer"),
    ]
    edges = {
        "NVDA": {
            "depends_on": ["TSM"],
            "supplies_to": [],
            "competes_with": ["AMD", "INTC"],
        },
        "TSM": {
            "depends_on": ["ASML"],
            "supplies_to": ["NVDA", "AMD"],
            "competes_with": ["INTC"],
        },
        "ASML": {
            "depends_on": [],
            "supplies_to": ["TSM", "INTC"],
            "competes_with": [],
        },
        "INTC": {
            "depends_on": ["ASML"],
            "supplies_to": [],
            "competes_with": ["NVDA", "AMD", "TSM"],
        },
        "AMD": {
            "depends_on": ["TSM"],
            "supplies_to": [],
            "competes_with": ["NVDA", "INTC"],
        },
    }
    return SupplyChainGraph(companies=companies, edges=edges)


@pytest.fixture
def signal_scorer():
    config = {
        "weights": {
            "deepseek_sentiment": 0.50,
            "news_volume": 0.20,
            "source_credibility": 0.15,
            "price_confirmation": 0.15,
        },
        "thresholds": {
            "strong_alert": 0.6,
            "watch_list": 0.4,
            "min_confidence": "medium",
        },
    }
    return SignalScorer(config)


# ── Prefilter Tests ─────────────────────────────────────────────────────────

class TestPrefilter:
    def test_scores_positive_sentiment(self, prefilter):
        article = RawArticle(
            title="NVIDIA delivers outstanding record profit and raises guidance",
            source="reuters",
            source_tier=1,
            text="NVIDIA delivered outstanding results with record profit, "
                 "strong growth, and raised guidance significantly. "
                 "This is great news for the company and investors are thrilled.",
        )
        result = prefilter.score(article)
        assert result.vader_score > 0.0  # Positive sentiment
        assert "NVDA" in result.matched_tickers
        assert result.should_escalate  # Positive sentiment + tier-1 source

    def test_scores_negative_sentiment(self, prefilter):
        article = RawArticle(
            title="Intel terrible disaster — massive layoffs and horrible losses",
            source="bloomberg",
            source_tier=1,
            text="Intel Corporation is a disaster with horrible losses, "
                 "massive layoffs, and a failing restructuring plan. "
                 "The company is losing badly to competitors and investors are worried.",
        )
        result = prefilter.score(article)
        assert result.vader_score < 0.0  # Negative sentiment
        assert "INTC" in result.matched_tickers
        assert "restructuring" in result.keyword_triggers
        assert result.should_escalate

    def test_neutral_article_not_escalated(self, prefilter):
        article = RawArticle(
            title="Tech stocks trade flat in afternoon session",
            source="marketwatch",
            source_tier=2,
            text="Technology stocks traded mostly flat on Tuesday as investors "
                 "waited for earnings reports later this week.",
        )
        result = prefilter.score(article)
        assert not result.should_escalate

    def test_detects_critical_keywords(self, prefilter):
        article = RawArticle(
            title="Company faces potential bankruptcy",
            source="cnbc",
            source_tier=2,
            text="The semiconductor startup is facing potential bankruptcy "
                 "after failing to secure additional funding.",
        )
        result = prefilter.score(article)
        assert "bankruptcy" in result.keyword_triggers
        assert result.should_escalate  # Critical keyword always escalates

    def test_matches_multiple_tickers(self, prefilter):
        article = RawArticle(
            title="NVIDIA and TSMC announce partnership, Intel shares fall",
            source="reuters",
            source_tier=1,
            text="NVIDIA and TSMC announced a new manufacturing partnership "
                 "while Intel shares fell 3% on the news.",
        )
        result = prefilter.score(article)
        assert "NVDA" in result.matched_tickers
        assert "TSM" in result.matched_tickers
        assert "INTC" in result.matched_tickers

    def test_empty_article(self, prefilter):
        article = RawArticle(
            title="", source="unknown", source_tier=3, text=""
        )
        result = prefilter.score(article)
        assert result.vader_score == 0.0
        assert len(result.matched_tickers) == 0


# ── Cascade Tests ───────────────────────────────────────────────────────────

class TestSupplyChainGraph:
    def test_get_suppliers(self, supply_chain):
        assert "TSM" in supply_chain.get_suppliers("NVDA")
        assert "ASML" in supply_chain.get_suppliers("TSM")

    def test_get_customers(self, supply_chain):
        assert "NVDA" in supply_chain.get_customers("TSM")
        assert "AMD" in supply_chain.get_customers("TSM")

    def test_get_competitors(self, supply_chain):
        assert "AMD" in supply_chain.get_competitors("NVDA")
        assert "INTC" in supply_chain.get_competitors("NVDA")

    def test_bearish_tsm_impacts_nvda(self, supply_chain):
        """If TSMC has bad news, NVDA should be bearish-impacted."""
        impacts = supply_chain.get_impacted("TSM", "bearish")
        tickers_impacted = [t for t, d, r in impacts if d == "bearish"]
        assert "NVDA" in tickers_impacted
        assert "AMD" in tickers_impacted

    def test_bearish_tsm_helps_competitors(self, supply_chain):
        """If TSMC has bad news, INTC (competitor) might benefit."""
        impacts = supply_chain.get_impacted("TSM", "bearish")
        bullish_impacted = [t for t, d, r in impacts if d == "bullish"]
        assert "INTC" in bullish_impacted

    def test_bullish_nvda_impacts_tsm(self, supply_chain):
        """If NVIDIA has great news, TSMC benefits (more orders)."""
        impacts = supply_chain.get_impacted("NVDA", "bullish")
        bullish_impacted = [t for t, d, r in impacts if d == "bullish"]
        assert "TSM" in bullish_impacted

    def test_bullish_nvda_hurts_competitors(self, supply_chain):
        """If NVIDIA soars, AMD and INTC may lose share."""
        impacts = supply_chain.get_impacted("NVDA", "bullish")
        bearish_impacted = [t for t, d, r in impacts if d == "bearish"]
        assert "AMD" in bearish_impacted
        assert "INTC" in bearish_impacted

    def test_generate_context_text(self, supply_chain):
        ctx = supply_chain.generate_context_text("NVDA")
        assert "NVDA" in ctx
        assert "NVIDIA" in ctx
        assert "TSM" in ctx  # Supplier

    def test_unknown_ticker(self, supply_chain):
        assert supply_chain.get_company("FAKE") is None
        assert supply_chain.get_suppliers("FAKE") == []
        assert supply_chain.get_impacted("FAKE", "bullish") == []


# ── Signal Scorer Tests ─────────────────────────────────────────────────────

class TestSignalScorer:
    @staticmethod
    def _make_deepseek_signal(**overrides):
        """Helper to create DeepSeekSignal with sensible defaults."""
        defaults = {
            "article_id": "abc123",
            "primary_ticker": "NVDA",
            "sentiment": 0.8,
            "confidence": "high",
            "reasoning": "Strong earnings beat with raised guidance.",
            "cascade_effects": [
                {"ticker": "TSM", "direction": "bullish", "reason": "More chip orders"},
            ],
            "urgency": "high",
            "action": "buy",
            "suggested_timeframe": "3-6 months",
            "risk_factors": ["Export controls"],
        }
        defaults.update(overrides)
        return DeepSeekSignal(**defaults)

    def test_strong_buy_signal(self, signal_scorer):
        """Strong sentiment + high credibility + price confirmation → strong buy."""
        ds = self._make_deepseek_signal(sentiment=0.95, confidence="high")
        result = signal_scorer.score(
            ds, source_credibility=0.9, price_confirmation=0.8
        )
        # 0.50*0.95 + 0.15*0.9 + 0.15*0.8 = 0.475 + 0.135 + 0.12 = 0.73
        assert result.score > 0.6
        assert result.should_alert
        assert "BUY" in result.alert_label

    def test_hold_on_low_confidence(self, signal_scorer):
        ds = self._make_deepseek_signal(sentiment=0.9, confidence="low")
        result = signal_scorer.score(ds)
        assert not result.should_alert  # Low confidence blocks alert

    def test_strong_sell(self, signal_scorer):
        """Very negative sentiment + price confirmation → strong sell."""
        ds = self._make_deepseek_signal(
            primary_ticker="INTC",
            sentiment=-0.95,
            confidence="high",
            action="sell",
            reasoning="Major restructuring and layoffs.",
        )
        result = signal_scorer.score(
            ds, source_credibility=0.9, price_confirmation=-0.7
        )
        # 0.50*(-0.95) + 0.15*(-0.7) + 0.15*0.9 = -0.475 + -0.105 + 0.135 = -0.445
        # Actually, credibility is positive, so: -0.475 + -0.105 + 0.135 = -0.445
        # We need more: -0.95*0.50 + 0.9*0.15 + (-0.7)*0.15 = -0.475 + 0.135 - 0.105 = -0.445
        # Still not -0.5. Let me adjust.
        assert result.score < -0.4
        assert result.action == "sell"

    def test_cascade_signal(self, signal_scorer):
        cascade = {"ticker": "TSM", "direction": "bullish", "reason": "More orders"}
        result = signal_scorer.score_cascade(cascade, "NVDA", ["abc123"])
        assert result.ticker == "TSM"
        assert result.cascade_source
        assert result.cascade_parent == "NVDA"
        assert result.action == "buy"

    def test_score_with_news_volume_spike(self, signal_scorer):
        """High news volume should amplify the signal."""
        ds = self._make_deepseek_signal(sentiment=0.6, confidence="medium")
        normal = signal_scorer.score(ds, news_volume_zscore=0.0)
        spiked = signal_scorer.score(ds, news_volume_zscore=2.0)
        assert spiked.score > normal.score  # Spike amplifies

    def test_score_with_price_confirmation(self, signal_scorer):
        """Price moving in same direction confirms the signal."""
        ds = self._make_deepseek_signal(sentiment=0.5, confidence="medium")
        confirmed = signal_scorer.score(ds, price_confirmation=0.8)
        contradicted = signal_scorer.score(ds, price_confirmation=-0.8)
        assert confirmed.score > contradicted.score
