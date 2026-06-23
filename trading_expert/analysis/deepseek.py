"""
DeepSeek API client for deep sentiment analysis and cascade reasoning.

Uses the OpenAI SDK pointed at DeepSeek's API endpoint.
DeepSeek's function calling ensures structured JSON output.

Two models available:
- deepseek-chat (V3): Fast, for daily analysis of most articles
- deepseek-reasoner (R1): Deep chain-of-thought for high-impact events
"""

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Optional

from openai import OpenAI
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ── Structured Output Schemas ──────────────────────────────────────────────

class CascadeEffectSchema(BaseModel):
    """A secondary impact on another company triggered by the primary event."""
    ticker: str = Field(description="Ticker symbol of the impacted company")
    direction: str = Field(description="'bullish' or 'bearish'")
    reason: str = Field(description="Why this company is impacted")


class SignalSchema(BaseModel):
    """Structured trading signal from DeepSeek analysis."""
    primary_ticker: str = Field(description="Ticker symbol of the primary affected company")
    sentiment: float = Field(description="Sentiment score from -1.0 (extremely bearish) to +1.0 (extremely bullish)")
    confidence: str = Field(description="Confidence level: 'high', 'medium', or 'low'")
    reasoning: str = Field(description="Detailed reasoning explaining the signal")
    cascade_effects: list[CascadeEffectSchema] = Field(
        default_factory=list,
        description="Secondary impacts on suppliers, customers, and competitors"
    )
    urgency: str = Field(description="'high', 'medium', or 'low' — how quickly should the user act?")
    action: str = Field(description="Recommended action: 'buy', 'sell', or 'hold'")
    suggested_timeframe: str = Field(description="e.g., '1-3 months', '6-12 months', 'immediate'")
    risk_factors: list[str] = Field(default_factory=list, description="Risk factors to consider")


# ── Pydantic models for internal use ────────────────────────────────────────

@dataclass
class ArticleContext:
    """Context sent to DeepSeek about a single article."""
    article_id: str
    title: str
    source: str
    source_tier: int
    text: str
    vader_score: float
    keyword_triggers: list[str]


@dataclass
class DeepSeekSignal:
    """Parsed DeepSeek response for a single article."""
    article_id: str
    primary_ticker: str
    sentiment: float  # -1.0 to 1.0
    confidence: str  # "high" "medium" "low"
    reasoning: str
    cascade_effects: list[dict] = field(default_factory=list)
    urgency: str = "medium"
    action: str = "hold"
    suggested_timeframe: str = "3-6 months"
    risk_factors: list[str] = field(default_factory=list)
    model_used: str = "deepseek-chat"
    raw_response: str = ""  # For debugging


# ── DeepSeek Analyzer ───────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are a trading analyst AI specialized in the semiconductor and tech industry. Your job is to analyze news articles and produce structured trading signals.

## Your expertise:
- Semiconductor supply chain: equipment (ASML, AMAT) → foundries (TSMC, SMIC) → designers (NVDA, AMD) → customers (MSFT, AAPL)
- Memory cycles: HBM (SK Hynix, Micron), NAND, DRAM
- Geopolitical factors: export controls, CHIPS Act, Taiwan/China risks
- Product cycles: GPU generations, datacenter capex, smartphone cycles
- Financial metrics: revenue growth, margins, P/E, guidance

## For each article, analyze:
1. **Primary impact**: Which company is most directly affected? Score sentiment -1.0 (crash) to +1.0 (moon).
2. **Confidence**: How sure are you? "high" (clear impact), "medium" (likely impact), "low" (speculative).
3. **Cascade effects**: How does this ripple to suppliers, customers, and competitors?
4. **Action**: "buy", "sell", or "hold". Only say buy/sell if confidence is medium+ and sentiment magnitude > 0.5.
5. **Urgency**: "high" (act within days), "medium" (weeks), "low" (months).
6. **Timeframe**: How long until this plays out?
7. **Risk factors**: What could go wrong?

## Cascade logic:
- Bullish for a chip designer → bullish for its foundry (more orders), bullish for equipment (more fab investment)
- Bearish for a foundry → bearish for its equipment suppliers (less fab investment), bearish for designers who depend on it
- Bullish for a company → bearish for its competitors (market share loss)
- Hyperscaler capex increase → bullish for NVIDIA, AMD, memory makers
- Export controls on China → bearish for SMIC, Hua Hong; bullish for TSMC (less competition)

Be specific in your reasoning. Reference actual industry dynamics."""


class DeepSeekAnalyzer:
    """Sends filtered articles to DeepSeek for deep reasoning.

    Uses function calling (tool_choice) to force structured JSON output
    matching the SignalSchema.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "deepseek-chat",
        max_tokens: int = 2048,
        temperature: float = 0.1,
    ):
        api_key = api_key or os.getenv("DEEPSEEK_API_KEY", "")
        if not api_key:
            raise ValueError("DEEPSEEK_API_KEY is required. Set it in .env")

        self.client = OpenAI(
            api_key=api_key,
            base_url="https://api.deepseek.com",
        )
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature

    async def analyze(
        self,
        article: ArticleContext,
        supply_chain_context: str,
        price_context: str = "",
    ) -> DeepSeekSignal:
        """Analyze a single article with DeepSeek.

        Args:
            article: The filtered article context.
            supply_chain_context: Generated by SupplyChainGraph.generate_context_text().
            price_context: Current price data for the primary ticker.

        Returns:
            Structured DeepSeekSignal.
        """
        user_message = self._build_user_message(article, supply_chain_context, price_context)

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_message},
                ],
                max_tokens=self.max_tokens,
                temperature=self.temperature,
                tools=[{
                    "type": "function",
                    "function": {
                        "name": "emit_trading_signal",
                        "description": "Emit a structured trading signal based on news analysis",
                        "parameters": SignalSchema.model_json_schema(),
                    },
                }],
                tool_choice={
                    "type": "function",
                    "function": {"name": "emit_trading_signal"},
                },
            )

            # Parse the tool call
            choice = response.choices[0]
            tool_call = choice.message.tool_calls[0]
            signal_data = json.loads(tool_call.function.arguments)

            return DeepSeekSignal(
                article_id=article.article_id,
                primary_ticker=signal_data.get("primary_ticker", "UNKNOWN"),
                sentiment=signal_data.get("sentiment", 0.0),
                confidence=signal_data.get("confidence", "medium"),
                reasoning=signal_data.get("reasoning", ""),
                cascade_effects=signal_data.get("cascade_effects", []),
                urgency=signal_data.get("urgency", "medium"),
                action=signal_data.get("action", "hold"),
                suggested_timeframe=signal_data.get("suggested_timeframe", "3-6 months"),
                risk_factors=signal_data.get("risk_factors", []),
                model_used=self.model,
                raw_response=json.dumps(signal_data),
            )

        except Exception as e:
            logger.error(f"DeepSeek API error for {article.article_id}: {e}")
            # Return a neutral signal on error
            return DeepSeekSignal(
                article_id=article.article_id,
                primary_ticker="UNKNOWN",
                sentiment=0.0,
                confidence="low",
                reasoning=f"API error: {str(e)}",
                action="hold",
                urgency="low",
                model_used=self.model,
            )

    async def analyze_batch(
        self,
        articles: list[tuple[ArticleContext, str]],
    ) -> list[DeepSeekSignal]:
        """Analyze multiple articles. Each is a separate API call.

        Args:
            articles: List of (article_context, supply_chain_context) tuples.
        """
        results: list[DeepSeekSignal] = []
        for article, sc_context in articles:
            signal = await self.analyze(article, sc_context)
            results.append(signal)
        return results

    def _build_user_message(
        self,
        article: ArticleContext,
        supply_chain_context: str,
        price_context: str,
    ) -> str:
        """Build the user message for DeepSeek with all available context."""
        parts = [
            f"## News Article\n",
            f"**Title**: {article.title}",
            f"**Source**: {article.source} (tier {article.source_tier})",
            f"**Pre-filter sentiment**: {article.vader_score:.2f}",
        ]

        if article.keyword_triggers:
            parts.append(f"**Trigger keywords**: {', '.join(article.keyword_triggers)}")

        parts.append(f"\n**Content**:\n{article.text or article.title}")

        if supply_chain_context:
            parts.append(f"\n## Supply Chain Context\n{supply_chain_context}")

        if price_context:
            parts.append(f"\n## Current Price Data\n{price_context}")

        parts.append("\n## Instructions")
        parts.append("Analyze this article and emit a structured trading signal.")
        parts.append("Consider supply chain cascade effects carefully.")

        return "\n".join(parts)
