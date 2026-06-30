"""Central constants for the Trading Expert pipeline.

All tunable magic numbers live here so they are documented in one place and
shared consistently across the scorer, fetch tasks, and notifier. Values that
are meant to be operator-tunable per universe (weights, alert thresholds) are
loaded from the YAML config; the constants here are the in-code defaults/
fallbacks plus the non-config structural values (clamps, scales, tier maps).
"""

# ── Signal scoring defaults (fallbacks when config omits them) ───────────────
DEFAULT_WEIGHTS: dict[str, float] = {
    "deepseek_sentiment": 0.50,
    "news_volume": 0.20,
    "source_credibility": 0.15,
    "price_confirmation": 0.15,
}

# Alert fires when |score| >= this; watch-list (daily summary) uses the lower one.
DEFAULT_STRONG_ALERT_THRESHOLD: float = 0.5
DEFAULT_WATCH_THRESHOLD: float = 0.4

# Minimum DeepSeek confidence required to push an alert.
DEFAULT_MIN_CONFIDENCE: str = "medium"
# Ordinal ranking so confidences can be compared (>= min_confidence).
CONFIDENCE_ORDER: dict[str, int] = {"low": 0, "medium": 1, "high": 2}

# Final score is clamped to this inclusive range.
SCORE_MIN: float = -1.0
SCORE_MAX: float = 1.0

# News-volume z-scores are clamped to ±this before being normalized to [-1, 1].
NEWS_VOLUME_ZSCORE_CLAMP: float = 3.0

# |score| below this collapses the action to "hold".
ACTION_HOLD_THRESHOLD: float = 0.2

# Cascade (secondary-impact) signal defaults.
CASCADE_SENTIMENT_MAGNITUDE: float = 0.6
CASCADE_ACTION_THRESHOLD: float = 0.4

# ── Human-readable signal label bands (by score, descending) ─────────────────
# (lower_bound_inclusive, label) — first band whose bound the score meets wins.
SIGNAL_LABEL_BANDS: list[tuple[float, str]] = [
    (0.6, "🟢 STRONG BUY"),
    (0.4, "🟢 BUY"),
    (0.2, "🟡 WEAK BUY"),
    (-0.2, "⚪ HOLD"),
    (-0.4, "🟡 WEAK SELL"),
    (-0.6, "🔴 SELL"),
]
SIGNAL_LABEL_STRONG_SELL: str = "🔴 STRONG SELL"

# ── Source credibility by tier (1=Reuters/Bloomberg, 2=industry, 3=blog) ─────
SOURCE_TIER_CREDIBILITY: dict[int, float] = {1: 1.0, 2: 0.5, 3: 0.2}
DEFAULT_SOURCE_CREDIBILITY: float = 0.2

# ── Price confirmation ───────────────────────────────────────────────────────
# Daily % move that counts as full (±1.0) confirmation of the sentiment.
PRICE_CONFIRMATION_SCALE_PCT: float = 3.0

# ── Fetch / prefilter ────────────────────────────────────────────────────────
FETCH_LOOKBACK_HOURS: int = 1
PREFILTER_SENTIMENT_THRESHOLD: float = 0.6

# ── Telegram notifier defaults ───────────────────────────────────────────────
DEFAULT_ALERT_COOLDOWN_MINUTES: int = 30
DEFAULT_MAX_ALERTS_PER_DAY: int = 20
