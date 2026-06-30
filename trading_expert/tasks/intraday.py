"""24/7 monitoring task — fetches news, filters, analyzes, and alerts.

Runs every 30 minutes around the clock to cover all time zones:
- Asian hours: TSMC, Samsung, SK Hynix, MediaTek
- European hours: ASML, Infineon, STMicro, Arm
- US hours: NVIDIA, AMD, Intel, Apple, Microsoft, etc.

Flow:
    1. Fetch new articles from all sources (RSS, NewsAPI, GNews)
    2. Pre-filter: VADER sentiment + keyword detection
    3. Escalate qualifying articles to DeepSeek for deep analysis
    4. Score signals and check against alert thresholds
    5. Generate cascade signals for impacted companies
    6. Send Telegram alerts for signals above threshold
    7. Store everything in the database
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
from trading_expert.analysis.cascade import SupplyChainGraph
from trading_expert.analysis.signals import SignalScorer, compute_price_confirmation
from trading_expert.analysis.news_volume import (
    compute_batch_volume_zscores,
    count_articles_per_ticker,
)
from trading_expert.notifications.telegram import TelegramNotifier
from trading_expert.constants import (
    DEFAULT_ALERT_COOLDOWN_MINUTES,
    DEFAULT_MAX_ALERTS_PER_DAY,
    DEFAULT_SOURCE_CREDIBILITY,
    FETCH_LOOKBACK_HOURS,
    PREFILTER_SENTIMENT_THRESHOLD,
    SOURCE_TIER_CREDIBILITY,
)

logger = logging.getLogger(__name__)


def load_config():
    """Load configuration from YAML files + environment."""
    import yaml

    config_dir = os.path.join(os.path.dirname(__file__), "..", "..", "config")

    with open(os.path.join(config_dir, "settings.yaml")) as f:
        settings = yaml.safe_load(f)
    with open(os.path.join(config_dir, "companies.yaml")) as f:
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
        # Add ADR if present
        adr = c.get("adr")
        if adr and isinstance(adr, str):
            variants.append(adr)
        tickers_map[ticker] = variants
    return tickers_map


@shared_task(bind=True, max_retries=2, default_retry_delay=60)
def intraday_fetch(self):
    """Main intraday task: fetch → filter → analyze → alert.

    This is scheduled by Celery Beat every 15 minutes during market hours.
    """
    logger.info("Starting intraday fetch cycle...")
    return asyncio.run(_intraday_fetch_async())


async def _intraday_fetch_async():
    """Async implementation of the intraday fetch pipeline."""
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
            fetchers.append(NewsAPIFetcher(newsapi_key))
        if gnews_key:
            fetchers.append(GNewsFetcher(gnews_key))

        since = datetime.now(timezone.utc) - timedelta(hours=FETCH_LOOKBACK_HOURS)
        all_articles = []
        for fetcher in fetchers:
            try:
                articles = await fetcher.fetch(all_tickers, since=since)
                all_articles.extend(articles)
            except Exception as e:
                logger.warning(f"Fetcher {fetcher.source_name} failed: {e}")

        logger.info(f"Fetched {len(all_articles)} total articles")

        if not all_articles:
            return {"status": "ok", "articles": 0, "signals": 0}

        # ── Step 2: Pre-filter ─────────────────────────────────────────
        prefilter = Prefilter(
            tickers_map=tickers_map,
            urgency_keywords=thresholds_config.get("urgency_keywords", {}),
            sentiment_threshold=PREFILTER_SENTIMENT_THRESHOLD,
        )
        results = prefilter.score_batch(all_articles)

        escalated = [r for r in results if r.should_escalate]
        logger.info(f"Prefilter: {len(escalated)}/{len(results)} articles escalated")

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
            logger.warning("No DEEPSEEK_API_KEY — skipping deep analysis")
            return {"status": "ok", "articles": len(all_articles), "signals": 0}

        analyzer = DeepSeekAnalyzer(api_key=deepseek_key)

        # Build supply chain context
        from trading_expert.analysis.cascade import Company
        companies_list = [
            Company(
                ticker=c["ticker"],
                name=c.get("name", ""),
                sector=c.get("sector", ""),
                role=c.get("role", ""),
                region=c.get("region", "us"),
                weight=c.get("weight", 1.0),
                note=c.get("note"),
            )
            for c in companies_config.get("tickers", [])
        ]
        supply_chain = SupplyChainGraph(
            companies=companies_list,
            edges=companies_config.get("supply_chain", {}),
        )

        # ── Step 5: Get prices ────────────────────────────────────────
        price_fetcher = PriceFetcher()
        prices = await price_fetcher.get_batch(all_tickers)

        # ── Step 6: Analyze each escalated article ────────────────────
        signal_scorer = SignalScorer(settings.get("signals", {}))
        notifier = TelegramNotifier(
            alert_cooldown_minutes=settings.get("telegram", {}).get(
                "alert_cooldown_minutes", DEFAULT_ALERT_COOLDOWN_MINUTES
            ),
            max_alerts_per_day=settings.get("telegram", {}).get(
                "max_alerts_per_day", DEFAULT_MAX_ALERTS_PER_DAY
            ),
        )

        signals_generated = 0
        for result in escalated:
            article = next(
                (a for a in all_articles if a.article_id == result.article_id),
                None,
            )
            if not article:
                continue

            # Get supply chain context for the primary matched ticker
            primary_ticker = (
                result.matched_tickers[0] if result.matched_tickers else "UNKNOWN"
            )
            sc_context = supply_chain.generate_context_text(primary_ticker)

            # Get price context
            price = prices.get(primary_ticker)
            price_context = ""
            if price:
                price_context = (
                    f"{primary_ticker}: ${price.close:.2f} "
                    f"({price.change_pct:+.2f}%), "
                    f"P/E: {price.pe_ratio}"
                )

            # DeepSeek analysis
            article_ctx = ArticleContext(
                article_id=article.article_id,
                title=article.title,
                source=article.source,
                source_tier=article.source_tier,
                text=article.text or "",
                vader_score=result.vader_score,
                keyword_triggers=result.keyword_triggers,
            )

            deepseek_signal = await analyzer.analyze(
                article=article_ctx,
                supply_chain_context=sc_context,
                price_context=price_context,
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
                cascade_source=final_signal.cascade_source,
                cascade_parent=final_signal.cascade_parent,
                source_article_ids=final_signal.source_article_ids,
                urgency=final_signal.urgency,
                signal_type="intraday",
                price_at_signal=final_signal.price_at_signal,
            )
            session.add(db_signal)
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
                    cascade_effects=final_signal.cascade_effects,
                    risk_factors=final_signal.risk_factors,
                    suggested_timeframe=final_signal.suggested_timeframe,
                    price=final_signal.price_at_signal,
                    alert_label=final_signal.alert_label,
                )

            # Generate cascade signals
            for cascade in final_signal.cascade_effects:
                cascade_signal = signal_scorer.score_cascade(
                    cascade_effect=cascade,
                    parent_ticker=final_signal.ticker,
                    source_article_ids=final_signal.source_article_ids,
                )
                db_cascade = Signal(
                    ticker=cascade_signal.ticker,
                    score=cascade_signal.score,
                    deepseek_sentiment=cascade_signal.deepseek_sentiment,
                    deepseek_confidence="low",
                    action=cascade_signal.action,
                    reasoning=cascade_signal.reasoning,
                    cascade_source=True,
                    cascade_parent=cascade_signal.cascade_parent,
                    source_article_ids=cascade_signal.source_article_ids,
                    urgency="medium",
                    signal_type="cascade",
                )
                session.add(db_cascade)
                signals_generated += 1

                # Alert on strong cascade impacts too (secondary plays)
                if cascade_signal.should_alert:
                    await notifier.send_alert(
                        ticker=cascade_signal.ticker,
                        score=cascade_signal.score,
                        action=cascade_signal.action,
                        confidence=cascade_signal.deepseek_confidence,
                        reasoning=(
                            f"Cascade from {cascade_signal.cascade_parent}: "
                            f"{cascade_signal.reasoning}"
                        ),
                        cascade_effects=[],
                        risk_factors=[],
                        suggested_timeframe=cascade_signal.suggested_timeframe,
                        price=None,
                        alert_label=cascade_signal.alert_label,
                    )

        session.commit()

        logger.info(
            f"Intraday cycle complete: {len(all_articles)} articles, "
            f"{signals_generated} signals generated"
        )

        return {
            "status": "ok",
            "articles": len(all_articles),
            "escalated": len(escalated),
            "signals": signals_generated,
        }

    except Exception as e:
        logger.error(f"Intraday fetch failed: {e}", exc_info=True)
        session.rollback()
        raise

    finally:
        session.close()
