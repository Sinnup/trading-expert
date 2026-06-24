"""Signal backtesting — measure signal accuracy over time.

Checks past trading signals against actual price outcomes at
7, 30, and 90 days to determine prediction accuracy.

The learning loop uses this data to adjust signal scorer weights
over time, rewarding factors that correlate with correct predictions.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from trading_expert.models import get_session
from trading_expert.models.signal import Signal
from trading_expert.models.portfolio import SignalOutcome
from trading_expert.fetchers.prices import PriceFetcher

logger = logging.getLogger(__name__)


class BacktestEngine:
    """Checks signal accuracy against historical price data."""

    def __init__(self):
        self.price_fetcher = PriceFetcher()

    async def check_signal(
        self, signal_id: int, current_price: Optional[float] = None
    ) -> Optional[SignalOutcome]:
        """Check a single signal's outcome.

        Args:
            signal_id: The signal to check.
            current_price: Current price (fetched if not provided).

        Returns:
            Updated SignalOutcome record.
        """
        session = get_session()

        try:
            signal = session.get(Signal, signal_id)
            if not signal:
                logger.warning(f"Signal {signal_id} not found")
                return None

            # Get or create outcome record
            outcome = (
                session.query(SignalOutcome)
                .filter(SignalOutcome.signal_id == signal_id)
                .first()
            )

            if outcome is None:
                outcome = SignalOutcome(
                    signal_id=signal.id,
                    ticker=signal.ticker,
                    signal_score=signal.score,
                    price_at_signal=signal.price_at_signal or 0,
                )
                session.add(outcome)
                session.flush()

            # Get current price if not provided
            if current_price is None:
                price_data = await self.price_fetcher.get_current(signal.ticker)
                if price_data:
                    current_price = price_data.close

            if current_price is None:
                logger.warning(f"No price data for {signal.ticker}")
                return None

            # Calculate days since signal
            days_since = (datetime.now(timezone.utc) - signal.created_at).days

            # Update price fields based on elapsed time
            if days_since >= 7 and outcome.price_7d is None:
                outcome.price_7d = current_price
                if signal.price_at_signal and signal.price_at_signal > 0:
                    outcome.return_7d_pct = (
                        (current_price - signal.price_at_signal)
                        / signal.price_at_signal
                        * 100
                    )

            if days_since >= 30 and outcome.price_30d is None:
                outcome.price_30d = current_price
                if signal.price_at_signal and signal.price_at_signal > 0:
                    outcome.return_30d_pct = (
                        (current_price - signal.price_at_signal)
                        / signal.price_at_signal
                        * 100
                    )

            if days_since >= 90 and outcome.price_90d is None:
                outcome.price_90d = current_price
                if signal.price_at_signal and signal.price_at_signal > 0:
                    outcome.return_90d_pct = (
                        (current_price - signal.price_at_signal)
                        / signal.price_at_signal
                        * 100
                    )

            # Determine correctness (using 7-day as quick check)
            if outcome.return_7d_pct is not None:
                if signal.action == "buy":
                    outcome.was_correct = outcome.return_7d_pct > 0
                elif signal.action == "sell":
                    outcome.was_correct = outcome.return_7d_pct < 0
                # "hold" signals are neither correct nor incorrect

            outcome.outcome_checked_at = datetime.now(timezone.utc)
            session.commit()

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
            cutoff = datetime.now(timezone.utc) - timedelta(days=days)

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
