"""
Signal scorer — combines DeepSeek analysis with quantitative factors.

Takes DeepSeek's structured output and combines it with:
- News volume (Z-score of article count vs 30-day average)
- Source credibility (weighted by source tier)
- Price confirmation (is the stock moving in the expected direction?)

Produces a final score from -1.0 (strong sell) to +1.0 (strong buy)
and determines whether an alert should be sent.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from trading_expert.constants import (
    ACTION_BUY,
    ACTION_HOLD,
    ACTION_HOLD_THRESHOLD,
    ACTION_SELL,
    CASCADE_ACTION_THRESHOLD,
    CASCADE_SENTIMENT_MAGNITUDE,
    CONFIDENCE_ORDER,
    DEFAULT_MIN_CONFIDENCE,
    DEFAULT_STRONG_ALERT_THRESHOLD,
    DEFAULT_WATCH_THRESHOLD,
    DEFAULT_WEIGHTS,
    NEWS_VOLUME_ZSCORE_CLAMP,
    PRICE_CONFIRMATION_SCALE_PCT,
    SCORE_MAX,
    SCORE_MIN,
    SIGNAL_LABEL_BANDS,
    SIGNAL_LABEL_STRONG_SELL,
)
from .deepseek import DeepSeekSignal

logger = logging.getLogger(__name__)


def signal_label(score: float) -> str:
    """Human-readable label for a signal score (e.g. '🟢 STRONG BUY')."""
    for lower_bound, label in SIGNAL_LABEL_BANDS:
        if score >= lower_bound:
            return label
    return SIGNAL_LABEL_STRONG_SELL


def compute_price_confirmation(
    sentiment: float,
    change_pct: Optional[float],
    scale_pct: float = PRICE_CONFIRMATION_SCALE_PCT,
) -> float:
    """How much the price move confirms the sentiment direction.

    Returns a value in [-1.0, 1.0]: positive when the price is moving the same
    way as the sentiment (confirmation), negative when it contradicts it. A
    daily move of ``scale_pct`` (or more) in the confirming direction yields the
    full ±1.0.

    Args:
        sentiment: DeepSeek sentiment, -1.0 (bearish) to +1.0 (bullish).
        change_pct: Day-over-day price change in percent. None → no signal.
        scale_pct: Move size that counts as full confirmation.
    """
    if change_pct is None or sentiment == 0:
        return 0.0
    normalized_move = max(-1.0, min(1.0, change_pct / scale_pct))
    direction = 1.0 if sentiment > 0 else -1.0
    return direction * normalized_move


@dataclass
class FinalSignal:
    """Complete trading signal ready for storage and notification."""
    ticker: str
    score: float  # -1.0 to +1.0 (combined)
    deepseek_sentiment: float  # DeepSeek's raw sentiment
    deepseek_confidence: str  # "high" "medium" "low"
    action: str  # "buy" "sell" "hold"
    suggested_timeframe: str
    reasoning: str
    risk_factors: list[str] = field(default_factory=list)
    cascade_effects: list[dict] = field(default_factory=list)
    cascade_source: bool = False
    cascade_parent: Optional[str] = None
    source_article_ids: list[str] = field(default_factory=list)
    urgency: str = "medium"
    signal_type: str = "intraday"
    price_at_signal: Optional[float] = None
    should_alert: bool = False
    alert_label: str = ""
    alert_message: str = ""
    # Per-factor breakdown (the normalized inputs that produced ``score``).
    # Persisted on the Signal so the calibration loop has training features.
    factors: dict[str, float] = field(default_factory=dict)


class SignalScorer:
    """Combines AI analysis with market data into actionable signals."""

    def __init__(self, config: dict):
        """
        Args:
            config: Signal scoring configuration from settings.yaml.
                {
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
                    }
                }
        """
        self.weights = config.get("weights", {})
        self.thresholds = config.get("thresholds", {})
        self.alert_threshold = self.thresholds.get(
            "strong_alert", DEFAULT_STRONG_ALERT_THRESHOLD
        )
        self.watch_threshold = self.thresholds.get(
            "watch_list", DEFAULT_WATCH_THRESHOLD
        )
        self.min_confidence = self.thresholds.get(
            "min_confidence", DEFAULT_MIN_CONFIDENCE
        )

    def score(
        self,
        deepseek: DeepSeekSignal,
        news_volume_zscore: float = 0.0,
        source_credibility: float = 0.5,
        price_confirmation: float = 0.0,
        price: Optional[float] = None,
    ) -> FinalSignal:
        """Compute the final signal score.

        Args:
            deepseek: The DeepSeek analysis result.
            news_volume_zscore: Z-score of article count (how unusual is the volume?).
                > 2.0 means unusually high news volume.
            source_credibility: 0.0 (tier-3 blog) to 1.0 (tier-1 Reuters).
            price_confirmation: -1.0 to 1.0.
                > 0 means price is moving in the expected direction.
            price: Current stock price.

        Returns:
            FinalSignal with combined score and alert decision.
        """
        # Clamp inputs, normalizing the news-volume z-score to [-1, 1]
        news_vol = (
            max(-NEWS_VOLUME_ZSCORE_CLAMP, min(NEWS_VOLUME_ZSCORE_CLAMP, news_volume_zscore))
            / NEWS_VOLUME_ZSCORE_CLAMP
        )
        price_conf = max(-1.0, min(1.0, price_confirmation))

        # Source credibility amplifies the DeepSeek sentiment (0=no boost, 1=full boost)
        credibility_boost = source_credibility  # 0.0 to 1.0
        credibility_weight = self.weights.get(
            "source_credibility", DEFAULT_WEIGHTS["source_credibility"]
        )
        volume_weight = self.weights.get("news_volume", DEFAULT_WEIGHTS["news_volume"])
        price_weight = self.weights.get(
            "price_confirmation", DEFAULT_WEIGHTS["price_confirmation"]
        )

        # Conviction from the news itself: DeepSeek sentiment amplified by source
        # credibility, plus the "surprise" term (how unusual is this ticker's
        # coverage right now).
        base = (
            self.weights.get("deepseek_sentiment", DEFAULT_WEIGHTS["deepseek_sentiment"])
            * deepseek.sentiment
            * (1.0 + credibility_weight * credibility_boost)
            + volume_weight * news_vol
        )

        # Price action gates that conviction instead of nudging it: a move that
        # confirms the thesis amplifies the score, a contradicting move dampens
        # it. ``price_weight`` sets how hard the gate bites — full contradiction
        # scales the score by (1 - price_weight), full confirmation by
        # (1 + price_weight). The market disagreeing is a veto signal, not a
        # small vote against.
        gate = 1.0 + price_weight * price_conf
        score = base * gate

        # Clamp to the configured score range
        score = max(SCORE_MIN, min(SCORE_MAX, score))

        # Record the normalized factor inputs for the calibration loop.
        factors = {
            "deepseek_sentiment": deepseek.sentiment,
            "news_volume": news_vol,
            "source_credibility": credibility_boost,
            "price_confirmation": price_conf,
        }

        # Determine if alert should fire: above threshold AND confident enough
        should_alert = abs(score) >= self.alert_threshold
        if should_alert and not self._meets_min_confidence(deepseek.confidence):
            should_alert = False

        # Alert label
        alert_label = self._get_alert_label(score)

        # Build alert message
        alert_message = self._build_alert_message(
            deepseek=deepseek,
            score=score,
            alert_label=alert_label,
            price=price,
        )

        return FinalSignal(
            ticker=deepseek.primary_ticker,
            score=round(score, 4),
            deepseek_sentiment=deepseek.sentiment,
            deepseek_confidence=deepseek.confidence,
            action=self._resolve_action(deepseek.action, score),
            suggested_timeframe=deepseek.suggested_timeframe,
            reasoning=deepseek.reasoning,
            risk_factors=deepseek.risk_factors,
            cascade_effects=deepseek.cascade_effects,
            source_article_ids=[deepseek.article_id],
            urgency=deepseek.urgency,
            signal_type="intraday",
            price_at_signal=price,
            should_alert=should_alert,
            alert_label=alert_label,
            alert_message=alert_message,
            factors=factors,
        )

    def score_cascade(
        self,
        cascade_effect: dict,
        parent_ticker: str,
        source_article_ids: list[str],
    ) -> FinalSignal:
        """Create a signal for a cascade effect (secondary impact)."""
        direction = cascade_effect.get("direction", "neutral")
        sentiment = (
            CASCADE_SENTIMENT_MAGNITUDE
            if direction == "bullish"
            else -CASCADE_SENTIMENT_MAGNITUDE
        )

        score = sentiment  # Cascade signals get pure sentiment score

        action = ACTION_BUY if direction == "bullish" else ACTION_SELL
        if abs(score) < CASCADE_ACTION_THRESHOLD:
            action = ACTION_HOLD

        return FinalSignal(
            ticker=cascade_effect.get("ticker", "UNKNOWN"),
            score=round(score, 4),
            deepseek_sentiment=sentiment,
            deepseek_confidence="medium",
            action=action,
            suggested_timeframe="3-6 months",
            reasoning=cascade_effect.get("reason", ""),
            cascade_source=True,
            cascade_parent=parent_ticker,
            source_article_ids=source_article_ids,
            urgency="medium",
            signal_type="cascade",
            should_alert=abs(score) >= self.alert_threshold,
            alert_label=self._get_alert_label(score),
        )

    def _meets_min_confidence(self, confidence: str) -> bool:
        """Whether a DeepSeek confidence meets the configured minimum to alert."""
        return CONFIDENCE_ORDER.get(confidence, 0) >= CONFIDENCE_ORDER.get(
            self.min_confidence, CONFIDENCE_ORDER[DEFAULT_MIN_CONFIDENCE]
        )

    @staticmethod
    def _get_alert_label(score: float) -> str:
        """Get human-readable label for a score."""
        return signal_label(score)

    @staticmethod
    def _resolve_action(deepseek_action: str, score: float) -> str:
        """Resolve the final action, possibly overriding DeepSeek."""
        if abs(score) < ACTION_HOLD_THRESHOLD:
            return ACTION_HOLD
        if deepseek_action in (ACTION_BUY, ACTION_SELL, ACTION_HOLD):
            return deepseek_action
        return ACTION_HOLD

    @staticmethod
    def _build_alert_message(
        deepseek: DeepSeekSignal,
        score: float,
        alert_label: str,
        price: Optional[float] = None,
    ) -> str:
        """Build a formatted alert message for Telegram."""
        lines = [f"{alert_label}: {deepseek.primary_ticker}"]

        if price:
            lines.append(f"💵 Price: ${price:.2f}")

        lines.append(f"📊 Signal: {score:+.2f}")
        lines.append(f"🎯 Confidence: {deepseek.confidence}")
        lines.append(f"\n💡 {deepseek.reasoning}")

        if deepseek.cascade_effects:
            lines.append("\n🔗 Cascade effects:")
            for ce in deepseek.cascade_effects:
                emoji = "🟢" if ce.get("direction") == "bullish" else "🔴"
                lines.append(f"  {emoji} {ce.get('ticker')}: {ce.get('reason')}")

        if deepseek.risk_factors:
            lines.append("\n⚠️ Risks:")
            for risk in deepseek.risk_factors:
                lines.append(f"  • {risk}")

        lines.append(f"\n⏱️ Timeframe: {deepseek.suggested_timeframe}")
        return "\n".join(lines)
