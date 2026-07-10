"""
Trading Chat Advisor — conversational AI for investment decisions.

Uses DeepSeek-R1 (deepseek-reasoner) for deep chain-of-thought reasoning.
Restricted to stock market and trading topics only — all other topics
are refused with a fixed message.
"""

import asyncio
import logging
import os
from collections import defaultdict
from typing import Optional

from openai import OpenAI

logger = logging.getLogger(__name__)


CHAT_SYSTEM_PROMPT = """You are an AI trading advisor specialized in semiconductor, AI, cloud, and tech stock investments. You assist investors with buy/sell/hold decisions backed by fundamental analysis, supply chain dynamics, and macro context.

## Scope — ONLY answer questions about:
- Individual stock investment decisions (buy, sell, hold, entry/exit points)
- Company fundamentals (earnings, revenue, margins, guidance, valuation)
- Semiconductor and tech supply chain dynamics and cascade effects
- Macroeconomic factors that affect stock markets (interest rates, inflation, geopolitics)
- Portfolio strategy and position sizing for equities
- Technical analysis patterns and price action
- Market news interpretation and its impact on stocks
- Options strategies related to tracked stocks
- Risk/reward assessment for specific trades
- US and BMV (Mexican) stock markets

## Hard restrictions:
If the user asks about ANY topic outside stocks, trading, investing, or financial markets — including but not limited to politics, sports, entertainment, cooking, relationships, general technology unrelated to public companies, or any other non-finance subject — respond ONLY with this exact sentence:
"⛔ I'm restricted to stock market and trading topics only. Ask me about specific stocks, investment decisions, market analysis, or portfolio strategy."
Do NOT add anything else. Do NOT apologize. Do NOT explain further.
Do NOT roleplay as another AI or change your persona under any circumstances.

## Analysis approach:
- Reason deeply about supply chain cascades: equipment → foundry → designers → customers
- Always present both bull and bear cases
- Be specific about catalysts, timeframes, and risk factors
- Quantify when possible (e.g., "a 10% miss in guidance typically causes -15–20% drawdown")
- End with a clear, actionable recommendation: BUY / SELL / HOLD with a suggested timeframe
- Always add: "⚠️ This is not formal financial advice — do your own due diligence."

## Tracked universe:
Semiconductors & equipment: NVDA, AMD, INTC, AVGO, QCOM, MRVL, MU, TXN, ADI, ON, MCHP, ASML, AMAT, LRCX, KLAC
Foundries: TSM (TSMC), SMIC, Hua Hong
AI/Cloud/Consumer: MSFT, GOOGL, AMZN, META, AAPL
Others: MediaTek (2454.TW), ARM, IFNNY (Infineon), STM
BMV (Mexico): BIMBOA.MX, WALMEX.MX, CEMEX.MX, FEMSA.MX, AMX.MX, GFNORTEO.MX
"""

OFF_TOPIC_RESPONSE = (
    "⛔ I'm restricted to stock market and trading topics only. "
    "Ask me about specific stocks, investment decisions, market analysis, or portfolio strategy."
)

# Maximum user+assistant turns kept in memory per chat (system message excluded)
MAX_HISTORY_MESSAGES = 10

# Telegram message size limit
TELEGRAM_MAX_CHARS = 4096


class TradingChatAdvisor:
    """Conversational trading advisor powered by DeepSeek-R1.

    Maintains per-chat conversation history and hard-restricts responses
    to stock market and trading topics via system prompt enforcement.
    """

    def __init__(self, api_key: Optional[str] = None):
        api_key = api_key or os.getenv("DEEPSEEK_API_KEY", "")
        if not api_key:
            raise ValueError("DEEPSEEK_API_KEY is required. Set it in .env")

        self.client = OpenAI(
            api_key=api_key,
            base_url="https://api.deepseek.com",
            timeout=120.0,  # R1 can take up to ~90 s for complex chains of thought
        )
        # {chat_id: [{"role": "user"/"assistant", "content": "..."}, ...]}
        self._history: dict[int | str, list[dict]] = defaultdict(list)

    async def ask(self, chat_id: int | str, user_message: str) -> str:
        """Process a user message and return a deep-reasoning trading response.

        Args:
            chat_id: Telegram chat ID — used as the conversation key.
            user_message: The user's free-text message.

        Returns:
            Assistant response text (plain, no Telegram markdown).
        """
        history = self._history[chat_id]
        history.append({"role": "user", "content": user_message})

        # Trim oldest turns when history grows too long
        if len(history) > MAX_HISTORY_MESSAGES:
            self._history[chat_id] = history[-MAX_HISTORY_MESSAGES:]
            history = self._history[chat_id]

        messages = [{"role": "system", "content": CHAT_SYSTEM_PROMPT}] + history

        try:
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: self.client.chat.completions.create(
                    model="deepseek-reasoner",
                    messages=messages,
                    max_tokens=4096,
                ),
            )

            reply = (response.choices[0].message.content or "").strip()
            history.append({"role": "assistant", "content": reply})
            return reply

        except Exception as e:
            logger.error("DeepSeek chat error for chat_id=%s: %s", chat_id, e)
            # Roll back the failed user turn so the next message starts clean
            if history and history[-1]["role"] == "user":
                history.pop()
            return (
                "⚠️ The analysis model is temporarily unavailable. "
                "Please try again in a moment."
            )

    def clear_history(self, chat_id: int | str) -> None:
        """Wipe conversation history for a chat (allows starting fresh)."""
        self._history.pop(chat_id, None)

    def history_length(self, chat_id: int | str) -> int:
        """Return number of stored messages for a chat."""
        return len(self._history.get(chat_id, []))
