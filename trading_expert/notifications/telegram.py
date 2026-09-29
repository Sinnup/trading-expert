"""
Telegram Bot — sends trading alerts to Android phone.

Uses python-telegram-bot library for async bot operation.
The bot sends formatted alerts, handles user commands, and supports
free-text conversations powered by DeepSeek-R1 (deepseek-reasoner).

Setup:
    1. Create a bot via @BotFather on Telegram → get token
    2. Get your chat ID via @userinfobot
    3. Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env
"""

import asyncio
import logging
import os
from datetime import datetime, timedelta
from typing import Optional

from zoneinfo import ZoneInfo

from trading_expert.constants import (
    DEFAULT_ALERT_ACTIONS,
    DEFAULT_ALERT_COOLDOWN_MINUTES,
    DEFAULT_ALERT_END_HOUR,
    DEFAULT_ALERT_START_HOUR,
    DEFAULT_ALERT_TIMEZONE,
    DEFAULT_MAX_ALERTS_PER_DAY,
)
from .formatter import (
    format_alert_concise,
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
        alert_cooldown_minutes: int = DEFAULT_ALERT_COOLDOWN_MINUTES,
        max_alerts_per_day: int = DEFAULT_MAX_ALERTS_PER_DAY,
        alert_start_hour: int = DEFAULT_ALERT_START_HOUR,
        alert_end_hour: int = DEFAULT_ALERT_END_HOUR,
        alert_timezone: str = DEFAULT_ALERT_TIMEZONE,
        alert_actions: tuple[str, ...] = DEFAULT_ALERT_ACTIONS,
    ):
        """
        Args:
            bot_token: Telegram bot token from @BotFather.
            chat_id: Your Telegram user/chat ID.
            alert_cooldown_minutes: Don't send same ticker alert twice in N minutes.
            max_alerts_per_day: Cap total alerts per day to avoid spam.
            alert_start_hour: Earliest local hour (0-23, inclusive) to push alerts.
            alert_end_hour: Latest local hour (0-23, exclusive) to push alerts.
            alert_timezone: IANA timezone name the alert window is measured in.
            alert_actions: Actions worth alerting on (e.g. buy/sell); others dropped.
        """
        self.bot_token = bot_token or os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID", "")

        if not self.bot_token:
            raise ValueError("TELEGRAM_BOT_TOKEN is required. Set it in .env")
        if not self.chat_id:
            raise ValueError("TELEGRAM_CHAT_ID is required. Set it in .env")

        self.alert_cooldown = timedelta(minutes=alert_cooldown_minutes)
        self.max_alerts_per_day = max_alerts_per_day
        self.alert_start_hour = alert_start_hour
        self.alert_end_hour = alert_end_hour
        self.alert_tz = ZoneInfo(alert_timezone)
        self.alert_actions = {a.lower() for a in alert_actions}

        # Cooldown tracking: {ticker: last_alert_time}
        self._cooldowns: dict[str, datetime] = {}
        self._alerts_today: int = 0
        self._last_reset_date: str = datetime.now().strftime("%Y-%m-%d")

    @classmethod
    def from_settings(cls, telegram_config: dict) -> "TelegramNotifier":
        """Build a notifier from the ``telegram`` block of a settings.yaml.

        Unspecified keys fall back to the module-level defaults, so both
        universes can share one construction path.
        """
        cfg = telegram_config or {}
        return cls(
            alert_cooldown_minutes=cfg.get(
                "alert_cooldown_minutes", DEFAULT_ALERT_COOLDOWN_MINUTES
            ),
            max_alerts_per_day=cfg.get(
                "max_alerts_per_day", DEFAULT_MAX_ALERTS_PER_DAY
            ),
            alert_start_hour=cfg.get("alert_start_hour", DEFAULT_ALERT_START_HOUR),
            alert_end_hour=cfg.get("alert_end_hour", DEFAULT_ALERT_END_HOUR),
            alert_timezone=cfg.get("alert_timezone", DEFAULT_ALERT_TIMEZONE),
            alert_actions=tuple(cfg.get("alert_actions", DEFAULT_ALERT_ACTIONS)),
        )

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

        # Action filter: only push what the user acts on (buy/sell), skip holds.
        if not force and action.lower() not in self.alert_actions:
            logger.info(f"Alert suppressed for {ticker}: action '{action}' not alertable")
            return False

        # Trading-hours window: stay silent outside the local alert window.
        if not force and not self._within_alert_window():
            logger.info(
                f"Alert suppressed for {ticker}: outside "
                f"{self.alert_start_hour:02d}:00-{self.alert_end_hour:02d}:00 window"
            )
            return False

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

        # Build message — concise, straight-to-the-point action alert
        message = format_alert_concise(
            ticker=ticker,
            action=action,
            price=price,
            alert_label=alert_label,
            suggested_timeframe=suggested_timeframe,
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

    def _within_alert_window(self) -> bool:
        """True if the current local time is inside the trading-hours window.

        Window is [start_hour, end_hour) in ``self.alert_tz``. When start == end
        the window is treated as always-open.
        """
        if self.alert_start_hour == self.alert_end_hour:
            return True
        hour = datetime.now(self.alert_tz).hour
        return self.alert_start_hour <= hour < self.alert_end_hour

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

def start_command_bot(
    bot_token: str,
    signal_repo,
    portfolio_tracker,
    owner_chat_id: Optional[int | str] = None,
):
    """Start a Telegram bot that listens for user commands and chat messages.

    Handles /status, /signals, /portfolio, /mute, /unmute, /threshold,
    /help, /ask, and /reset commands, plus free-text conversational
    trading questions powered by DeepSeek-R1.

    This is a blocking call — python-telegram-bot manages its own event loop
    internally so it must NOT be called from inside asyncio.run().

    Args:
        bot_token: Telegram bot token.
        signal_repo: Repository for querying signals.
        portfolio_tracker: Portfolio tracker for P&L queries.
        owner_chat_id: If set, only this chat may use the bot — the wallet and
            signals are private, so messages from any other chat are silently
            ignored. If None/empty, the bot is unrestricted (a warning is
            logged) to preserve backwards-compatible behaviour.
    """
    from telegram import Update
    from telegram.ext import (
        Application,
        CommandHandler,
        MessageHandler,
        filters,
        ContextTypes,
    )

    from trading_expert.analysis.chat_advisor import TradingChatAdvisor

    notifier = TelegramNotifier(bot_token=bot_token)
    advisor = TradingChatAdvisor()

    # ── Shared chat helper ───────────────────────────────────────────────────

    async def _run_chat(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
        """Send message to advisor while streaming a typing indicator."""
        chat_id = update.effective_chat.id

        async def _typing_loop():
            while True:
                await context.bot.send_chat_action(chat_id=chat_id, action="typing")
                await asyncio.sleep(4)

        # Fetch a live wallet snapshot so the advisor can answer questions about
        # the user's own portfolio ("how's my wallet?") from real data. Best
        # effort: if it fails, the advisor still answers general questions.
        try:
            portfolio_summary = portfolio_tracker.get_summary()
        except Exception as e:
            logger.warning("Could not load wallet snapshot for chat: %s", e)
            portfolio_summary = None

        typing_task = asyncio.create_task(_typing_loop())
        try:
            response = await advisor.ask(chat_id, text, portfolio_summary=portfolio_summary)
        finally:
            typing_task.cancel()

        # Split into chunks if R1 returns a very long response
        for i in range(0, max(1, len(response)), 4000):
            chunk = response[i : i + 4000]
            await update.message.reply_text(chunk)

    # ── Command handlers ─────────────────────────────────────────────────────

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

    async def cmd_ask(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Ask a trading question explicitly: /ask Is NVDA a good buy now?"""
        question = " ".join(context.args) if context.args else ""
        if not question:
            await update.message.reply_text(
                "Usage: /ask <your trading question>\n"
                "Example: /ask Should I buy NVDA at current levels?"
            )
            return
        await _run_chat(update, context, question)

    async def cmd_reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Clear conversation history and start fresh."""
        advisor.clear_history(update.effective_chat.id)
        await update.message.reply_text(
            "🗑️ Conversation history cleared. Start a new question anytime."
        )

    async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle free-text trading questions (non-command messages)."""
        text = update.message.text
        if not text:
            return
        await _run_chat(update, context, text)

    # Owner-only access: restrict every handler to the owner's chat so the
    # private wallet/signals are never exposed to strangers who find the bot.
    if owner_chat_id:
        owner_filter = filters.Chat(chat_id=int(owner_chat_id))
        logger.info("Bot restricted to owner chat_id=%s", owner_chat_id)
    else:
        owner_filter = None
        logger.warning(
            "TELEGRAM_CHAT_ID not set — bot is UNRESTRICTED and will answer "
            "any chat, exposing wallet/signal data. Set it to lock the bot down."
        )

    # Build the app
    app = Application.builder().token(bot_token).build()

    command_handlers = {
        "status": cmd_status,
        "signals": cmd_signals,
        "portfolio": cmd_portfolio,
        "mute": cmd_mute,
        "unmute": cmd_unmute,
        "threshold": cmd_threshold,
        "help": cmd_help,
        "start": cmd_help,
        "ask": cmd_ask,
        "reset": cmd_reset,
    }
    for command, handler in command_handlers.items():
        app.add_handler(CommandHandler(command, handler, filters=owner_filter))

    # Free-text chat — must come last so commands take priority
    text_filter = filters.TEXT & ~filters.COMMAND
    if owner_filter is not None:
        text_filter = text_filter & owner_filter
    app.add_handler(MessageHandler(text_filter, handle_message))

    logger.info("Starting Telegram command bot with chat (polling)...")
    app.run_polling()  # blocking; manages its own event loop internally
