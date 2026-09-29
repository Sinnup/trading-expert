"""Tests for the Telegram chat advisor's live-wallet grounding.

The advisor now accepts an optional paper-wallet snapshot and injects it as a
system message so DeepSeek-R1 can answer "how's my wallet?" from real data.
These tests stub the network client so no API call is made.
"""

from unittest.mock import MagicMock

import pytest

from trading_expert.analysis.chat_advisor import (
    TradingChatAdvisor,
    format_wallet_context,
)


SUMMARY = {
    "total_value": 99_999.99,
    "cash": 95_997.07,
    "pnl_total": -0.01,
    "pnl_pct": 0.0,
    "holdings": {
        "NVDA": {"quantity": 4, "avg_price": 204.37, "current_price": 210.0},
    },
}


class TestFormatWalletContext:
    def test_includes_cash_value_pnl_and_holdings(self):
        ctx = format_wallet_context(SUMMARY)
        assert "LIVE WALLET SNAPSHOT" in ctx
        assert "95,997.07" in ctx  # cash
        assert "99,999.99" in ctx  # total value
        assert "NVDA" in ctx

    def test_empty_holdings_reads_as_all_cash(self):
        ctx = format_wallet_context({**SUMMARY, "holdings": {}})
        assert "none" in ctx.lower()


class TestAskInjectsWallet:
    @pytest.fixture
    def advisor(self, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
        return TradingChatAdvisor()

    @staticmethod
    def _stub_client(advisor, capture):
        """Replace the OpenAI client so create() records messages, returns 'ok'."""
        def fake_create(**kwargs):
            capture["messages"] = kwargs["messages"]
            resp = MagicMock()
            resp.choices = [MagicMock()]
            resp.choices[0].message.content = "ok"
            return resp

        advisor.client.chat.completions.create = fake_create

    async def test_wallet_snapshot_injected_as_system_message(self, advisor):
        capture: dict = {}
        self._stub_client(advisor, capture)

        await advisor.ask(1, "how's my wallet?", portfolio_summary=SUMMARY)

        systems = [m["content"] for m in capture["messages"] if m["role"] == "system"]
        # The injected snapshot uses a bracketed header unique to the live data
        # (the base prompt merely references "LIVE WALLET SNAPSHOT" in prose).
        assert any("[LIVE WALLET SNAPSHOT" in s for s in systems)
        assert any("95,997.07" in s for s in systems)

    async def test_no_snapshot_when_summary_omitted(self, advisor):
        capture: dict = {}
        self._stub_client(advisor, capture)

        await advisor.ask(1, "is NVDA a buy?")

        systems = [m["content"] for m in capture["messages"] if m["role"] == "system"]
        assert not any("[LIVE WALLET SNAPSHOT" in s for s in systems)
