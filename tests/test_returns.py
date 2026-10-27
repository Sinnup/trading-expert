"""Tests for price-return / profit-margin formulas and outcome grading.

Covers the regressions found in the price-data audit:
- outcome grading crashed on naive SQLite datetimes (no outcome was ever saved);
- horizons used the price on the day the grader ran instead of the close on
  the horizon date, and ignored stock splits;
- the portfolio message always showed $0 P&L per position;
- non-USD quotes were booked into the USD paper portfolio unconverted.
"""

import asyncio
from datetime import date, datetime, timezone
from typing import Optional

import pytest

import trading_expert.models.article  # noqa: F401
import trading_expert.models.signal  # noqa: F401
import trading_expert.models.portfolio  # noqa: F401
from trading_expert.analysis.returns import (
    close_at_horizon,
    horizon_target_date,
    pct_return,
    position_return_pct,
    profit_margin_pct,
    split_factor_after,
)
from trading_expert.fetchers.base import PriceData
from trading_expert.fetchers.prices import DailyHistory, PriceFetcher
from trading_expert.models import get_session, init_db
from trading_expert.models.portfolio import SignalOutcome
from trading_expert.models.signal import Signal
from trading_expert.notifications.formatter import format_portfolio_summary
from trading_expert.portfolio.backtest import apply_outcome, grade_signal_outcomes
from trading_expert.portfolio.tracker import PaperPortfolio


# Mon 2026-06-01 .. Fri 2026-06-12 trading sessions (no weekend bars).
SESSIONS = [date(2026, 6, d) for d in (1, 2, 3, 4, 5, 8, 9, 10, 11, 12)]
CLOSES = [(d, 100.0 + i) for i, d in enumerate(SESSIONS)]


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'returns.db'}")
    init_db()
    yield


def _price(ticker: str, close: float, currency: str = "USD") -> PriceData:
    return PriceData(
        ticker=ticker, date=datetime.now(timezone.utc), open=close, high=close,
        low=close, close=close, volume=0, currency=currency,
    )


class FakeFetcher(PriceFetcher):
    """PriceFetcher with canned quotes/history and a call log."""

    def __init__(self, quotes: dict[str, PriceData], history: Optional[DailyHistory] = None):
        super().__init__()
        self.quotes = quotes
        self.history = history
        self.history_calls: list[str] = []

    async def get_current(self, ticker: str) -> Optional[PriceData]:
        return self.quotes.get(ticker)

    async def get_daily_history(self, ticker: str, start: date) -> Optional[DailyHistory]:
        self.history_calls.append(ticker)
        return self.history


class TestFormulas:
    def test_pct_return(self):
        assert pct_return(100.0, 110.0) == pytest.approx(10.0)
        assert pct_return(100.0, 95.0) == pytest.approx(-5.0)

    def test_pct_return_invalid_base(self):
        assert pct_return(0.0, 10.0) is None
        assert pct_return(None, 10.0) is None
        assert pct_return(10.0, None) is None

    def test_margins(self):
        assert position_return_pct(50.0, 60.0) == pytest.approx(20.0)
        assert profit_margin_pct(250.0, 1000.0) == pytest.approx(25.0)
        assert profit_margin_pct(10.0, 0.0) is None

    def test_horizon_target_date_handles_naive(self):
        assert horizon_target_date(datetime(2026, 6, 5, 23, 0), 7) == date(2026, 6, 12)

    def test_split_factor_only_counts_later_splits(self):
        splits = [(date(2026, 6, 3), 2.0), (date(2026, 6, 9), 10.0)]
        assert split_factor_after(splits, date(2026, 6, 5)) == 10.0
        assert split_factor_after(splits, date(2026, 6, 1)) == 20.0


class TestCloseAtHorizon:
    def test_weekend_target_rolls_to_next_session(self):
        # Fri 06-05 + 1d = Sat → first session on/after is Mon 06-08.
        price = close_at_horizon(CLOSES, [], datetime(2026, 6, 5, 15), 1, date(2026, 6, 20))
        assert price == 105.0

    def test_uses_horizon_close_not_latest(self):
        # 7d from Mon 06-01 is 06-08, even if graded long afterwards.
        price = close_at_horizon(CLOSES, [], datetime(2026, 6, 1, 15), 7, date(2026, 6, 20))
        assert price == 105.0

    def test_not_elapsed_returns_none(self):
        assert close_at_horizon(CLOSES, [], datetime(2026, 6, 10), 7, date(2026, 6, 20)) is None

    def test_todays_bar_is_not_complete(self):
        # Target 06-12 exists but it's "today" → still trading, don't grade.
        assert close_at_horizon(CLOSES, [], datetime(2026, 6, 5), 7, date(2026, 6, 12)) is None

    def test_split_converted_to_signal_share_basis(self):
        # 10:1 split effective 06-09: post-split 106 close ≙ 1060 pre-split.
        price = close_at_horizon(
            CLOSES, [(date(2026, 6, 9), 10.0)], datetime(2026, 6, 2), 7, date(2026, 6, 20)
        )
        assert price == pytest.approx(1060.0)


class TestApplyOutcome:
    def _signal(self, action: str = "buy", base: float = 100.0) -> Signal:
        return Signal(
            ticker="NVDA", score=0.7, action=action, price_at_signal=base,
            created_at=datetime(2026, 6, 1, 14, 0),  # naive, as SQLite returns it
        )

    def test_fills_elapsed_horizons_and_grades(self):
        outcome = SignalOutcome(signal_score=0.7, price_at_signal=100.0, ticker="NVDA")
        history = DailyHistory("NVDA", CLOSES, [])
        assert apply_outcome(outcome, self._signal(), history, date(2026, 6, 20))
        assert outcome.price_1d == 101.0
        assert outcome.return_1d_pct == pytest.approx(1.0)
        assert outcome.price_7d == 105.0
        assert outcome.return_7d_pct == pytest.approx(5.0)
        assert outcome.price_30d is None
        assert outcome.was_correct is True

    def test_sell_graded_on_drop(self):
        falling = [(d, 110.0 - i) for i, d in enumerate(SESSIONS)]  # 06-01 = 110
        outcome = SignalOutcome(signal_score=-0.7, price_at_signal=110.0, ticker="NVDA")
        apply_outcome(outcome, self._signal("sell", 110.0),
                      DailyHistory("NVDA", falling, []), date(2026, 6, 20))
        assert outcome.return_7d_pct == pytest.approx(-5 / 110 * 100)
        assert outcome.was_correct is True

    def test_already_filled_horizon_untouched(self):
        outcome = SignalOutcome(signal_score=0.7, price_at_signal=100.0, ticker="NVDA",
                                price_1d=999.0, return_1d_pct=899.0)
        apply_outcome(outcome, self._signal(), DailyHistory("NVDA", CLOSES, []), date(2026, 6, 20))
        assert outcome.price_1d == 999.0


class TestGradeSignalOutcomes:
    def test_grades_naive_signals_and_skips_finished(self, db):
        session = get_session()
        pending = Signal(ticker="NVDA", score=0.7, action="buy", price_at_signal=100.0,
                         created_at=datetime(2026, 6, 1, 14, 0))
        no_price = Signal(ticker="NVDA", score=0.7, action="buy", price_at_signal=None,
                          created_at=datetime(2026, 6, 1, 14, 0))
        session.add_all([pending, no_price])
        session.commit()

        fetcher = FakeFetcher({}, DailyHistory("NVDA", CLOSES, []))
        now = datetime(2026, 6, 20, tzinfo=timezone.utc)
        updated = asyncio.run(grade_signal_outcomes(session, fetcher, now=now))
        session.commit()

        assert updated == 1
        assert fetcher.history_calls == ["NVDA"]  # one fetch per ticker
        outcome = session.query(SignalOutcome).one()
        assert outcome.signal_id == pending.id
        assert outcome.return_7d_pct == pytest.approx(5.0)

        # Re-run: nothing new elapsed → no updates.
        assert asyncio.run(grade_signal_outcomes(session, fetcher, now=now)) == 0
        session.close()


class TestPortfolioMargins:
    def test_summary_reports_margins_and_position_pct(self, db):
        portfolio = PaperPortfolio()
        portfolio.execute_buy("AMD", price=100.0, quantity=10)
        portfolio.execute_sell("AMD", price=120.0, quantity=10)  # +200 on 1000
        portfolio.execute_buy("NVDA", price=50.0, quantity=10)

        summary = portfolio.get_summary(price_map={"NVDA": 45.0})  # -50 on 500
        assert summary["realized_margin_pct"] == pytest.approx(20.0)
        assert summary["unrealized_margin_pct"] == pytest.approx(-10.0)
        assert summary["win_rate_pct"] == pytest.approx(100.0)
        assert summary["holdings"]["NVDA"]["unrealized_pct"] == pytest.approx(-10.0)

    def test_margins_none_without_basis(self, db):
        summary = PaperPortfolio().get_summary()
        assert summary["realized_margin_pct"] is None
        assert summary["unrealized_margin_pct"] is None
        assert summary["win_rate_pct"] is None

    def test_live_price_map_converts_to_usd(self, db):
        portfolio = PaperPortfolio()
        portfolio.execute_buy("AXTELCPO.MX", price=1.0, quantity=10)
        fetcher = FakeFetcher({
            "AXTELCPO.MX": _price("AXTELCPO.MX", 4.0, "MXN"),
            "MXNUSD=X": _price("MXNUSD=X", 0.05),
        })
        price_map = asyncio.run(portfolio.live_price_map(fetcher))
        assert price_map == {"AXTELCPO.MX": pytest.approx(0.2)}


class TestUsdConversion:
    def test_usd_passthrough(self):
        assert asyncio.run(FakeFetcher({}).to_usd(_price("NVDA", 10.0))) == 10.0

    def test_minor_currency_unit(self):
        fetcher = FakeFetcher({"GBPUSD=X": _price("GBPUSD=X", 1.25)})
        assert asyncio.run(fetcher.to_usd(_price("BARC.L", 200.0, "GBp"))) == pytest.approx(2.5)

    def test_missing_fx_rate(self):
        assert asyncio.run(FakeFetcher({}).to_usd(_price("2454.TW", 4000.0, "TWD"))) is None


class TestPortfolioFormatter:
    def test_position_shows_unrealized_pnl_and_margins(self):
        msg = format_portfolio_summary(
            total_value=99_950.0, cash=99_500.0,
            holdings={"NVDA": {"quantity": 10, "avg_price": 50.0, "current_price": 45.0,
                               "unrealized_pnl": -50.0, "unrealized_pct": -10.0}},
            pnl_total=-50.0, pnl_pct=-0.05,
            realized_margin_pct=20.0, unrealized_margin_pct=-10.0, win_rate_pct=100.0,
        )
        assert "$-50.00 (-10.00%)" in msg
        assert "Realized margin: +20.00%" in msg
        assert "Win rate: 100.0%" in msg


class TestImplausibleBase:
    def test_foreign_quote_base_not_graded(self):
        # Signal stored with another company's price (~10x the real close).
        signal = Signal(ticker="KEY", score=0.7, action="buy", price_at_signal=1000.0,
                        created_at=datetime(2026, 6, 2, 14, 0))
        outcome = SignalOutcome(signal_score=0.7, price_at_signal=1000.0, ticker="KEY")
        changed = apply_outcome(outcome, signal, DailyHistory("KEY", CLOSES, []), date(2026, 6, 20))
        assert changed is False
        assert outcome.price_7d is None
        assert outcome.was_correct is None

    def test_reference_close_uses_split_basis(self):
        from trading_expert.analysis.returns import close_on_signal_date
        ref = close_on_signal_date(CLOSES, [(date(2026, 6, 9), 10.0)], datetime(2026, 6, 2, 20))
        assert ref == pytest.approx(1010.0)

    def test_no_reference_close_not_graded(self):
        signal = Signal(ticker="WBD", score=0.7, action="buy", price_at_signal=100.0,
                        created_at=datetime(2026, 5, 20, 14, 0))  # before history starts
        outcome = SignalOutcome(signal_score=0.7, price_at_signal=100.0, ticker="WBD")
        assert apply_outcome(outcome, signal, DailyHistory("WBD", CLOSES, []),
                             date(2026, 6, 20)) is False
