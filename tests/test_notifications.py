"""Tests for the notification formatter."""

import pytest
from trading_expert.notifications.formatter import (
    format_alert,
    format_daily_summary,
    format_portfolio_summary,
    format_help,
    format_error,
)


class TestFormatAlert:
    def test_alert_contains_ticker_and_score(self):
        msg = format_alert(
            ticker="NVDA",
            score=0.82,
            action="buy",
            confidence="high",
            reasoning="Strong earnings beat with raised guidance.",
            cascade_effects=[
                {"ticker": "TSM", "direction": "bullish", "reason": "More chip orders"},
                {"ticker": "INTC", "direction": "bearish", "reason": "Losing market share"},
            ],
            risk_factors=["Export controls"],
            suggested_timeframe="3-6 months",
            alert_label="🟢 STRONG BUY",
        )
        assert "NVDA" in msg
        assert "0.82" in msg
        assert "TSM" in msg
        assert "INTC" in msg
        assert "Export controls" in msg

    def test_alert_includes_price_when_provided(self):
        msg = format_alert(
            ticker="AAPL", score=0.5, action="buy", confidence="medium",
            reasoning="Test.", cascade_effects=[], risk_factors=[],
            suggested_timeframe="1-3 months", alert_label="🟢 BUY", price=185.50,
        )
        assert "$185.50" in msg

    def test_empty_cascade_and_risks(self):
        msg = format_alert(
            ticker="TSM", score=0.3, action="hold", confidence="low",
            reasoning="Neutral outlook.", cascade_effects=[], risk_factors=[],
            suggested_timeframe="6-12 months", alert_label="⚪ HOLD",
        )
        assert "NVDA" not in msg  # Should not contain cascade section


class TestDailySummary:
    def test_empty_summary(self):
        msg = format_daily_summary([])
        assert "No significant signals" in msg

    def test_summary_ranks_by_score(self):
        signals = [
            {"ticker": "AAPL", "score": 0.3, "action": "buy", "alert_label": "BUY", "confidence": "medium"},
            {"ticker": "NVDA", "score": 0.85, "action": "buy", "alert_label": "STRONG BUY", "confidence": "high"},
            {"ticker": "INTC", "score": -0.7, "action": "sell", "alert_label": "SELL", "confidence": "high"},
        ]
        msg = format_daily_summary(signals)
        # NVDA (0.85) should appear before AAPL (0.3)
        nvda_pos = msg.index("NVDA")
        aapl_pos = msg.index("AAPL")
        assert nvda_pos < aapl_pos

    def test_summary_includes_counts(self):
        signals = [
            {"ticker": "NVDA", "score": 0.8, "action": "buy", "alert_label": "BUY", "confidence": "high"},
            {"ticker": "INTC", "score": -0.7, "action": "sell", "alert_label": "SELL", "confidence": "high"},
            {"ticker": "TSM", "score": 0.1, "action": "hold", "alert_label": "HOLD", "confidence": "low"},
        ]
        msg = format_daily_summary(signals)
        assert "Buys: 1" in msg
        assert "Sells: 1" in msg
        assert "Holds: 1" in msg


class TestPortfolioSummary:
    def test_positive_pnl(self):
        msg = format_portfolio_summary(
            total_value=15000.0, cash=3000.0,
            holdings={"NVDA": {"quantity": 10, "avg_price": 800, "pnl": 2000}},
            pnl_total=2000.0, pnl_pct=15.4,
        )
        assert "$15,000" in msg
        assert "NVDA" in msg

    def test_negative_pnl(self):
        msg = format_portfolio_summary(
            total_value=8500.0, cash=2000.0,
            holdings={"INTC": {"quantity": 20, "avg_price": 50, "pnl": -500}},
            pnl_total=-500.0, pnl_pct=-5.5,
        )
        assert "$8,500" in msg


class TestFormatHelp:
    def test_help_contains_commands(self):
        msg = format_help()
        assert "/status" in msg
        assert "/signals" in msg
        assert "/portfolio" in msg
        assert "/mute" in msg
        assert "/unmute" in msg
        assert "/threshold" in msg


class TestFormatError:
    def test_error_message(self):
        msg = format_error("Something went wrong")
        assert "Error" in msg
        assert "Something went wrong" in msg
