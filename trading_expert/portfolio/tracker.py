"""Paper trading portfolio tracker.

Maintains a virtual portfolio that simulates buy/sell orders
based on trading signals. Tracks P&L and provides summary stats.

This allows the user to validate the signal strategy before
committing real money via GBM.
"""

import logging
from datetime import datetime, date, timezone
from typing import Optional

from trading_expert.constants import TRADEABLE_ACTIONS
from trading_expert.models import get_session
from trading_expert.models.portfolio import PaperTrade, PortfolioSnapshot

logger = logging.getLogger(__name__)

# Virtual starting capital
DEFAULT_CAPITAL = 100_000.0  # $100k paper money


def eligible_for_paper_trade(
    action: str,
    price: Optional[float],
    should_alert: bool,
) -> bool:
    """Whether a primary signal should produce a paper trade.

    Gated on the signal *decision* (``should_alert`` already encodes
    ``|score| >= threshold`` AND ``confidence >= min_confidence``), NOT on
    whether the Telegram alert physically went out. The alert send is subject
    to a per-ticker cooldown that a non-actionable ``hold`` for the same ticker
    frequently trips first, which would otherwise suppress the actionable
    buy/sell alert and silently skip the trade. Duplicate stacking of the same
    re-escalated news is instead prevented at the position level (one open
    position per ticker) — see ``PaperPortfolio.has_open_position``.
    """
    return should_alert and action in TRADEABLE_ACTIONS and bool(price)


class PaperPortfolio:
    """Manages a virtual paper trading portfolio."""

    def __init__(self, initial_capital: float = DEFAULT_CAPITAL):
        self.initial_capital = initial_capital

    def execute_buy(
        self,
        ticker: str,
        price: float,
        quantity: int,
        signal_id: Optional[int] = None,
    ) -> Optional[PaperTrade]:
        """Execute a simulated buy order.

        Args:
            ticker: Stock ticker.
            price: Buy price per share.
            quantity: Number of shares.
            signal_id: Which signal triggered this trade.

        Returns:
            The PaperTrade record, or None if insufficient cash.
        """
        session = get_session()

        try:
            # Check cash balance
            summary = self.get_summary()
            cost = price * quantity
            if cost > summary["cash"]:
                logger.warning(
                    f"Insufficient cash for {ticker} buy: "
                    f"need ${cost:,.2f}, have ${summary['cash']:,.2f}"
                )
                return None

            trade = PaperTrade(
                ticker=ticker,
                action="buy",
                quantity=quantity,
                price=price,
                signal_id=signal_id,
                executed_at=datetime.now(timezone.utc),
            )
            session.add(trade)
            session.commit()
            session.refresh(trade)

            logger.info(
                f"PAPER BUY: {quantity} {ticker} @ ${price:.2f} "
                f"(total: ${cost:,.2f})"
            )
            return trade

        except Exception as e:
            session.rollback()
            logger.error(f"Paper trade failed: {e}")
            return None
        finally:
            session.close()

    def execute_sell(
        self,
        ticker: str,
        price: float,
        quantity: int,
        signal_id: Optional[int] = None,
    ) -> Optional[PaperTrade]:
        """Execute a simulated sell order.

        Args:
            ticker: Stock ticker.
            price: Sell price per share.
            quantity: Number of shares.
            signal_id: Which signal triggered this trade.

        Returns:
            The PaperTrade record, or None if insufficient shares.
        """
        session = get_session()

        try:
            # Check position
            position = self._get_position(ticker)
            if position["quantity"] < quantity:
                logger.warning(
                    f"Insufficient shares for {ticker} sell: "
                    f"need {quantity}, have {position['quantity']:.0f}"
                )
                return None

            trade = PaperTrade(
                ticker=ticker,
                action="sell",
                quantity=quantity,
                price=price,
                signal_id=signal_id,
                executed_at=datetime.now(timezone.utc),
            )

            # Close out existing buy trades (FIFO)
            # Find open buy trades for this ticker
            open_buys = (
                session.query(PaperTrade)
                .filter(
                    PaperTrade.ticker == ticker,
                    PaperTrade.action == "buy",
                    PaperTrade.closed_at.is_(None),
                )
                .order_by(PaperTrade.executed_at.asc())
                .all()
            )

            remaining_to_close = quantity
            for buy in open_buys:
                if remaining_to_close <= 0:
                    break

                close_qty = min(remaining_to_close, buy.quantity)
                now = datetime.now(timezone.utc)

                if close_qty == buy.quantity:
                    # Whole lot consumed — close it in place.
                    buy.closed_at = now
                    buy.close_price = price
                    buy.pnl_realized = (price - buy.price) * close_qty
                else:
                    # Partial fill — split the lot: shrink the still-open remainder
                    # and record the closed portion as its own row so that both
                    # realized P&L and open-position quantities stay correct.
                    buy.quantity -= close_qty
                    session.add(PaperTrade(
                        ticker=buy.ticker,
                        action="buy",
                        quantity=close_qty,
                        price=buy.price,
                        signal_id=buy.signal_id,
                        executed_at=buy.executed_at,
                        closed_at=now,
                        close_price=price,
                        pnl_realized=(price - buy.price) * close_qty,
                    ))

                remaining_to_close -= close_qty

            session.add(trade)
            session.commit()
            session.refresh(trade)

            logger.info(
                f"PAPER SELL: {quantity} {ticker} @ ${price:.2f}"
            )
            return trade

        except Exception as e:
            session.rollback()
            logger.error(f"Paper sell failed: {e}")
            return None
        finally:
            session.close()

    def get_summary(self) -> dict:
        """Get current portfolio summary.

        Returns:
            Dict with total_value, cash, holdings, pnl_total, pnl_pct.
        """
        session = get_session()

        try:
            # All trades
            buys = (
                session.query(PaperTrade)
                .filter(PaperTrade.action == "buy")
                .all()
            )
            sells = (
                session.query(PaperTrade)
                .filter(PaperTrade.action == "sell")
                .all()
            )

            # Total spent
            total_spent = sum(
                (b.price * b.quantity)
                for b in buys
                if b.closed_at is None  # Only still-open buys
            )
            # Actually, we need: all buy cost - closed buy cost
            total_buy_cost = sum(b.price * b.quantity for b in buys)
            total_sell_proceeds = sum(s.price * s.quantity for s in sells)

            # Cash
            cash = self.initial_capital - total_buy_cost + total_sell_proceeds

            # Holdings
            holdings = self._get_all_positions()

            # Total value (cash + current holdings value)
            holdings_value = sum(
                pos["quantity"] * pos["current_price"]
                for pos in holdings.values()
            )
            total_value = cash + holdings_value

            # P&L
            realized_pnl = sum(
                b.pnl_realized or 0 for b in buys if b.closed_at is not None
            )
            unrealized_pnl = sum(
                pos.get("unrealized_pnl", 0) for pos in holdings.values()
            )
            pnl_total = realized_pnl + unrealized_pnl
            pnl_pct = (pnl_total / self.initial_capital) * 100

            return {
                "total_value": total_value,
                "cash": cash,
                "holdings": holdings,
                "pnl_total": pnl_total,
                "pnl_pct": pnl_pct,
                "realized_pnl": realized_pnl,
                "unrealized_pnl": unrealized_pnl,
            }

        finally:
            session.close()

    def take_snapshot(self) -> PortfolioSnapshot:
        """Save a daily portfolio snapshot."""
        session = get_session()
        summary = self.get_summary()

        try:
            snapshot = PortfolioSnapshot(
                date=date.today(),
                total_value=summary["total_value"],
                cash=summary["cash"],
                holdings={
                    ticker: {
                        "quantity": pos["quantity"],
                        "avg_price": pos["avg_price"],
                        "current_price": pos["current_price"],
                    }
                    for ticker, pos in summary["holdings"].items()
                },
            )
            session.add(snapshot)
            session.commit()
            return snapshot
        finally:
            session.close()

    def has_open_position(self, ticker: str) -> bool:
        """Whether an open (unclosed) long position exists for ``ticker``.

        Used to dedup buys: the same fresh news re-escalates every intraday
        cycle and would otherwise stack a new buy each time. We hold at most one
        open position per ticker; a subsequent sell signal closes it and frees
        the ticker to be re-entered.
        """
        return self._get_position(ticker)["quantity"] > 0

    def _get_position(self, ticker: str) -> dict:
        """Get current position for a ticker."""
        return self._get_all_positions().get(ticker, {
            "quantity": 0,
            "avg_price": 0,
            "current_price": 0,
            "unrealized_pnl": 0,
        })

    def _get_all_positions(self) -> dict:
        """Get all current positions with unrealized P&L."""
        session = get_session()

        try:
            buys = (
                session.query(PaperTrade)
                .filter(
                    PaperTrade.action == "buy",
                    PaperTrade.closed_at.is_(None),
                )
                .all()
            )

            positions: dict[str, dict] = {}
            for buy in buys:
                if buy.ticker not in positions:
                    positions[buy.ticker] = {
                        "quantity": 0,
                        "total_cost": 0,
                        "avg_price": 0,
                        "current_price": buy.price,  # Fallback to buy price
                        "unrealized_pnl": 0,
                    }

                pos = positions[buy.ticker]
                pos["quantity"] += buy.quantity
                pos["total_cost"] += buy.price * buy.quantity
                pos["avg_price"] = pos["total_cost"] / pos["quantity"] if pos["quantity"] > 0 else 0
                # Note: current_price should be updated from external price data
                pos["unrealized_pnl"] = (
                    (pos["current_price"] - pos["avg_price"]) * pos["quantity"]
                )

            # Clean up dict
            for ticker in positions:
                del positions[ticker]["total_cost"]

            return positions

        finally:
            session.close()
