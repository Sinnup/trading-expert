"""
Supply chain cascade engine.

Models the semiconductor supply chain as a directed graph and traces
how events at one company propagate to suppliers, customers, and competitors.

Example cascade:
  ASML EUV delay → TSM fab capacity constrained → NVDA chip shortage
                                               → AMD chip shortage
                                               → MSFT/AAPL datacenter delays

The engine is used to:
1. Augment DeepSeek prompts with relevant supply chain context
2. Auto-generate cascade signal effects when a primary signal fires
"""

import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class Company:
    """A tracked company in the supply chain graph."""
    ticker: str
    name: str
    sector: str
    role: str
    region: str = "us"
    weight: float = 1.0
    note: Optional[str] = None


class SupplyChainGraph:
    """Directed graph of semiconductor supply chain relationships.

    Nodes are companies (tickers). Edges represent:
    - supplies_to: A → B means A sells to B
    - competes_with: A ↔ B means they compete
    """

    def __init__(self, companies: list[Company], edges: dict):
        """
        Args:
            companies: List of Company dataclasses.
            edges: {ticker: {depends_on: [...], supplies_to: [...], competes_with: [...]}}
        """
        self.companies: dict[str, Company] = {
            c.ticker: c for c in companies
        }
        self.edges = edges

        # Build reverse indexes for fast lookup
        self._suppliers: dict[str, list[str]] = {}  # who supplies ticker
        self._customers: dict[str, list[str]] = {}  # who ticker sells to
        self._competitors: dict[str, list[str]] = {}

        for ticker, relations in edges.items():
            self._suppliers[ticker] = relations.get("depends_on", [])
            self._customers[ticker] = relations.get("supplies_to", [])
            self._competitors[ticker] = relations.get("competes_with", [])

    def get_company(self, ticker: str) -> Optional[Company]:
        """Get company info by ticker."""
        return self.companies.get(ticker)

    def get_suppliers(self, ticker: str) -> list[str]:
        """Who supplies to this company?"""
        return self._suppliers.get(ticker, [])

    def get_customers(self, ticker: str) -> list[str]:
        """Who does this company sell to?"""
        return self._customers.get(ticker, [])

    def get_competitors(self, ticker: str) -> list[str]:
        """Who competes with this company?"""
        return self._competitors.get(ticker, [])

    def get_impacted(
        self, ticker: str, direction: str
    ) -> list[tuple[str, str, str]]:
        """Get all companies impacted by an event at ticker.

        Args:
            ticker: The company where the event happened.
            direction: "bullish" or "bearish"

        Returns:
            List of (impacted_ticker, impact_direction, reason) tuples.
        """
        impacts: list[tuple[str, str, str]] = []
        company = self.get_company(ticker)
        if not company:
            return impacts

        # Bullish for a company → bullish for its suppliers (more orders)
        # Bullish for a company → bullish for its customers (better supply)
        # Bullish for a company → bearish for its competitors (market share loss)
        # Bearish reverses all of the above.

        same_dir = direction  # "bullish" or "bearish"
        opposite_dir = "bearish" if direction == "bullish" else "bullish"

        # Impact on suppliers: same direction
        for supplier in self._suppliers.get(ticker, []):
            sup = self.get_company(supplier)
            impacts.append((
                supplier,
                same_dir,
                f"Event at {ticker} ({company.name}) affects supplier "
                f"{sup.name if sup else supplier}"
            ))

        # Impact on customers: same direction
        for customer in self._customers.get(ticker, []):
            cust = self.get_company(customer)
            impacts.append((
                customer,
                same_dir,
                f"Event at {ticker} ({company.name}) affects customer "
                f"{cust.name if cust else customer}"
            ))

        # Impact on competitors: opposite direction
        for competitor in self._competitors.get(ticker, []):
            comp = self.get_company(competitor)
            impacts.append((
                competitor,
                opposite_dir,
                f"Event at {ticker} ({company.name}) affects competitor "
                f"{comp.name if comp else competitor}"
            ))

        return impacts

    def generate_context_text(self, ticker: str) -> str:
        """Generate a concise supply chain context string for DeepSeek prompts.

        Example:
            "NVDA (NVIDIA) — fabless AI GPU designer
             Suppliers: TSM, SK Hynix, Micron
             Customers: MSFT, GOOGL, AMZN, META
             Competitors: AMD, INTC, AVGO"
        """
        company = self.get_company(ticker)
        if not company:
            return ""

        suppliers = self.get_suppliers(ticker)
        customers = self.get_customers(ticker)
        competitors = self.get_competitors(ticker)

        lines = [
            f"{ticker} ({company.name}) — {company.role.replace('_', ' ')}",
            f"Sector: {company.sector} | Region: {company.region}",
        ]
        if suppliers:
            lines.append(f"Suppliers: {', '.join(suppliers)}")
        if customers:
            lines.append(f"Customers: {', '.join(customers)}")
        if competitors:
            lines.append(f"Competitors: {', '.join(competitors)}")
        if company.note:
            lines.append(f"Note: {company.note}")

        return "\n".join(lines)
