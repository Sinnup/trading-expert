"""Weight calibration — let the outcome data set the scorer weights.

The signal scorer blends four factors (DeepSeek sentiment, news-volume surprise,
source credibility, price confirmation) with hand-picked weights. Those weights
were guesses. This module closes the loop: it reads realized signal outcomes
from ``signal_outcomes``, fits a logistic model that predicts whether a signal's
directional call was *correct*, and reports:

- **discrimination** — do high-scoring signals actually outperform low ones? (AUC)
- **calibration** — when we emit "+0.7", does it come true ~70% of the time? (Brier + reliability bins)
- **decay** — how much of the 7-day move already happened on day 1? (early-alpha ratio)
- **suggested weights** — factor importances derived from the fitted model,
  normalized to sum to 1 so they drop straight into ``signals.weights``.

The fit is a linear logistic approximation of a scorer that is mildly non-linear
(credibility amplifies sentiment; price confirmation gates the result), so the
suggested weights are an importance ranking to review — not a black-box
overwrite. ``save_learned_weights`` persists them as an overlay the tasks apply
on top of the YAML defaults, keeping the hand-tuned config pristine.
"""

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
from sqlalchemy.orm import Session

from trading_expert.constants import DEFAULT_WEIGHTS
from trading_expert.models.portfolio import SignalOutcome
from trading_expert.models.signal import Signal

logger = logging.getLogger(__name__)

# Feature order is fixed so coefficients line up with the config weight keys.
FEATURE_KEYS: list[str] = [
    "deepseek_sentiment",
    "news_volume",
    "source_credibility",
    "price_confirmation",
]

# Signal types that carry a full factor breakdown, per universe. Cascade signals
# are excluded — they use a fixed sentiment magnitude, not the four factors.
UNIVERSE_SIGNAL_TYPES: dict[str, tuple[str, ...]] = {
    "semiconductor": ("intraday",),
    "bmv": ("intraday_bmv",),
}

_DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data")


# ── Persistence: learned-weights overlay ─────────────────────────────────────

def learned_weights_path(universe: str) -> str:
    """Path to the learned-weights overlay for a universe."""
    return os.path.join(_DATA_DIR, f"learned_weights_{universe}.json")


def load_learned_weights(universe: str) -> Optional[dict[str, float]]:
    """Load the learned-weights overlay for a universe, or None if absent."""
    path = learned_weights_path(universe)
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            data = json.load(f)
        weights = data.get("weights", data)
        return {k: float(v) for k, v in weights.items() if k in FEATURE_KEYS}
    except (json.JSONDecodeError, ValueError, OSError) as e:
        logger.warning("Could not read learned weights at %s: %s", path, e)
        return None


def save_learned_weights(universe: str, weights: dict[str, float], meta: dict) -> str:
    """Persist a learned-weights overlay. Returns the file path written."""
    os.makedirs(_DATA_DIR, exist_ok=True)
    path = learned_weights_path(universe)
    with open(path, "w") as f:
        json.dump({"weights": weights, "meta": meta}, f, indent=2)
    logger.info("Saved learned weights for %s → %s", universe, path)
    return path


def merge_weights(base: dict[str, float], overlay: Optional[dict[str, float]]) -> dict[str, float]:
    """Overlay learned weights on top of the config defaults."""
    merged = dict(base)
    if overlay:
        merged.update(overlay)
    return merged


# ── Training data ─────────────────────────────────────────────────────────────

@dataclass
class TrainingRow:
    """One scored signal with a realized 7-day outcome."""
    features: dict[str, float]
    score: float
    return_7d_pct: float
    return_1d_pct: Optional[float]

    @property
    def correct(self) -> int:
        """1 if the signal's direction matched the realized 7-day move."""
        return 1 if self.score * self.return_7d_pct > 0 else 0


def load_training_data(session: Session, universe: str) -> list[TrainingRow]:
    """Load scored signals with realized 7-day outcomes for a universe."""
    signal_types = UNIVERSE_SIGNAL_TYPES.get(universe, ("intraday",))
    rows = (
        session.query(Signal, SignalOutcome)
        .join(SignalOutcome, SignalOutcome.signal_id == Signal.id)
        .filter(Signal.signal_type.in_(signal_types))
        .filter(SignalOutcome.return_7d_pct.isnot(None))
        .all()
    )

    training: list[TrainingRow] = []
    for signal, outcome in rows:
        factors = signal.factors or {}
        if not all(k in factors for k in FEATURE_KEYS):
            continue  # signal predates factor tracking
        if signal.score == 0 or outcome.return_7d_pct == 0:
            continue  # no directional call / no move to grade
        training.append(
            TrainingRow(
                features={k: float(factors[k]) for k in FEATURE_KEYS},
                score=float(signal.score),
                return_7d_pct=float(outcome.return_7d_pct),
                return_1d_pct=(
                    float(outcome.return_1d_pct)
                    if outcome.return_1d_pct is not None
                    else None
                ),
            )
        )
    return training


# ── Logistic regression (pure numpy) ─────────────────────────────────────────

def _sigmoid(z: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(z, -30, 30)))


def _fit_logistic(
    X: np.ndarray,
    y: np.ndarray,
    l2: float = 1.0,
    lr: float = 0.1,
    epochs: int = 5000,
) -> tuple[np.ndarray, float]:
    """Fit L2-regularized logistic regression by gradient descent.

    Args:
        X: Standardized feature matrix (n, k).
        y: Binary targets (n,).
        l2: Ridge penalty (not applied to the intercept).
        lr: Learning rate.
        epochs: Gradient steps.

    Returns:
        (coefficients (k,), intercept). Coefficients are in standardized space,
        so their magnitudes are directly comparable as importances.
    """
    n, k = X.shape
    w = np.zeros(k)
    b = 0.0
    for _ in range(epochs):
        p = _sigmoid(X @ w + b)
        error = p - y
        grad_w = X.T @ error / n + l2 * w / n
        grad_b = error.mean()
        w -= lr * grad_w
        b -= lr * grad_b
    return w, b


def _auc(y: np.ndarray, scores: np.ndarray) -> float:
    """Rank-based ROC AUC. Returns 0.5 when one class is absent."""
    pos = scores[y == 1]
    neg = scores[y == 0]
    if len(pos) == 0 or len(neg) == 0:
        return 0.5
    # Mann–Whitney U via rank sum.
    order = np.argsort(scores)
    ranks = np.empty(len(scores), dtype=float)
    ranks[order] = np.arange(1, len(scores) + 1)
    # Average ranks for ties.
    _, inv, counts = np.unique(scores, return_inverse=True, return_counts=True)
    tie_mean = np.zeros(len(counts))
    np.add.at(tie_mean, inv, ranks)
    tie_mean /= counts
    ranks = tie_mean[inv]
    r_pos = ranks[y == 1].sum()
    n_pos, n_neg = len(pos), len(neg)
    return (r_pos - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg)


# ── Report ────────────────────────────────────────────────────────────────────

@dataclass
class CalibrationReport:
    """Result of a calibration run."""
    universe: str
    n_samples: int
    base_rate: float = 0.0            # fraction of signals that were directionally correct
    auc: float = 0.5                  # discrimination
    brier: float = 0.0                # calibration error (lower is better)
    reliability: list[dict] = field(default_factory=list)   # bins: predicted vs actual
    coefficients: dict[str, float] = field(default_factory=dict)  # standardized logistic coefs
    suggested_weights: dict[str, float] = field(default_factory=dict)
    current_weights: dict[str, float] = field(default_factory=dict)
    decay_ratio: Optional[float] = None   # mean|1d move| / mean|7d move|
    decay_n: int = 0
    fitted: bool = False              # False when too few samples to fit
    message: str = ""


def calibrate(session: Session, universe: str, min_samples: int = 30) -> CalibrationReport:
    """Fit factor weights from realized outcomes and score the current model."""
    current_weights = {k: DEFAULT_WEIGHTS[k] for k in FEATURE_KEYS}
    rows = load_training_data(session, universe)
    report = CalibrationReport(
        universe=universe,
        n_samples=len(rows),
        current_weights=current_weights,
    )

    # Decay: how much of the 7-day move already showed up on day 1?
    decay_pairs = [
        (abs(r.return_1d_pct), abs(r.return_7d_pct))
        for r in rows
        if r.return_1d_pct is not None and r.return_7d_pct != 0
    ]
    if decay_pairs:
        mean_1d = float(np.mean([d[0] for d in decay_pairs]))
        mean_7d = float(np.mean([d[1] for d in decay_pairs]))
        report.decay_n = len(decay_pairs)
        report.decay_ratio = round(mean_1d / mean_7d, 3) if mean_7d else None

    if len(rows) < min_samples:
        report.message = (
            f"Only {len(rows)} graded signals — need >= {min_samples} to fit weights. "
            "Keep collecting outcomes."
        )
        return report

    X = np.array([[r.features[k] for k in FEATURE_KEYS] for r in rows], dtype=float)
    y = np.array([r.correct for r in rows], dtype=float)
    report.base_rate = round(float(y.mean()), 3)

    # Standardize so coefficients are comparable importances; guard zero-variance.
    mu = X.mean(axis=0)
    sigma = X.std(axis=0)
    sigma_safe = np.where(sigma == 0, 1.0, sigma)
    Xs = (X - mu) / sigma_safe

    w, b = _fit_logistic(Xs, y)
    probs = _sigmoid(Xs @ w + b)

    report.auc = round(float(_auc(y, probs)), 3)
    report.brier = round(float(np.mean((probs - y) ** 2)), 3)
    report.coefficients = {k: round(float(c), 4) for k, c in zip(FEATURE_KEYS, w)}

    # Reliability bins (predicted probability vs actual hit rate).
    bins = np.linspace(0, 1, 6)
    idx = np.clip(np.digitize(probs, bins) - 1, 0, len(bins) - 2)
    for b_i in range(len(bins) - 1):
        mask = idx == b_i
        if mask.sum() == 0:
            continue
        report.reliability.append({
            "range": f"{bins[b_i]:.1f}-{bins[b_i + 1]:.1f}",
            "n": int(mask.sum()),
            "predicted": round(float(probs[mask].mean()), 3),
            "actual": round(float(y[mask].mean()), 3),
        })

    # Suggested weights: normalize absolute standardized coefficients. A factor
    # with a near-zero coefficient contributed little to correct calls.
    abs_coefs = np.abs(w)
    total = abs_coefs.sum()
    if total > 0:
        report.suggested_weights = {
            k: round(float(c / total), 3) for k, c in zip(FEATURE_KEYS, abs_coefs)
        }
        report.fitted = True
    else:
        report.suggested_weights = dict(current_weights)
        report.message = "All coefficients ~0 — factors show no signal; keeping current weights."

    return report
