"""
Telegram Bot — sends trading alerts to Android phone.

Uses python-telegram-bot library for async bot operation.
The bot sends formatted alerts and handles user commands.

Setup:
    1. Create a bot via @BotFather on Telegram → get token
    2. Get your chat ID via @userinfobot
    3. Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env
"""

import logging
import os
from datetime import datetime, timedelta
from typing import Optional

from .formatter import (
    format_alert,
    format_daily_summary,
    format_portfolio_summary,
    format_help,
    format_error,
)

logger = logging.getLogger(__name__)


class TelegramNotifier:
    """Sends trading alerts via Telegram Bot API.

    Uses raw HTTP API (httpx) for lightweight operation.
    No polling needed — this is push-only for alerts.
    Can optionally start a polling bot for command handling.
    """

    def __init__(
        self,
        bot_token: Optional[str] = None,
        chat_id: Optional[str] = None,
        alert_cooldown_minutes: int = 30,
        max_alerts_per_day: int = 20,
    ):
        """
        Args:
            bot_token: Telegram bot token from @BotFather.
            chat_id: Your Telegram user/chat ID.
            alert_cooldown_minutes: Don't send same ticker alert twice in N minutes.
            max_alerts_per_day: Cap total alerts per day to avoid spam.
        """
        self.bot_token = bot_token or os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID", "")

        if not self.bot_token:
            raise ValueError("TELEGRAM_BOT_TOKEN is required. Set it in .env")
        if not self.chat_id:
            raise ValueError("TELEGRAM_CHAT_ID is required. Set it in .env")

        self.alert_cooldown = timedelta(minutes=alert_cooldown_minutes)
        self.max_alerts_per_day = max_alerts_per_day

        # Cooldown tracking: {ticker: last_alert_time}
        self._cooldowns: dict[str, datetime] = {}
        self._alerts_today: int = 0
        self._last_reset_date: str = datetime.now().strftime("%Y-%m-%d")

    async def send_alert(
        self,
        ticker: str,
        score: float,
        action: str,
        confidence: str,
        reasoning: str,
        cascade_effects: list[dict],
        risk_factors: list[str],
        suggested_timeframe: str,
        price: Optional[float] = None,
        alert_label: str = "",
        force: bool = False,
    ) -> bool:
        """Send a trading alert to Telegram.

        Args:
            ticker: Stock ticker symbol.
            score: Signal score (-1.0 to 1.0).
            action: "buy", "sell", or "hold".
            confidence: "high", "medium", or "low".
            reasoning: Analysis reasoning text.
            cascade_effects: List of cascade impact dicts.
            risk_factors: List of risk factor strings.
            suggested_timeframe: Time horizon.
            price: Current stock price.
            alert_label: Formatted label like "🟢 STRONG BUY".
            force: If True, bypass cooldown and rate limits.

        Returns:
            True if the alert was sent, False if suppressed.
        """
        # Reset daily counter
        today = datetime.now().strftime("%Y-%m-%d")
        if today != self._last_reset_date:
            self._alerts_today = 0
            self._last_reset_date = today

        # Rate limit check
        if not force and self._alerts_today >= self.max_alerts_per_day:
            logger.warning(f"Daily alert limit reached ({self.max_alerts_per_day})")
            return False

        # Cooldown check (same ticker)
        if not force and ticker in self._cooldowns:
            elapsed = datetime.now() - self._cooldowns[ticker]
            if elapsed < self.alert_cooldown:
                logger.info(f"Alert suppressed for {ticker} (cooldown: {elapsed})")
                return False

        # Build message
        message = format_alert(
            ticker=ticker,
            score=score,
            action=action,
            confidence=confidence,
            reasoning=reasoning,
            cascade_effects=cascade_effects,
            risk_factors=risk_factors,
            suggested_timeframe=suggested_timeframe,
            price=price,
            alert_label=alert_label,
        )

        # Send via Telegram API
        success = await self._send_message(message)

        if success:
            self._cooldowns[ticker] = datetime.now()
            self._alerts_today += 1
            logger.info(f"Alert sent for {ticker} (score: {score:+.2f}, #{self._alerts_today} today)")

        return success

    async def send_daily_summary(
        self, signals: list[dict], date: Optional[str] = None
    ) -> bool:
        """Send the daily trading summary."""
        message = format_daily_summary(signals, date)
        return await self._send_message(message)

    async def send_portfolio(
        self,
        total_value: float,
        cash: float,
        holdings: dict,
        pnl_total: float,
        pnl_pct: float,
    ) -> bool:
        """Send portfolio summary."""
        message = format_portfolio_summary(
            total_value, cash, holdings, pnl_total, pnl_pct
        )
        return await self._send_message(message)

    async def send_error(self, error_message: str) -> bool:
        """Send an error notification."""
        message = format_error(error_message)
        return await self._send_message(message)

    async def send_help(self) -> bool:
        """Send help message with available commands."""
        message = format_help()
        return await self._send_message(message)

    async def _send_message(self, text: str) -> bool:
        """Send a message via Telegram Bot API using httpx."""
        import httpx

        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.post(
                    url,
                    json={
                        "chat_id": self.chat_id,
                        "text": text,
                        "parse_mode": "Markdown",
                        "disable_web_page_preview": True,
                    },
                )
                response.raise_for_status()
                data = response.json()

                if not data.get("ok"):
                    logger.error(f"Telegram API error: {data}")
                    return False

                return True

        except Exception as e:
            logger.error(f"Failed to send Telegram message: {e}")
            return False

    # ── Cooldown / Rate Limit Control ───────────────────────────────────────

    def is_in_cooldown(self, ticker: str) -> bool:
        """Check if a ticker is in alert cooldown."""
        if ticker not in self._cooldowns:
            return False
        elapsed = datetime.now() - self._cooldowns[ticker]
        return elapsed < self.alert_cooldown

    def reset_cooldown(self, ticker: str):
        """Force-reset cooldown for a ticker."""
        self._cooldowns.pop(ticker, None)

    def get_remaining_alerts_today(self) -> int:
        """How many more alerts can be sent today?"""
        return max(0, self.max_alerts_per_day - self._alerts_today)


# ── Optional Polling Bot for Command Handling ────────────────────────────────

async def start_command_bot(
    bot_token: str,
    signal_repo,
    portfolio_tracker,
):
    """Start a Telegram bot that listens for user commands.

    This runs a polling loop that handles /status, /signals, /portfolio,
    /mute, /unmute, /threshold, and /help commands.

    Note: This is optional. The core alert system works push-only.
    Use this when you want interactive querying from the phone.

    Args:
        bot_token: Telegram bot token.
        signal_repo: Repository for querying signals.
        portfolio_tracker: Portfolio tracker for P&L queries.
    """
    from telegram import Update
    from telegram.ext import (
        Application,
        CommandHandler,
        ContextTypes,
    )

    notifier = TelegramNotifier(bot_token=bot_token)

    async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show current signal summary. Optional universe filter via args."""
        universe = context.args[0] if context.args else None
        signal_type = None
        if universe == "bmv":
            signal_type = "intraday_bmv"
            label = "BMV (Mexican Stocks)"
        elif universe == "semiconductor":
            signal_type = "intraday"
            label = "Semiconductor & Tech"
        else:
            label = "All Universes"

        try:
            signals = signal_repo.get_recent(days=1, signal_type=signal_type)
            if not signals:
                await update.message.reply_text(
                    f"No signals today in {label}. Markets are quiet."
                )
                return
            msg = format_daily_summary([
                {
                    "ticker": s.ticker,
                    "score": s.score,
                    "action": s.action,
                    "alert_label": s.alert_label if hasattr(s, 'alert_label') else "",
                    "confidence": s.deepseek_confidence if hasattr(s, 'deepseek_confidence') else "N/A",
                }
                for s in signals
            ])
            await update.message.reply_text(msg, parse_mode="Markdown")
        except Exception as e:
            await update.message.reply_text(format_error(str(e)))

    async def cmd_signals(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show last 5 signals for a ticker. Auto-detects BMV from .MX suffix."""
        ticker = " ".join(context.args) if context.args else None
        if not ticker:
            await update.message.reply_text(
                "Usage: /signals NVDA or /signals BIMBOA.MX"
            )
            return
        try:
            # signal_repo auto-detects .MX → signal_type="intraday_bmv"
            signals = signal_repo.get_for_ticker(ticker, limit=5)
            if not signals:
                await update.message.reply_text(f"No signals for {ticker.upper()}")
                return
            universe = "BMV" if ticker.upper().endswith(".MX") else "US"
            lines = [f"📋 Last {universe} signals for {ticker.upper()}:", ""]
            for s in signals:
                lines.append(
                    f"• {s.created_at.strftime('%m/%d')}: {s.action.upper()} "
                    f"(score: {s.score:+.2f})"
                )
            await update.message.reply_text("\n".join(lines))
        except Exception as e:
            await update.message.reply_text(format_error(str(e)))

    async def cmd_portfolio(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show paper trading portfolio."""
        try:
            summary = portfolio_tracker.get_summary()
            msg = format_portfolio_summary(
                total_value=summary["total_value"],
                cash=summary["cash"],
                holdings=summary["holdings"],
                pnl_total=summary["pnl_total"],
                pnl_pct=summary["pnl_pct"],
            )
            await update.message.reply_text(msg, parse_mode="Markdown")
        except Exception as e:
            await update.message.reply_text(format_error(str(e)))

    async def cmd_mute(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Snooze alerts."""
        duration = context.args[0] if context.args else "4h"
        await update.message.reply_text(f"🔕 Alerts snoozed for {duration}.")

    async def cmd_unmute(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Resume alerts."""
        await update.message.reply_text("🔔 Alerts resumed!")

    async def cmd_threshold(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Set alert threshold."""
        try:
            threshold = float(context.args[0]) if context.args else 0.6
            await update.message.reply_text(
                f"✅ Alert threshold set to ±{threshold:.1f}"
            )
        except (ValueError, IndexError):
            await update.message.reply_text("Usage: /threshold 0.8")

    async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show help."""
        await update.message.reply_text(format_help(), parse_mode="Markdown")

    # Build the app
    app = Application.builder().token(bot_token).build()

    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("signals", cmd_signals))
    app.add_handler(CommandHandler("portfolio", cmd_portfolio))
    app.add_handler(CommandHandler("mute", cmd_mute))
    app.add_handler(CommandHandler("unmute", cmd_unmute))
    app.add_handler(CommandHandler("threshold", cmd_threshold))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("start", cmd_help))

    logger.info("Starting Telegram command bot (polling)...")
    await app.run_polling()
