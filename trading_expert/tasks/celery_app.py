"""Celery app configuration for the Trading Expert.

Celery handles:
- Intraday news fetching and analysis (every 15 min during market hours)
- Daily summary generation (at market close)
- Retries for failed API calls
- Rate limiting for external APIs

Run workers:
    celery -A trading_expert.tasks.celery_app worker --loglevel=info

Run scheduler:
    celery -A trading_expert.tasks.celery_app beat --loglevel=info
"""

import os
from celery import Celery
from celery.schedules import crontab

# Redis URL from environment or default
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

celery_app = Celery(
    "trading_expert",
    broker=REDIS_URL,
    backend=REDIS_URL,
    include=[
        "trading_expert.tasks.intraday",
        "trading_expert.tasks.daily",
    ],
)

# ── Celery Configuration ─────────────────────────────────────────────────

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="America/New_York",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=10 * 60,  # 10 min max per task
    task_soft_time_limit=8 * 60,  # 8 min soft limit
    worker_concurrency=2,
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
)

# ── Beat Schedule ────────────────────────────────────────────────────────

celery_app.conf.beat_schedule = {
    # 24/7 monitoring: fetch and analyze news every 30 minutes, around the clock
    # Covers Asian (TSMC, Samsung), European (ASML, Infineon), and US markets
    "intraday-fetch": {
        "task": "trading_expert.tasks.intraday.intraday_fetch",
        "schedule": crontab(minute="*/30", hour="*", day_of_week="*"),
        "options": {"expires": 29 * 60},  # Expire after 29 min
    },
    # Daily summary: 30 minutes after US market close (ET)
    "daily-summary": {
        "task": "trading_expert.tasks.daily.daily_summary",
        "schedule": crontab(minute="30", hour="16", day_of_week="1-5"),
        "options": {"expires": 60 * 60},
    },
    # Signal outcome check: once per day (checks past signals)
    "outcome-check": {
        "task": "trading_expert.tasks.daily.check_signal_outcomes",
        "schedule": crontab(minute="0", hour="6", day_of_week="*"),
        "options": {"expires": 30 * 60},
    },
}
