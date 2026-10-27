"""Daily tasks — summary report and signal outcome checking."""

import asyncio
import logging
from datetime import date, datetime, timedelta, timezone

from celery import shared_task

from trading_expert.models import get_session, init_db
from trading_expert.models.signal import Signal
from trading_expert.fetchers.prices import PriceFetcher
from trading_expert.portfolio.backtest import grade_signal_outcomes
from trading_expert.notifications.telegram import TelegramNotifier
from trading_expert.analysis.signals import signal_label

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=1, default_retry_delay=300)
def daily_summary(self):
    """Send the daily trading summary via Telegram.

    Scheduled at market close (4:30 PM ET). Aggregates all signals
    from today, ranks them, and sends a comprehensive report.
    """
    logger.info("Generating daily summary...")
    return asyncio.run(_daily_summary_async())


async def _daily_summary_async():
    """Async implementation of daily summary generation."""
    init_db()
    session = get_session()

    try:
        # Get today's signals
        today_start = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        signals = (
            session.query(Signal)
            .filter(Signal.created_at >= today_start)
            .filter(Signal.cascade_source == False)  # Only primary signals
            .order_by(Signal.score.desc())
            .all()
        )

        if not signals:
            logger.info("No signals generated today")
            return {"status": "ok", "signals": 0}

        # Format and send
        notifier = TelegramNotifier()
        signal_dicts = [
            {
                "ticker": s.ticker,
                "score": s.score,
                "action": s.action,
                "alert_label": signal_label(s.score),
                "confidence": s.deepseek_confidence,
            }
            for s in signals[:20]  # Top 20
        ]

        await notifier.send_daily_summary(signal_dicts)

        logger.info(f"Daily summary sent with {len(signal_dicts)} signals")
        return {"status": "ok", "signals": len(signal_dicts)}

    except Exception as e:
        logger.error(f"Daily summary failed: {e}", exc_info=True)
        raise
    finally:
        session.close()


@shared_task(bind=True, max_retries=1)
def check_signal_outcomes(self):
    """Check past signals against actual price movements.

    Runs once per day. Records each signal's 1/7/30/90-day close (the close on
    the horizon date, from daily history) and whether the direction was correct.
    """
    logger.info("Checking signal outcomes...")
    return asyncio.run(_check_outcomes_async())


async def _check_outcomes_async():
    """Async implementation of signal outcome checking."""
    init_db()
    session = get_session()
    price_fetcher = PriceFetcher()

    try:
        outcomes_checked = await grade_signal_outcomes(session, price_fetcher)
        session.commit()
        logger.info(f"Outcomes checked: {outcomes_checked} signals updated")
        return {"status": "ok", "outcomes_checked": outcomes_checked}

    except Exception as e:
        logger.error(f"Outcome check failed: {e}", exc_info=True)
        session.rollback()
        raise
    finally:
        session.close()


@shared_task(bind=True, max_retries=1)
def recalibrate_weights(self):
    """Refit scorer weights from realized outcomes and save the overlay.

    Runs weekly. For each universe it fits weights from graded signals and,
    when there is enough data and the fitted model discriminates better than a
    coin flip (AUC > 0.5), saves them as the learned-weights overlay the
    intraday tasks apply. Otherwise it just logs the report and changes nothing.
    """
    from trading_expert.analysis.calibration import calibrate, save_learned_weights

    logger.info("Recalibrating scorer weights from outcomes...")
    init_db()
    session = get_session()
    applied: dict[str, bool] = {}
    try:
        for universe in ("semiconductor", "bmv"):
            report = calibrate(session, universe)
            logger.info(
                "Calibration %s: n=%d fitted=%s auc=%s decay=%s",
                universe, report.n_samples, report.fitted, report.auc, report.decay_ratio,
            )
            if report.fitted and report.auc > 0.5:
                save_learned_weights(
                    universe,
                    report.suggested_weights,
                    meta={
                        "n_samples": report.n_samples,
                        "auc": report.auc,
                        "base_rate": report.base_rate,
                    },
                )
                applied[universe] = True
            else:
                applied[universe] = False
        return {"status": "ok", "applied": applied}
    except Exception as e:
        logger.error(f"Recalibration failed: {e}", exc_info=True)
        raise
    finally:
        session.close()
