"""BMV daily tasks — summary report and signal outcome checking.

Runs independently from the semiconductor daily tasks. Filters signals
by signal_type="intraday_bmv" to keep universes separate.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from celery import shared_task

from trading_expert.models import get_session, init_db
from trading_expert.models.signal import Signal
from trading_expert.models.portfolio import SignalOutcome
from trading_expert.fetchers.prices import PriceFetcher
from trading_expert.notifications.telegram import TelegramNotifier

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=1, default_retry_delay=300)
def daily_summary_bmv(self):
    """Send the daily BMV trading summary via Telegram.

    Scheduled at BMV close (3:15 PM Mexico City, ~21:15 UTC).
    Aggregates all BMV signals from today, ranks them, and sends
    a comprehensive report.
    """
    logger.info("Generating BMV daily summary...")
    return asyncio.run(_daily_summary_bmv_async())


async def _daily_summary_bmv_async():
    """Async implementation of BMV daily summary generation."""
    init_db()
    session = get_session()

    try:
        # Get today's BMV signals (primary only — BMV has no cascade)
        today_start = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        signals = (
            session.query(Signal)
            .filter(Signal.created_at >= today_start)
            .filter(Signal.signal_type == "intraday_bmv")
            .order_by(Signal.score.desc())
            .all()
        )

        if not signals:
            logger.info("No BMV signals generated today")
            return {"status": "ok", "signals": 0}

        # Format and send
        notifier = TelegramNotifier()
        signal_dicts = [
            {
                "ticker": s.ticker,
                "score": s.score,
                "action": s.action,
                "alert_label": _get_label(s.score),
                "confidence": s.deepseek_confidence,
            }
            for s in signals[:20]  # Top 20
        ]

        await notifier.send_daily_summary(signal_dicts)

        logger.info(f"BMV daily summary sent with {len(signal_dicts)} signals")
        return {"status": "ok", "signals": len(signal_dicts)}

    except Exception as e:
        logger.error(f"BMV daily summary failed: {e}", exc_info=True)
        raise
    finally:
        session.close()


def _get_label(score: float) -> str:
    """Get signal label from score."""
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
