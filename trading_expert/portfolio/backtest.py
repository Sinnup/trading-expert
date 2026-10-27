"""Signal backtesting — measure signal accuracy over time.

Checks past trading signals against actual price outcomes at
7, 30, and 90 days to determine prediction accuracy.

The learning loop uses this data to adjust signal scorer weights
over time, rewarding factors that correlate with correct predictions.
"""

import logging
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import or_
from sqlalchemy.orm import Session

from trading_expert.analysis.returns import (
    as_utc,
    base_price_is_plausible,
    close_at_horizon,
    close_on_signal_date,
    pct_return,
)
from trading_expert.constants import (
    ACTION_BUY,
    ACTION_SELL,
    GRADING_HORIZON_DAYS,
    MAX_BASE_PRICE_DEVIATION,
    OUTCOME_HORIZONS,
)
from trading_expert.models import get_session
from trading_expert.models.signal import Signal
from trading_expert.models.portfolio import SignalOutcome
from trading_expert.fetchers.prices import DailyHistory, PriceFetcher

logger = logging.getLogger(__name__)

_GRADING_RETURN_ATTR = next(
    ret for days, _, ret in OUTCOME_HORIZONS if days == GRADING_HORIZON_DAYS
)


def apply_outcome(
    outcome: SignalOutcome,
    signal: Signal,
    history: DailyHistory,
    today: date,
) -> bool:
    """Fill every elapsed, still-empty horizon on ``outcome``; grade direction.

    Each horizon's price is the close of the first completed session on/after
    ``signal date + N`` calendar days — independent of when the grader runs —
    and its return is ``(price_N - price_at_signal) / price_at_signal * 100``.
    ``was_correct`` compares the sign of the grading-horizon return with the
    action (buy → up, sell → down; hold stays ungraded).

    A ``price_at_signal`` that doesn't match the signal-date close (see
    ``base_price_is_plausible``) is left ungraded rather than producing a
    bogus return.

    Returns True if anything changed.
    """
    base = signal.price_at_signal
    reference = close_on_signal_date(history.closes, history.splits, signal.created_at)
    if not base_price_is_plausible(base, reference, MAX_BASE_PRICE_DEVIATION):
        logger.debug(
            f"Signal {signal.id} {signal.ticker}: price_at_signal {base} vs "
            f"signal-date close {reference} — implausible base, not graded"
        )
        return False
    changed = False
    for days, price_attr, return_attr in OUTCOME_HORIZONS:
        if getattr(outcome, price_attr) is not None:
            continue
        price = close_at_horizon(
            history.closes, history.splits, signal.created_at, days, today
        )
        if price is None:
            continue
        setattr(outcome, price_attr, price)
        setattr(outcome, return_attr, pct_return(base, price))
        changed = True

    graded_return = getattr(outcome, _GRADING_RETURN_ATTR)
    if graded_return is not None:
        if signal.action == ACTION_BUY:
            outcome.was_correct = graded_return > 0
        elif signal.action == ACTION_SELL:
            outcome.was_correct = graded_return < 0
    return changed


def _new_outcome(signal: Signal) -> SignalOutcome:
    return SignalOutcome(
        signal_id=signal.id,
        ticker=signal.ticker,
        signal_score=signal.score,
        price_at_signal=signal.price_at_signal,
    )


async def grade_signal_outcomes(
    session: Session,
    price_fetcher: PriceFetcher,
    now: Optional[datetime] = None,
) -> int:
    """Record 1/7/30/90-day outcomes for every signal still missing one.

    Only signals with a usable ``price_at_signal`` and at least one horizon
    still open are loaded (so fully graded history never starves new
    signals), and price history is fetched once per ticker.

    Returns the number of outcome rows updated. Caller commits.
    """
    now = as_utc(now or datetime.now(timezone.utc))
    today = now.date()
    min_age = now - timedelta(days=min(days for days, _, _ in OUTCOME_HORIZONS))

    rows = (
        session.query(Signal, SignalOutcome)
        .outerjoin(SignalOutcome, SignalOutcome.signal_id == Signal.id)
        .filter(Signal.created_at < min_age.replace(tzinfo=None))
        .filter(Signal.is_active == True)  # noqa: E712
        .filter(Signal.price_at_signal > 0)
        .filter(or_(
            SignalOutcome.id.is_(None),
            *(getattr(SignalOutcome, price_attr).is_(None)
              for _, price_attr, _ in OUTCOME_HORIZONS),
        ))
        .all()
    )

    by_ticker: dict[str, list[tuple[Signal, Optional[SignalOutcome]]]] = defaultdict(list)
    for signal, outcome in rows:
        if signal.ticker:
            by_ticker[signal.ticker].append((signal, outcome))

    updated = 0
    for ticker, pairs in by_ticker.items():
        # A week of lead-in so the signal-date reference close always exists.
        start = min(as_utc(s.created_at) for s, _ in pairs).date() - timedelta(days=7)
        history = await price_fetcher.get_daily_history(ticker, start)
        if history is None or not history.closes:
            continue
        for signal, outcome in pairs:
            outcome = outcome or _new_outcome(signal)
            if apply_outcome(outcome, signal, history, today):
                if outcome.id is None:
                    session.add(outcome)
                outcome.outcome_checked_at = now
                updated += 1
    return updated


class BacktestEngine:
    """Checks signal accuracy against historical price data."""

    def __init__(self):
        self.price_fetcher = PriceFetcher()

    async def check_signal(self, signal_id: int) -> Optional[SignalOutcome]:
        """Grade a single signal's outcome from its price history."""
        session = get_session()

        try:
            signal = session.get(Signal, signal_id)
            if not signal:
                logger.warning(f"Signal {signal_id} not found")
                return None
            if not signal.price_at_signal or signal.price_at_signal <= 0:
                logger.warning(f"Signal {signal_id} has no price_at_signal")
                return None

            outcome = (
                session.query(SignalOutcome)
                .filter(SignalOutcome.signal_id == signal_id)
                .first()
            )
            if outcome is None:
                outcome = _new_outcome(signal)
                session.add(outcome)

            history = await self.price_fetcher.get_daily_history(
                signal.ticker, as_utc(signal.created_at).date() - timedelta(days=7)
            )
            if history is None or not history.closes:
                logger.warning(f"No price data for {signal.ticker}")
                return None

            now = datetime.now(timezone.utc)
            apply_outcome(outcome, signal, history, now.date())
            outcome.outcome_checked_at = now
            session.commit()
            session.refresh(outcome)
            return outcome

        except Exception as e:
            session.rollback()
            logger.error(f"Backtest check failed for signal {signal_id}: {e}")
            return None
        finally:
            session.close()

    def get_accuracy_stats(self, days: int = 30) -> dict:
        """Get aggregate signal accuracy statistics.

        Args:
            days: Look back window for signals.

        Returns:
            Dict with accuracy metrics:
            {
                "total_checked": 100,
                "correct": 65,
                "incorrect": 30,
                "pending": 5,
                "accuracy_pct": 68.4,
                "by_ticker": {"NVDA": 0.75, ...},
                "by_action": {"buy": 0.70, "sell": 0.65},
                "by_confidence": {"high": 0.80, "medium": 0.65, "low": 0.45},
            }
        """
        session = get_session()

        try:
            # created_at is stored naive-UTC
            cutoff = datetime.utcnow() - timedelta(days=days)

            outcomes = (
                session.query(SignalOutcome)
                .join(Signal, SignalOutcome.signal_id == Signal.id)
                .filter(Signal.created_at >= cutoff)
                .all()
            )

            total = len(outcomes)
            if total == 0:
                return {
                    "total_checked": 0,
                    "correct": 0,
                    "incorrect": 0,
                    "pending": 0,
                    "accuracy_pct": 0,
                    "by_ticker": {},
                    "by_action": {},
                    "by_confidence": {},
                }

            correct = sum(1 for o in outcomes if o.was_correct is True)
            incorrect = sum(1 for o in outcomes if o.was_correct is False)
            pending = sum(1 for o in outcomes if o.was_correct is None)

            # By ticker
            by_ticker: dict[str, list[bool]] = {}
            for o in outcomes:
                if o.ticker not in by_ticker:
                    by_ticker[o.ticker] = []
                if o.was_correct is not None:
                    by_ticker[o.ticker].append(o.was_correct)

            ticker_accuracy = {
                t: sum(v) / len(v) if v else 0
                for t, v in by_ticker.items()
            }

            # By action
            # Join with Signal to get action
            by_action: dict[str, list[bool]] = {}
            for o in outcomes:
                signal = session.get(Signal, o.signal_id)
                action = signal.action if signal else "unknown"
                if action not in by_action:
                    by_action[action] = []
                if o.was_correct is not None:
                    by_action[action].append(o.was_correct)

            action_accuracy = {
                a: sum(v) / len(v) if v else 0
                for a, v in by_action.items()
            }

            # By confidence
            by_confidence: dict[str, list[bool]] = {}
            for o in outcomes:
                signal = session.get(Signal, o.signal_id)
                confidence = (
                    signal.deepseek_confidence if signal else "unknown"
                )
                if confidence not in by_confidence:
                    by_confidence[confidence] = []
                if o.was_correct is not None:
                    by_confidence[confidence].append(o.was_correct)

            confidence_accuracy = {
                c: sum(v) / len(v) if v else 0
                for c, v in by_confidence.items()
            }

            checked = correct + incorrect
            accuracy = (correct / checked * 100) if checked > 0 else 0

            logger.info(
                f"Backtest accuracy: {accuracy:.1f}% "
                f"({correct}/{checked} correct over {days}d)"
            )

            return {
                "total_checked": total,
                "correct": correct,
                "incorrect": incorrect,
                "pending": pending,
                "accuracy_pct": round(accuracy, 1),
                "by_ticker": {t: round(a, 3) for t, a in ticker_accuracy.items()},
                "by_action": {a: round(v, 3) for a, v in action_accuracy.items()},
                "by_confidence": {c: round(v, 3) for c, v in confidence_accuracy.items()},
            }

        finally:
            session.close()
