"""
Trading Expert CLI — Click-based terminal interface.

Usage:
    uv run trading-agent analyze --ticker NVDA
    uv run trading-agent analyze --ticker BIMBOA.MX --universe bmv
    uv run trading-agent watch
    uv run trading-agent daily --universe bmv
    uv run trading-agent signals --ticker NVDA
    uv run trading-agent portfolio
    uv run trading-agent backtest

Universes:
    semiconductor (default) — 29 semiconductor/tech tickers
    bmv                      — 6 Mexican BMV tickers
    all                      — Both universes
"""

import os
import click
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("trading-expert")

UNIVERSE_CHOICES = ["semiconductor", "bmv", "all"]

# Map universe → config files
UNIVERSE_CONFIG = {
    "semiconductor": {
        "companies": "companies.yaml",
        "settings": "settings.yaml",
        "label": "Semiconductor & Tech",
    },
    "bmv": {
        "companies": "companies_bmv.yaml",
        "settings": "settings_bmv.yaml",
        "label": "BMV (Mexican Stocks)",
    },
}


def _detect_universe(ticker: str | None, universe: str | None) -> str:
    """Auto-detect universe from ticker suffix if not explicitly set.

    - .MX suffix → bmv (even if semiconductor was explicitly chosen)
    - None + no .MX → semiconductor (default)
    - "all" stays as "all"
    """
    if universe == "all":
        return "all"
    if ticker and ticker.upper().endswith(".MX"):
        return "bmv"
    if universe:
        return universe
    return "semiconductor"


def _load_universe_config(universe: str):
    """Load config for a specific universe. Returns (companies, settings)."""
    import yaml

    config_dir = os.path.join(os.path.dirname(__file__), "..", "config")
    uc = UNIVERSE_CONFIG.get(universe)
    if not uc:
        raise click.BadParameter(f"Unknown universe: {universe}")

    with open(os.path.join(config_dir, uc["companies"])) as f:
        companies = yaml.safe_load(f)
    with open(os.path.join(config_dir, uc["settings"])) as f:
        settings = yaml.safe_load(f)

    return companies, settings


@click.group()
@click.version_option(version="0.2.0", prog_name="trading-expert")
def cli():
    """Trading Expert — AI-powered trading advisor for semiconductor and BMV stocks.

    Uses DeepSeek API to analyze news sentiment and supply chain cascades,
    sends buy/sell/hold alerts to your Android phone via Telegram.

    Supports two universes: semiconductor (default) and bmv (Mexican stocks).
    """
    pass


@cli.command()
@click.option("--ticker", "-t", help="Single ticker to analyze (e.g., NVDA, BIMBOA.MX)")
@click.option("--all", "all_tickers", is_flag=True, help="Analyze all tracked tickers")
@click.option(
    "--universe", "-u",
    default=None,
    type=click.Choice(UNIVERSE_CHOICES),
    help="Market universe to operate on (auto-detected from .MX suffix)",
)
def analyze(ticker: str | None, all_tickers: bool, universe: str):
    """Run sentiment analysis on one or all tracked tickers.

    Fetches recent news, runs sentiment analysis through DeepSeek,
    and generates buy/sell/hold signals with cascade effects
    (semiconductor universe only).

    Tickername ending in .MX are auto-routed to the BMV universe.
    """
    # Auto-detect universe from ticker suffix
    universe = _detect_universe(ticker, universe)

    if all_tickers:
        click.echo(f"🔄 Analyzing all tickers in {UNIVERSE_CONFIG[universe]['label']}...")
        # TODO: Phase 3 — iterate all tickers in config
    elif ticker:
        ticker = ticker.upper()
        companies, settings = _load_universe_config(universe)
        tickers = [c["ticker"] for c in companies.get("tickers", [])]

        if ticker not in tickers:
            available = ", ".join(tickers[:10])
            click.echo(
                f"⚠️  {ticker} not found in {UNIVERSE_CONFIG[universe]['label']} universe.\n"
                f"   Tracked tickers (first 10): {available}{'...' if len(tickers) > 10 else ''}",
                err=True,
            )
            return

        click.echo(
            f"🔄 Analyzing {ticker} in {UNIVERSE_CONFIG[universe]['label']} universe..."
        )
        # TODO: Phase 3 — run pipeline for single ticker
    else:
        click.echo("Please specify --ticker or --all", err=True)
        return

    click.echo(
        "✅ Analysis complete. Check Telegram for alerts or run "
        f"'trading-agent signals --ticker {ticker or ''} --universe {universe}'"
    )


@cli.command()
@click.option(
    "--universe", "-u",
    default=None,
    type=click.Choice(UNIVERSE_CHOICES),
    help="Market universe to monitor",
)
def watch(universe: str):
    """Start intraday monitoring mode.

    Fetches news every 30 minutes, runs analysis, and sends alerts.
    Uses Celery workers for background processing.
    """
    if universe == "all":
        click.echo("👀 Starting intraday monitoring for ALL universes...")
    else:
        click.echo(
            f"👀 Starting intraday monitoring for {UNIVERSE_CONFIG[universe]['label']}..."
        )
    click.echo("   Celery Beat handles scheduling — ensure docker compose is running.")
    click.echo("   Press Ctrl+C to stop.")


@cli.command()
@click.option(
    "--universe", "-u",
    default=None,
    type=click.Choice(UNIVERSE_CHOICES),
    help="Market universe for daily summary",
)
def daily(universe: str):
    """Run the daily summary analysis.

    Aggregates all signals from the day, ranks tickers,
    and sends a comprehensive report via Telegram.
    """
    if universe == "all":
        click.echo("📊 Running daily summary for ALL universes...")
    else:
        click.echo(
            f"📊 Running daily summary for {UNIVERSE_CONFIG[universe]['label']}..."
        )


@cli.command()
@click.option("--ticker", "-t", help="Filter by ticker (e.g., NVDA, BIMBOA.MX)")
@click.option("--days", "-d", default=30, help="Number of days to look back")
@click.option(
    "--universe", "-u",
    default=None,
    type=click.Choice(UNIVERSE_CHOICES),
    help="Market universe (auto-detected from .MX suffix)",
)
def signals(ticker: str | None, days: int, universe: str):
    """Show recent trading signals and their outcomes.

    Tickername ending in .MX are auto-routed to the BMV universe.
    """
    universe = _detect_universe(ticker, universe)
    click.echo(
        f"📋 {UNIVERSE_CONFIG[universe]['label']} signals "
        f"from the last {days} days:"
    )
    if ticker:
        click.echo(f"   Filtered to: {ticker.upper()}")


@cli.command()
@click.option(
    "--universe", "-u",
    default=None,
    type=click.Choice(UNIVERSE_CHOICES),
    help="Market universe for portfolio",
)
def portfolio(universe: str):
    """Show paper trading portfolio performance."""
    if universe == "all":
        click.echo("💰 Paper Trading Portfolio (All Universes):")
    else:
        click.echo(
            f"💰 Paper Trading Portfolio ({UNIVERSE_CONFIG[universe]['label']}):"
        )


@cli.command()
@click.option("--days", "-d", default=90, help="Number of days to backtest")
@click.option(
    "--universe", "-u",
    default=None,
    type=click.Choice(UNIVERSE_CHOICES),
    help="Market universe for backtest",
)
def backtest(days: int, universe: str):
    """Check signal accuracy over the last N days."""
    if universe == "all":
        click.echo(f"📐 Backtesting ALL universes from last {days} days...")
    else:
        click.echo(
            f"📐 Backtesting {UNIVERSE_CONFIG[universe]['label']} "
            f"from last {days} days..."
        )


if __name__ == "__main__":
    cli()
