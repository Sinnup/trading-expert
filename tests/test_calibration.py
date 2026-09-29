"""Tests for the weight-calibration learning loop.

Covers:
- the price-confirmation gate (multiplicative, not additive)
- factor breakdown recorded on every scored signal
- learned-weights overlay persistence + merge
- the numpy logistic fit / calibration report on synthetic outcomes
"""

import numpy as np
import pytest

from trading_expert.analysis.calibration import (
    FEATURE_KEYS,
    CalibrationReport,
    TrainingRow,
    _auc,
    _fit_logistic,
    _sigmoid,
    calibrate,
    load_learned_weights,
    merge_weights,
    save_learned_weights,
)
from trading_expert.analysis.deepseek import DeepSeekSignal
from trading_expert.analysis.signals import SignalScorer


def _scorer(**weights) -> SignalScorer:
    w = {
        "deepseek_sentiment": 0.50,
        "news_volume": 0.20,
        "source_credibility": 0.15,
        "price_confirmation": 0.15,
    }
    w.update(weights)
    return SignalScorer({"weights": w, "thresholds": {"strong_alert": 0.5, "min_confidence": "medium"}})


def _ds(**overrides) -> DeepSeekSignal:
    defaults = {"article_id": "x", "primary_ticker": "NVDA", "sentiment": 0.8,
                "confidence": "medium", "reasoning": "r"}
    defaults.update(overrides)
    return DeepSeekSignal(**defaults)


# ── Price confirmation gate ──────────────────────────────────────────────────

class TestPriceGate:
    def test_contradicting_price_dampens_score(self):
        confirm = _scorer().score(_ds(), price_confirmation=1.0).score
        contradict = _scorer().score(_ds(), price_confirmation=-1.0).score
        neutral = _scorer().score(_ds(), price_confirmation=0.0).score
        # Confirmation lifts the score, contradiction shrinks it toward zero.
        assert contradict < neutral < confirm

    def test_gate_is_multiplicative(self):
        # With price_weight=0.15, full contradiction scales the base by 0.85.
        neutral = _scorer().score(_ds(sentiment=0.6), price_confirmation=0.0).score
        contradict = _scorer().score(_ds(sentiment=0.6), price_confirmation=-1.0).score
        assert contradict == pytest.approx(neutral * 0.85, abs=5e-4)

    def test_neutral_price_leaves_base_unchanged(self):
        assert _scorer().score(_ds(), price_confirmation=0.0).score == pytest.approx(
            0.50 * 0.8 * (1 + 0.15 * 0.5), rel=1e-6
        )


# ── Factor breakdown recorded ────────────────────────────────────────────────

class TestFactorsRecorded:
    def test_factors_populated(self):
        result = _scorer().score(
            _ds(sentiment=0.7), news_volume_zscore=3.0,
            source_credibility=1.0, price_confirmation=0.5,
        )
        assert set(result.factors) == set(FEATURE_KEYS)
        assert result.factors["deepseek_sentiment"] == 0.7
        assert result.factors["source_credibility"] == 1.0
        # z-score of 3.0 normalizes to the +1.0 clamp.
        assert result.factors["news_volume"] == pytest.approx(1.0)
        assert result.factors["price_confirmation"] == 0.5


# ── Overlay persistence ──────────────────────────────────────────────────────

class TestLearnedWeightsOverlay:
    def test_save_and_load_roundtrip(self, tmp_path, monkeypatch):
        import trading_expert.analysis.calibration as cal
        monkeypatch.setattr(cal, "_DATA_DIR", str(tmp_path))
        weights = {k: 0.25 for k in FEATURE_KEYS}
        cal.save_learned_weights("semiconductor", weights, meta={"n_samples": 50})
        assert cal.load_learned_weights("semiconductor") == weights

    def test_load_missing_returns_none(self, tmp_path, monkeypatch):
        import trading_expert.analysis.calibration as cal
        monkeypatch.setattr(cal, "_DATA_DIR", str(tmp_path))
        assert cal.load_learned_weights("bmv") is None

    def test_merge_overlays_on_base(self):
        base = {"deepseek_sentiment": 0.5, "news_volume": 0.2}
        merged = merge_weights(base, {"news_volume": 0.4})
        assert merged == {"deepseek_sentiment": 0.5, "news_volume": 0.4}

    def test_merge_none_overlay_is_identity(self):
        base = {"deepseek_sentiment": 0.5}
        assert merge_weights(base, None) == base


# ── Logistic fit primitives ──────────────────────────────────────────────────

class TestLogisticFit:
    def test_sigmoid_bounds(self):
        assert _sigmoid(np.array([-1000.0]))[0] == pytest.approx(0.0, abs=1e-6)
        assert _sigmoid(np.array([1000.0]))[0] == pytest.approx(1.0, abs=1e-6)

    def test_fit_recovers_separating_direction(self):
        # Feature 0 perfectly predicts the label; others are noise.
        rng = np.random.default_rng(0)
        n = 400
        X = rng.normal(size=(n, 3))
        y = (X[:, 0] > 0).astype(float)
        w, b = _fit_logistic(X, y, epochs=3000)
        assert abs(w[0]) > abs(w[1])
        assert abs(w[0]) > abs(w[2])

    def test_auc_perfect_and_chance(self):
        y = np.array([0, 0, 1, 1])
        assert _auc(y, np.array([0.1, 0.2, 0.8, 0.9])) == pytest.approx(1.0)
        assert _auc(y, np.array([0.5, 0.5, 0.5, 0.5])) == pytest.approx(0.5)

    def test_auc_single_class_is_half(self):
        assert _auc(np.array([1, 1, 1]), np.array([0.1, 0.5, 0.9])) == 0.5


# ── End-to-end calibrate() on synthetic data ─────────────────────────────────

class _FakeQuery:
    def __init__(self, rows):
        self._rows = rows
    def join(self, *a, **k): return self
    def filter(self, *a, **k): return self
    def all(self): return self._rows


class _FakeSession:
    def __init__(self, rows):
        self._rows = rows
    def query(self, *a, **k):
        return _FakeQuery(self._rows)


class _Sig:
    def __init__(self, score, factors, signal_type="intraday"):
        self.score = score
        self.factors = factors
        self.signal_type = signal_type


class _Out:
    def __init__(self, return_7d_pct, return_1d_pct=None):
        self.return_7d_pct = return_7d_pct
        self.return_1d_pct = return_1d_pct


def _row(sentiment, ret7, ret1=None):
    factors = {
        "deepseek_sentiment": sentiment,
        "news_volume": 0.0,
        "source_credibility": 0.5,
        "price_confirmation": 0.0,
    }
    return (_Sig(score=sentiment, factors=factors), _Out(ret7, ret1))


class TestCalibrateEndToEnd:
    def test_too_few_samples_reports_unfitted(self):
        session = _FakeSession([_row(0.6, 2.0), _row(-0.6, -2.0)])
        report = calibrate(session, "semiconductor", min_samples=30)
        assert isinstance(report, CalibrationReport)
        assert not report.fitted
        assert report.n_samples == 2

    def test_sentiment_predicts_direction(self):
        # High-conviction calls come true; low-conviction ones don't. Sentiment
        # magnitude therefore separates correct from incorrect (both classes
        # present), so the fit should credit sentiment. 1d move is half the 7d.
        rows = []
        for _ in range(30):
            rows.append(_row(0.9, ret7=3.0, ret1=1.5))    # strong → correct (y=1)
        for _ in range(30):
            rows.append(_row(0.1, ret7=-3.0, ret1=-1.5))  # weak → wrong (y=0)
        report = calibrate(_FakeSession(rows), "semiconductor", min_samples=30)
        assert report.fitted
        assert report.base_rate == pytest.approx(0.5)
        assert report.auc > 0.9  # sentiment cleanly discriminates
        assert report.decay_ratio == pytest.approx(0.5, abs=0.05)
        assert report.suggested_weights["deepseek_sentiment"] > 0.5  # dominant factor
