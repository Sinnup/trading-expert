"""
Message formatter — builds rich Telegram messages from trading signals.

Produces beautiful, scannable alerts with emojis, structured sections,
and actionable information. Designed for mobile-first reading.
"""

from datetime import datetime
from typing import Optional


def format_alert(
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
) -> str:
    """Format a trading signal as a rich Telegram alert message.

    Example output:
    ```
    🔴🟢 ALERT: Time to Invest in NVIDIA (NVDA)

    NVIDIA signed a $2B deal with Tesla for autonomous driving chips.
    This is a multi-year supply agreement at their N3 node.

    📈 Signal: STRONG BUY (0.82/1.0 confidence)

    🔗 Cascade effects:
      • TSMC → 🟢 Bullish (will fab these N3 chips)
      • ASML → 🟢 Bullish (increased EUV demand)
      • Intel → 🔴 Bearish (losing automotive socket)

    ⚠️ Risk: Export controls could limit N3 shipments

    💡 Strategy: Accumulate NVDA on any pullback.
       Consider TSM as a paired trade.
       Set stop-loss at -8% from entry.

    [View details] [Acknowledge] [Snooze 4h]
    ```
    """
    emoji_map = {
        "buy": "🟢",
        "sell": "🔴",
        "hold": "⚪",
        "bullish": "🟢",
        "bearish": "🔴",
    }

    lines = [
        f"{alert_label}: {ticker}",
        "",
    ]

    if price:
        lines.append(f"💵 Current price: ${price:.2f}")

    lines.extend([
        f"📊 Signal score: {score:+.2f}",
        f"🎯 Confidence: {confidence.upper()}",
        f"⏱️ Suggested timeframe: {suggested_timeframe}",
        "",
        "💡 Analysis:",
        reasoning,
    ])

    # Cascade effects section
    if cascade_effects:
        lines.append("")
        lines.append("🔗 Supply Chain Impact:")
        for ce in cascade_effects:
            direction = ce.get("direction", "neutral")
            emoji = emoji_map.get(direction, "➡️")
            ticker_name = ce.get("ticker", "???")
            reason = ce.get("reason", "")
            lines.append(f"  {emoji} **{ticker_name}**: {reason}")

    # Risk factors
    if risk_factors:
        lines.append("")
        lines.append("⚠️ Risk Factors:")
        for risk in risk_factors:
            lines.append(f"  • {risk}")

    # Footer
    lines.append("")
    lines.append(f"🕐 Generated: {datetime.now().strftime('%Y-%m-%d %H:%M UTC')}")

    return "\n".join(lines)


def format_alert_concise(
    ticker: str,
    action: str,
    price: Optional[float] = None,
    alert_label: str = "",
    suggested_timeframe: str = "",
) -> str:
    """Format a signal as a short, straight-to-the-point action alert.

    No analysis, cascade, or risk sections — just what to buy/sell and at
    what price. Designed for at-a-glance reading during trading hours.

    Example:
        🟢 BUY NVDA @ $123.45  (1-2 weeks)
    """
    emoji = {"buy": "🟢", "sell": "🔴"}.get(action.lower(), "⚪")
    verb = action.upper()

    headline = f"{emoji} {verb} {ticker}"
    if price:
        headline += f" @ ${price:,.2f}"
    if suggested_timeframe:
        headline += f"  ({suggested_timeframe})"
    return headline


def format_daily_summary(
    signals: list[dict],
    date: Optional[str] = None,
) -> str:
    """Format a daily summary of all signals, ranked by score.

    Args:
        signals: List of signal dicts with ticker, score, action, alert_label.
        date: Date string for the report.

    Returns:
        Formatted Telegram message.
    """
    date_str = date or datetime.now().strftime("%Y-%m-%d")
    lines = [
        f"📊 **Daily Trading Report — {date_str}**",
        "",
    ]

    if not signals:
        lines.append("No significant signals today. Markets were quiet.")
        return "\n".join(lines)

    # Sort by absolute score (strongest first)
    ranked = sorted(signals, key=lambda s: abs(s["score"]), reverse=True)

    lines.append(f"{len(ranked)} signals generated today:")
    lines.append("")

    for i, sig in enumerate(ranked, 1):
        lines.append(f"{i}. {sig['alert_label']}: **{sig['ticker']}**  "
                     f"({sig['score']:+.2f})")
        lines.append(f"   Action: {sig['action'].upper()} | "
                     f"Confidence: {sig.get('confidence', 'N/A')}")
        lines.append("")

    # Summary stats
    buy_count = sum(1 for s in signals if s["action"] == "buy")
    sell_count = sum(1 for s in signals if s["action"] == "sell")
    hold_count = sum(1 for s in signals if s["action"] == "hold")

    lines.append("---")
    lines.append(f"📈 Buys: {buy_count} | 📉 Sells: {sell_count} | ⚪ Holds: {hold_count}")
    lines.append("")
    lines.append("Reply /portfolio for P&L | /signals <TICKER> for details")

    return "\n".join(lines)


def format_portfolio_summary(
    total_value: float,
    cash: float,
    holdings: dict,
    pnl_total: float,
    pnl_pct: float,
) -> str:
    """Format a portfolio summary message."""
    pnl_emoji = "🟢" if pnl_total >= 0 else "🔴"

    lines = [
        "💰 **Paper Trading Portfolio**",
        "",
        f"💵 Total Value: ${total_value:,.2f}",
        f"🏦 Cash: ${cash:,.2f}",
        f"{pnl_emoji} Total P&L: ${pnl_total:+,.2f} ({pnl_pct:+.2f}%)",
        "",
    ]

    if holdings:
        lines.append("**Positions:**")
        for ticker, pos in holdings.items():
            pos_pnl = pos.get("pnl", 0)
            pos_emoji = "🟢" if pos_pnl >= 0 else "🔴"
            lines.append(
                f"  {pos_emoji} {ticker}: {pos.get('quantity', 0):.0f} shares "
                f"@ ${pos.get('avg_price', 0):.2f} | "
                f"P&L: ${pos_pnl:+,.2f}"
            )

    return "\n".join(lines)


def format_error(message: str) -> str:
    """Format an error message."""
    return f"⚠️ Error: {message}"


def format_help() -> str:
    """Format the help message for bot commands."""
    return """🤖 *Trading Expert Bot*

*Chat* — just type any trading question and the AI advisor (DeepSeek-R1) will respond:
  "Should I buy NVDA at these levels?"
  "What's the outlook for semiconductors?"
  "Explain the TSMC supply chain impact on AMD"

*Commands*
/ask <question> — Same as typing; useful in group chats
/reset — Clear conversation history and start fresh
/status — Today's signal summary for all tracked tickers
/signals NVDA — Last 5 signals for a specific ticker
/portfolio — Paper trading P&L summary
/mute 4h — Snooze alerts (1h, 4h, 8h, 24h)
/unmute — Resume alerts
/threshold 0.8 — Set minimum alert threshold
/help — Show this message

_Note: The advisor only discusses stocks and trading topics._"""
