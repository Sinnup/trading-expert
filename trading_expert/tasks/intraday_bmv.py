"""BMV intraday monitoring task — news fetch → filter → analyze → alert.

Runs independently from the semiconductor pipeline. Uses Spanish-language
news sources and BMV market schedule. Skips supply chain cascade entirely
since BMV stocks span unrelated sectors (consumer, finance, airline, retail,
telecom).

Flow:
    1. Fetch new articles from all sources (RSS, NewsAPI, GNews) in Spanish
    2. Pre-filter: VADER sentiment + BMV-specific keyword detection
    3. Escalate qualifying articles to DeepSeek for deep analysis
    4. Score signals and check against alert thresholds
    5. Send Telegram alerts for signals above threshold
    6. Store everything in the database
"""

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from celery import shared_task

from trading_expert.models import get_session, init_db
from trading_expert.models.article import Article
from trading_expert.models.signal import Signal
from trading_expert.fetchers.news import RSSFetcher, NewsAPIFetcher, GNewsFetcher
from trading_expert.fetchers.prices import PriceFetcher
from trading_expert.analysis.prefilter import Prefilter
from trading_expert.analysis.deepseek import DeepSeekAnalyzer, ArticleContext
from trading_expert.analysis.signals import SignalScorer, compute_price_confirmation
from trading_expert.analysis.calibration import load_learned_weights, merge_weights
from trading_expert.analysis.news_volume import (
    compute_batch_volume_zscores,
    count_articles_per_ticker,
)
from trading_expert.notifications.telegram import TelegramNotifier
from trading_expert.portfolio.tracker import (
    PaperPortfolio,
    compute_position_size,
    eligible_for_paper_trade,
)
from trading_expert.constants import (
    ACTION_BUY,
    DEFAULT_CONVICTION_CAP,
    DEFAULT_MAX_CONCURRENT_POSITIONS,
    DEFAULT_MAX_POSITION_FRACTION,
    DEFAULT_SOURCE_CREDIBILITY,
    DEFAULT_STRONG_ALERT_THRESHOLD,
    DEFAULT_TARGET_INVESTED_FRACTION,
    FETCH_LOOKBACK_HOURS,
    PREFILTER_SENTIMENT_THRESHOLD,
    SIGNAL_TYPE_INTRADAY_BMV,
    SOURCE_TIER_CREDIBILITY,
)

logger = logging.getLogger(__name__)


def load_config():
    """Load BMV configuration from YAML files + environment."""
    import yaml

    config_dir = os.path.join(os.path.dirname(__file__), "..", "..", "config")

    with open(os.path.join(config_dir, "settings_bmv.yaml")) as f:
        settings = yaml.safe_load(f)
    with open(os.path.join(config_dir, "companies_bmv.yaml")) as f:
        companies = yaml.safe_load(f)
    with open(os.path.join(config_dir, "thresholds.yaml")) as f:
        thresholds = yaml.safe_load(f)

    return settings, companies, thresholds


def build_tickers_map(companies_config: dict) -> dict[str, list[str]]:
    """Build ticker → [name variants] map for matching."""
    tickers_map: dict[str, list[str]] = {}
    for c in companies_config.get("tickers", []):
        ticker = str(c["ticker"])
        variants: list[str] = [ticker]
        name = c.get("name")
        if name and isinstance(name, str):
            variants.append(name)
        adr = c.get("adr")
        if adr and isinstance(adr, str):
            variants.append(adr)
        tickers_map[ticker] = variants
    return tickers_map


@shared_task(bind=True, max_retries=2, default_retry_delay=60)
def intraday_fetch_bmv(self):
    """BMV intraday task: fetch → filter → analyze → alert.

    Scheduled by Celery Beat every 30 minutes. Uses Spanish-language
    news sources. No supply chain cascade — BMV stocks have no
    semiconductor supply chain relationships.
    """
    logger.info("Starting BMV intraday fetch cycle...")
    return asyncio.run(_intraday_fetch_bmv_async())


async def _intraday_fetch_bmv_async():
    """Async implementation of the BMV intraday fetch pipeline."""
    # Load config
    settings, companies_config, thresholds_config = load_config()

    # Init DB
    init_db()
    session = get_session()

    try:
        # Build ticker map
        tickers_map = build_tickers_map(companies_config)
        all_tickers = list(tickers_map.keys())

        # ── Step 1: Fetch articles ─────────────────────────────────────
        rss_feeds = settings.get("rss_feeds", [])
        newsapi_key = os.getenv("NEWSAPI_KEY", "")
        gnews_key = os.getenv("GNEWS_API_KEY", "")

        fetchers = [RSSFetcher(rss_feeds)]
        if newsapi_key:
            fetchers.append(NewsAPIFetcher(newsapi_key, language="es"))
        if gnews_key:
            fetchers.append(GNewsFetcher(gnews_key, language="es"))

        since = datetime.now(timezone.utc) - timedelta(hours=FETCH_LOOKBACK_HOURS)
        all_articles = []
        for fetcher in fetchers:
            try:
                articles = await fetcher.fetch(all_tickers, since=since)
                all_articles.extend(articles)
            except Exception as e:
                logger.warning(f"BMV fetcher {fetcher.source_name} failed: {e}")

        logger.info(f"BMV: fetched {len(all_articles)} total articles")

        if not all_articles:
            return {"status": "ok", "articles": 0, "signals": 0}

        # ── Step 2: Pre-filter ─────────────────────────────────────────
        prefilter = Prefilter(
            tickers_map=tickers_map,
            urgency_keywords=settings.get("urgency_keywords", {}),
            sentiment_threshold=PREFILTER_SENTIMENT_THRESHOLD,
        )
        results = prefilter.score_batch(all_articles)

        escalated = [r for r in results if r.should_escalate]
        logger.info(f"BMV prefilter: {len(escalated)}/{len(results)} articles escalated")

        if not escalated:
            return {"status": "ok", "articles": len(all_articles), "signals": 0}

        # News-volume z-scores: how unusual is each ticker's coverage this cycle?
        volume_zscores = compute_batch_volume_zscores(
            count_articles_per_ticker([r.matched_tickers for r in results])
        )

        # ── Step 3: Store articles in DB ──────────────────────────────
        for article in all_articles:
            result = next((r for r in results if r.article_id == article.article_id), None)
            if not result:
                continue

            db_article = Article(
                id=article.article_id,
                title=article.title,
                source=article.source,
                source_tier=article.source_tier,
                url=article.url,
                published_at=article.published_at,
                raw_text=article.text,
                matched_tickers=result.matched_tickers,
                vader_score=result.vader_score,
                keyword_triggers=result.keyword_triggers,
                passed_prefilter=result.should_escalate,
            )
            session.merge(db_article)

        session.commit()

        # ── Step 4: DeepSeek analysis ─────────────────────────────────
        deepseek_key = os.getenv("DEEPSEEK_API_KEY", "")
        if not deepseek_key:
            logger.warning("No DEEPSEEK_API_KEY — skipping BMV deep analysis")
            return {"status": "ok", "articles": len(all_articles), "signals": 0}

        analyzer = DeepSeekAnalyzer(api_key=deepseek_key)

        # Reasoner escalation config (see intraday.py for rationale).
        deepseek_cfg = settings.get("deepseek", {})
        reasoner_model = deepseek_cfg.get("model_reasoning", "deepseek-reasoner")
        reasoning_threshold = deepseek_cfg.get("reasoning_threshold", 1.1)

        # ── Step 5: Get prices ────────────────────────────────────────
        price_fetcher = PriceFetcher()
        prices = await price_fetcher.get_batch(all_tickers)

        # ── Step 6: Analyze each escalated article ────────────────────
        # Apply the learned-weights overlay on top of the YAML defaults.
        signals_cfg = dict(settings.get("signals", {}))
        signals_cfg["weights"] = merge_weights(
            signals_cfg.get("weights", {}), load_learned_weights("bmv")
        )
        signal_scorer = SignalScorer(signals_cfg)
        notifier = TelegramNotifier.from_settings(settings.get("telegram", {}))

        signals_generated = 0
        # (signal_id, ticker, action, price, score)
        pending_paper_trades: list[tuple[int, str, str, float, float]] = []
        for result in escalated:
            article = next(
                (a for a in all_articles if a.article_id == result.article_id),
                None,
            )
            if not article:
                continue

            primary_ticker = (
                result.matched_tickers[0] if result.matched_tickers else "UNKNOWN"
            )

            # Get price context
            price = prices.get(primary_ticker)
            price_context = ""
            if price:
                price_context = (
                    f"{primary_ticker}: ${price.close:.2f} "
                    f"({price.change_pct:+.2f}%), "
                    f"P/E: {price.pe_ratio}"
                )

            # DeepSeek analysis — no supply chain context for BMV stocks
            # (these are consumer/financial/airline/telecom, no semiconductor
            # supply chain relationships)
            article_ctx = ArticleContext(
                article_id=article.article_id,
                title=article.title,
                source=article.source,
                source_tier=article.source_tier,
                text=article.text or "",
                vader_score=result.vader_score,
                keyword_triggers=result.keyword_triggers,
            )

            # Escalate high-conviction prefilter hits to the reasoner.
            use_reasoner = abs(result.vader_score) >= reasoning_threshold
            deepseek_signal = await analyzer.analyze(
                article=article_ctx,
                supply_chain_context="",  # No cascade for BMV
                price_context=price_context,
                model=reasoner_model if use_reasoner else None,
            )

            # Compute source credibility from the article's source tier
            source_cred = SOURCE_TIER_CREDIBILITY.get(
                article.source_tier, DEFAULT_SOURCE_CREDIBILITY
            )

            # News-volume z-score and price confirmation for this signal's ticker
            news_volume_zscore = volume_zscores.get(deepseek_signal.primary_ticker, 0.0)
            price_confirmation = compute_price_confirmation(
                sentiment=deepseek_signal.sentiment,
                change_pct=price.change_pct if price else None,
            )

            # Score the signal
            final_signal = signal_scorer.score(
                deepseek=deepseek_signal,
                news_volume_zscore=news_volume_zscore,
                source_credibility=source_cred,
                price_confirmation=price_confirmation,
                price=price.close if price else None,
            )

            # Store signal in DB
            db_signal = Signal(
                ticker=final_signal.ticker,
                score=final_signal.score,
                deepseek_sentiment=final_signal.deepseek_sentiment,
                deepseek_confidence=final_signal.deepseek_confidence,
                action=final_signal.action,
                suggested_timeframe=final_signal.suggested_timeframe,
                reasoning=final_signal.reasoning,
                risk_factors=final_signal.risk_factors,
                cascade_source=False,  # BMV has no cascade
                cascade_parent=None,
                source_article_ids=final_signal.source_article_ids,
                urgency=final_signal.urgency,
                signal_type=SIGNAL_TYPE_INTRADAY_BMV,  # Distinguish from semiconductor signals
                price_at_signal=final_signal.price_at_signal,
                factors=final_signal.factors,
            )
            session.add(db_signal)
            session.flush()  # Assign db_signal.id before paper trade FK reference
            signals_generated += 1

            # Update article with DeepSeek result
            db_article = session.get(Article, article.article_id)
            if db_article:
                db_article.deepseek_analysis = {
                    "sentiment": deepseek_signal.sentiment,
                    "confidence": deepseek_signal.confidence,
                    "reasoning": deepseek_signal.reasoning,
                    "action": deepseek_signal.action,
                }
                db_article.deepseek_model = deepseek_signal.model_used

            # Send alert if threshold crossed
            if final_signal.should_alert:
                await notifier.send_alert(
                    ticker=final_signal.ticker,
                    score=final_signal.score,
                    action=final_signal.action,
                    confidence=final_signal.deepseek_confidence,
                    reasoning=final_signal.reasoning,
                    cascade_effects=[],  # BMV: no cascade effects
                    risk_factors=final_signal.risk_factors,
                    suggested_timeframe=final_signal.suggested_timeframe,
                    price=final_signal.price_at_signal,
                    alert_label=final_signal.alert_label,
                )
                # Paper-trade on the signal *decision* (should_alert), not on
                # whether the Telegram alert physically sent: a non-actionable
                # hold for the same ticker often trips the notifier cooldown
                # first and would suppress the actionable buy/sell alert.
                # Duplicate stacking is prevented at the position level below.
                if eligible_for_paper_trade(
                    final_signal.action,
                    final_signal.price_at_signal,
                    final_signal.should_alert,
                ):
                    pending_paper_trades.append((
                        db_signal.id,
                        final_signal.ticker,
                        final_signal.action,
                        final_signal.price_at_signal,
                        final_signal.score,
                    ))

        session.commit()

        # Execute paper trades for alert-triggering signals (signals committed above).
        if pending_paper_trades:
            portfolio = PaperPortfolio()
            price_map = {t: p.close for t, p in prices.items() if p}
            portfolio_cfg = settings.get("portfolio", {})
            alert_threshold = signals_cfg.get("thresholds", {}).get(
                "strong_alert", DEFAULT_STRONG_ALERT_THRESHOLD
            )
            for sig_id, ticker, action, price, score in pending_paper_trades:
                if action == ACTION_BUY:
                    # One open position per ticker: skip re-buying the same
                    # re-escalated news every cycle. A later sell frees the ticker.
                    if portfolio.has_open_position(ticker):
                        logger.info(f"PAPER BUY skipped: already holding {ticker}")
                        continue
                    summary = portfolio.get_summary(price_map=price_map)
                    quantity = compute_position_size(
                        equity=summary["total_value"],
                        cash=summary["cash"],
                        price=price,
                        score=score,
                        alert_threshold=alert_threshold,
                        target_invested_fraction=portfolio_cfg.get(
                            "target_invested_fraction", DEFAULT_TARGET_INVESTED_FRACTION
                        ),
                        max_positions=portfolio_cfg.get(
                            "max_concurrent_positions", DEFAULT_MAX_CONCURRENT_POSITIONS
                        ),
                        conviction_cap=portfolio_cfg.get(
                            "conviction_cap", DEFAULT_CONVICTION_CAP
                        ),
                        max_position_fraction=portfolio_cfg.get(
                            "max_position_fraction", DEFAULT_MAX_POSITION_FRACTION
                        ),
                    )
                    if quantity < 1:
                        logger.info(
                            f"PAPER BUY skipped: {ticker} @ ${price:.2f} — "
                            f"sizing yields <1 share (low cash or price too high)"
                        )
                        continue
                    portfolio.execute_buy(ticker, price, quantity, signal_id=sig_id)
                else:
                    # Sell signal → fully exit the open position (long-only).
                    quantity = portfolio.open_quantity(ticker)
                    if quantity < 1:
                        continue
                    portfolio.execute_sell(ticker, price, quantity, signal_id=sig_id)

        logger.info(
            f"BMV intraday cycle complete: {len(all_articles)} articles, "
            f"{signals_generated} signals generated"
        )

        return {
            "status": "ok",
            "articles": len(all_articles),
            "escalated": len(escalated),
            "signals": signals_generated,
        }

    except Exception as e:
        logger.error(f"BMV intraday fetch failed: {e}", exc_info=True)
        session.rollback()
        raise

    finally:
        session.close()
