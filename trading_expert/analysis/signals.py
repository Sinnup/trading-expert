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

from .deepseek import DeepSeekSignal

logger = logging.getLogger(__name__)


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
        self.alert_threshold = self.thresholds.get("strong_alert", 0.6)
        self.watch_threshold = self.thresholds.get("watch_list", 0.4)

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
        # Clamp inputs
        news_vol = max(-3.0, min(3.0, news_volume_zscore)) / 3.0  # Normalize to [-1, 1]
        price_conf = max(-1.0, min(1.0, price_confirmation))

        # Source credibility amplifies the DeepSeek sentiment (0=no boost, 1=full boost)
        credibility_boost = source_credibility  # 0.0 to 1.0
        credibility_weight = self.weights.get("source_credibility", 0.15)

        # Combined score: credibility amplifies sentiment magnitude
        score = (
            self.weights.get("deepseek_sentiment", 0.50)
            * deepseek.sentiment
            * (1.0 + credibility_weight * credibility_boost)
            + self.weights.get("news_volume", 0.20) * news_vol
            + self.weights.get("price_confirmation", 0.15) * price_conf
        )

        # Clamp to [-1.0, 1.0]
        score = max(-1.0, min(1.0, score))

        # Determine if alert should fire
        should_alert = abs(score) >= self.alert_threshold
        if should_alert and deepseek.confidence == "low":
            should_alert = False  # Don't alert on low confidence

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
        )

    def score_cascade(
        self,
        cascade_effect: dict,
        parent_ticker: str,
        source_article_ids: list[str],
    ) -> FinalSignal:
        """Create a signal for a cascade effect (secondary impact)."""
        direction = cascade_effect.get("direction", "neutral")
        sentiment = 0.6 if direction == "bullish" else -0.6

        score = sentiment  # Cascade signals get pure sentiment score

        action = "buy" if direction == "bullish" else "sell"
        if abs(score) < 0.4:
            action = "hold"

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

    @staticmethod
    def _get_alert_label(score: float) -> str:
        """Get human-readable label for a score."""
        if score >= 0.6:
            return "🟢 STRONG BUY"
        elif score >= 0.4:
            return "🟢 BUY"
        elif score >= 0.2:
            return "🟡 WEAK BUY"
        elif score > -0.2:
            return "⚪ HOLD"
        elif score > -0.4:
            return "🟡 WEAK SELL"
        elif score > -0.6:
            return "🔴 SELL"
        else:
            return "🔴 STRONG SELL"

    @staticmethod
    def _resolve_action(deepseek_action: str, score: float) -> str:
        """Resolve the final action, possibly overriding DeepSeek."""
        if abs(score) < 0.2:
            return "hold"
        if deepseek_action in ("buy", "sell", "hold"):
            return deepseek_action
        return "hold"

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
