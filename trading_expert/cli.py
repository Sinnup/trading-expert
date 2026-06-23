"""
Trading Expert CLI — Click-based terminal interface.

Usage:
    uv run trading-agent analyze --ticker NVDA
    uv run trading-agent watch
    uv run trading-agent daily
    uv run trading-agent signals --ticker NVDA
    uv run trading-agent portfolio
    uv run trading-agent backtest
"""

import click
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("trading-expert")


@click.group()
@click.version_option(version="0.1.0", prog_name="trading-expert")
def cli():
    """Trading Expert — AI-powered trading advisor for semiconductor stocks.

    Uses DeepSeek API to analyze news sentiment and supply chain cascades,
    sends buy/sell/hold alerts to your Android phone via Telegram.
    """
    pass


@cli.command()
@click.option("--ticker", "-t", help="Single ticker to analyze (e.g., NVDA)")
@click.option("--all", "all_tickers", is_flag=True, help="Analyze all tracked tickers")
def analyze(ticker: str | None, all_tickers: bool):
    """Run sentiment analysis on one or all tracked tickers.

    Fetches recent news, runs sentiment analysis through DeepSeek,
    and generates buy/sell/hold signals with cascade effects.
    """
    if all_tickers:
        click.echo("🔄 Analyzing all tracked tickers...")
        # TODO: Phase 3 — iterate all tickers in companies.yaml
    elif ticker:
        click.echo(f"🔄 Analyzing {ticker}...")
        # TODO: Phase 3 — run pipeline for single ticker
    else:
        click.echo("Please specify --ticker or --all", err=True)
        return

    click.echo("✅ Analysis complete. Check Telegram for alerts or run 'trading-agent signals'")


@cli.command()
def watch():
    """Start intraday monitoring mode.

    Fetches news every 15 minutes during market hours,
    runs analysis, and sends alerts. Runs until interrupted.
    """
    click.echo("👀 Starting intraday monitoring...")
    click.echo("   Press Ctrl+C to stop.")
    # TODO: Phase 5 — start Celery worker + beat


@cli.command()
def daily():
    """Run the daily summary analysis.

    Aggregates all signals from the day, ranks tickers,
    and sends a comprehensive report via Telegram.
    """
    click.echo("📊 Running daily summary...")
    # TODO: Phase 5


@cli.command()
@click.option("--ticker", "-t", help="Filter by ticker (e.g., NVDA)")
@click.option("--days", "-d", default=30, help="Number of days to look back")
def signals(ticker: str | None, days: int):
    """Show recent trading signals and their outcomes."""
    click.echo(f"📋 Signals from the last {days} days:")
    # TODO: Phase 5 — query signals table and display


@cli.command()
def portfolio():
    """Show paper trading portfolio performance."""
    click.echo("💰 Paper Trading Portfolio:")
    # TODO: Phase 5


@cli.command()
@click.option("--days", "-d", default=90, help="Number of days to backtest")
def backtest(days: int):
    """Check signal accuracy over the last N days."""
    click.echo(f"📐 Backtesting signals from last {days} days...")
    # TODO: Phase 6


if __name__ == "__main__":
    cli()
