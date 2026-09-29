"""Tests for the paper-trading portfolio tracker.

The tracker is wired into both intraday pipelines (a buy/sell is executed
whenever an alert-triggering signal fires), so its buy/sell/P&L behaviour is
now load-bearing. These tests exercise it against a throwaway SQLite file.
"""

import pytest

# Import model modules so their tables are registered on Base.metadata
# before init_db() creates them.
import trading_expert.models.article  # noqa: F401
import trading_expert.models.signal  # noqa: F401
import trading_expert.models.portfolio  # noqa: F401
from trading_expert.models import init_db, get_session
from trading_expert.models.portfolio import PaperTrade
from trading_expert.portfolio.tracker import (
    PaperPortfolio,
    compute_position_size,
    eligible_for_paper_trade,
)


@pytest.fixture
def paper_db(tmp_path, monkeypatch):
    """Point the DB layer at a fresh per-test SQLite file.

    The tracker opens a new session (and therefore a new engine) per call, so
    a file-based DB is required — an in-memory one would be discarded between
    calls. get_engine reads DATABASE_URL at call time, so setting the env var
    is enough to redirect every get_session() the tracker makes.
    """
    db_path = tmp_path / "test_trading.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    init_db()
    yield


class TestExecuteBuy:
    def test_buy_records_trade_and_reduces_cash(self, paper_db):
        portfolio = PaperPortfolio()
        trade = portfolio.execute_buy("NVDA", price=100.0, quantity=10)

        assert trade is not None
        assert trade.action == "buy"
        assert trade.quantity == 10

        summary = portfolio.get_summary()
        # 100k starting capital - (100 * 10) spent
        assert summary["cash"] == pytest.approx(99_000.0)
        assert "NVDA" in summary["holdings"]
        assert summary["holdings"]["NVDA"]["quantity"] == 10

    def test_buy_rejected_when_insufficient_cash(self, paper_db):
        portfolio = PaperPortfolio(initial_capital=500.0)
        trade = portfolio.execute_buy("NVDA", price=100.0, quantity=10)  # needs 1000

        assert trade is None
        # No trade should have been persisted
        session = get_session()
        try:
            assert session.query(PaperTrade).count() == 0
        finally:
            session.close()


class TestExecuteSell:
    def test_sell_closes_buy_fifo_and_realizes_pnl(self, paper_db):
        portfolio = PaperPortfolio()
        portfolio.execute_buy("NVDA", price=100.0, quantity=10)
        sell = portfolio.execute_sell("NVDA", price=120.0, quantity=10)

        assert sell is not None
        summary = portfolio.get_summary()
        # (120 - 100) * 10 = 200 realized profit
        assert summary["realized_pnl"] == pytest.approx(200.0)
        # Position is fully closed
        assert "NVDA" not in summary["holdings"]
        # Cash: 100k - 1000 buy + 1200 sell proceeds
        assert summary["cash"] == pytest.approx(100_200.0)

    def test_sell_without_position_is_noop_long_only(self, paper_db):
        """The paper portfolio does not short: selling unheld stock no-ops."""
        portfolio = PaperPortfolio()
        sell = portfolio.execute_sell("NVDA", price=120.0, quantity=10)

        assert sell is None
        session = get_session()
        try:
            assert session.query(PaperTrade).count() == 0
        finally:
            session.close()

    def test_partial_sell_keeps_remainder_and_realizes_partial_pnl(self, paper_db):
        portfolio = PaperPortfolio()
        portfolio.execute_buy("NVDA", price=100.0, quantity=10)
        sell = portfolio.execute_sell("NVDA", price=110.0, quantity=4)

        assert sell is not None
        summary = portfolio.get_summary()
        # 6 shares remain open after selling 4 of 10
        assert summary["holdings"]["NVDA"]["quantity"] == 6
        # Realized only on the 4 sold: (110 - 100) * 4
        assert summary["realized_pnl"] == pytest.approx(40.0)
        # Cash: 100k - 1000 buy + 440 sell proceeds
        assert summary["cash"] == pytest.approx(99_440.0)


class TestEligibleForPaperTrade:
    """The gate is now the signal *decision* (should_alert), not alert_sent.

    Regression guard for the bug where a hold's Telegram cooldown suppressed a
    later buy's alert and silently skipped the trade.
    """

    def test_actionable_alerting_signal_with_price_is_eligible(self):
        assert eligible_for_paper_trade("buy", price=88.47, should_alert=True)
        assert eligible_for_paper_trade("sell", price=88.47, should_alert=True)

    def test_hold_is_never_eligible(self):
        assert not eligible_for_paper_trade("hold", price=88.47, should_alert=True)

    def test_below_alert_bar_is_not_eligible(self):
        # should_alert already folds in |score|>=threshold AND confidence>=min
        assert not eligible_for_paper_trade("buy", price=88.47, should_alert=False)

    def test_missing_price_is_not_eligible(self):
        # Cascade signals carry price=None and so cannot be sized/traded
        assert not eligible_for_paper_trade("buy", price=None, should_alert=True)


class TestHasOpenPosition:
    """Position-level dedup that replaces the old alert-cooldown dedup."""

    def test_flat_ticker_has_no_open_position(self, paper_db):
        portfolio = PaperPortfolio()
        assert portfolio.has_open_position("NVDA") is False

    def test_open_position_after_buy_then_freed_after_full_sell(self, paper_db):
        portfolio = PaperPortfolio()
        portfolio.execute_buy("NVDA", price=100.0, quantity=10)
        assert portfolio.has_open_position("NVDA") is True

        # A later sell closes it out and frees the ticker to be re-entered
        portfolio.execute_sell("NVDA", price=110.0, quantity=10)
        assert portfolio.has_open_position("NVDA") is False

    def test_partial_sell_still_leaves_open_position(self, paper_db):
        portfolio = PaperPortfolio()
        portfolio.execute_buy("NVDA", price=100.0, quantity=10)
        portfolio.execute_sell("NVDA", price=110.0, quantity=4)
        assert portfolio.has_open_position("NVDA") is True


# ── Conviction-weighted position sizing ──────────────────────────────────────

_SIZING = dict(
    target_invested_fraction=0.95,
    max_positions=20,
    conviction_cap=2.0,
    max_position_fraction=0.15,
)


class TestPositionSizing:
    def test_base_slot_size(self):
        # At exactly the alert threshold, conviction=1.0 → one base slot.
        # base = 0.95 * 100_000 / 20 = 4_750 → 47 shares @ $100.
        qty = compute_position_size(
            equity=100_000, cash=100_000, price=100.0,
            score=0.5, alert_threshold=0.5, **_SIZING,
        )
        assert qty == 47

    def test_stronger_signal_gets_more(self):
        weak = compute_position_size(
            equity=100_000, cash=100_000, price=100.0,
            score=0.5, alert_threshold=0.5, **_SIZING,
        )
        strong = compute_position_size(
            equity=100_000, cash=100_000, price=100.0,
            score=1.0, alert_threshold=0.5, **_SIZING,
        )
        assert strong > weak

    def test_conviction_is_capped(self):
        # score/threshold = 4× but cap is 2× → same as a 2× signal.
        capped = compute_position_size(
            equity=100_000, cash=100_000, price=100.0,
            score=2.0, alert_threshold=0.5, **_SIZING,
        )
        at_cap = compute_position_size(
            equity=100_000, cash=100_000, price=100.0,
            score=1.0, alert_threshold=0.5, **_SIZING,
        )
        assert capped == at_cap
        # 2× base = 9_500, under the 15_000 per-name cap → 95 shares.
        assert capped == 95

    def test_per_name_cap_binds(self):
        # Huge conviction cap would blow past 15% of equity; cap holds.
        qty = compute_position_size(
            equity=100_000, cash=100_000, price=100.0,
            score=5.0, alert_threshold=0.5,
            target_invested_fraction=0.95, max_positions=2,
            conviction_cap=10.0, max_position_fraction=0.15,
        )
        assert qty == int(0.15 * 100_000 / 100.0)  # 150 shares

    def test_capped_by_available_cash(self):
        qty = compute_position_size(
            equity=100_000, cash=500.0, price=100.0,
            score=1.0, alert_threshold=0.5, **_SIZING,
        )
        assert qty == 5  # only $500 cash → 5 shares

    def test_zero_when_price_exceeds_budget(self):
        qty = compute_position_size(
            equity=100_000, cash=100.0, price=200.0,
            score=1.0, alert_threshold=0.5, **_SIZING,
        )
        assert qty == 0

    def test_no_cash_no_shares(self):
        assert compute_position_size(
            equity=100_000, cash=0.0, price=100.0,
            score=1.0, alert_threshold=0.5, **_SIZING,
        ) == 0


class TestMarkToMarket:
    def test_unrealized_pnl_uses_live_price(self, paper_db):
        portfolio = PaperPortfolio()
        portfolio.execute_buy("NVDA", price=100.0, quantity=10)

        # No price_map → falls back to cost basis, so unrealized ~0.
        flat = portfolio.get_summary()
        assert flat["holdings"]["NVDA"]["unrealized_pnl"] == pytest.approx(0.0)
        assert flat["total_value"] == pytest.approx(100_000.0)

        # With a live mark above cost, equity and unrealized P&L rise.
        marked = portfolio.get_summary(price_map={"NVDA": 120.0})
        assert marked["holdings"]["NVDA"]["unrealized_pnl"] == pytest.approx(200.0)
        assert marked["total_value"] == pytest.approx(100_200.0)

    def test_open_quantity(self, paper_db):
        portfolio = PaperPortfolio()
        assert portfolio.open_quantity("NVDA") == 0
        portfolio.execute_buy("NVDA", price=100.0, quantity=10)
        assert portfolio.open_quantity("NVDA") == 10
