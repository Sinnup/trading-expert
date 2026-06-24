"""News fetchers for NewsAPI, GNews, and RSS feeds.

Each fetcher handles one news source API, normalizing articles into the
standard RawArticle format. All fetchers share dedup and ticker matching
from BaseFetcher.

RSS feeds are unlimited and free — they are the primary high-frequency source.
NewsAPI and GNews provide broader coverage but have free-tier rate limits.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import feedparser
import httpx
from .base import BaseFetcher, RawArticle

logger = logging.getLogger(__name__)


class RSSFetcher(BaseFetcher):
    """Fetches articles from RSS/Atom feeds. Unlimited and free."""

    source_name = "rss"
    source_tier = 2

    def __init__(self, feeds: list[dict]):
        """
        Args:
            feeds: List of {url, tier} dicts from config.
        """
        self.feeds = feeds

    async def fetch(
        self, tickers: list[str], since: Optional[datetime] = None
    ) -> list[RawArticle]:
        """Fetch from all configured RSS feeds."""
        articles: list[RawArticle] = []
        since = since or datetime.now(timezone.utc) - timedelta(hours=6)

        async with httpx.AsyncClient(timeout=30.0) as client:
            for feed_config in self.feeds:
                url = feed_config["url"]
                tier = feed_config.get("tier", 2)
                try:
                    response = await client.get(url)
                    response.raise_for_status()
                    feed = feedparser.parse(response.text)

                    for entry in feed.entries:
                        pub_date = None
                        if hasattr(entry, "published_parsed") and entry.published_parsed:
                            pub_date = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)

                        # Skip old articles
                        if pub_date and pub_date < since:
                            continue

                        article = RawArticle(
                            title=entry.get("title", ""),
                            source=self._extract_source(entry, url),
                            source_tier=tier,
                            url=entry.get("link", ""),
                            published_at=pub_date,
                            text=self._extract_text(entry),
                        )
                        articles.append(article)

                except Exception as e:
                    logger.warning(f"RSS fetch failed for {url}: {e}")
                    continue

        logger.info(f"RSS: fetched {len(articles)} articles from {len(self.feeds)} feeds")
        return self.deduplicate(articles)

    @staticmethod
    def _extract_source(entry: dict, feed_url: str) -> str:
        """Extract source name from feed entry."""
        if "source" in entry:
            return entry.source.get("title", feed_url)
        return feed_url

    @staticmethod
    def _extract_text(entry: dict) -> str:
        """Extract full text from an RSS entry."""
        # Prefer content, fall back to summary
        if "content" in entry:
            return entry.content[0].get("value", "")
        if "summary" in entry:
            return entry.summary
        return ""


class NewsAPIFetcher(BaseFetcher):
    """Fetches from NewsAPI (newsapi.org). Free tier: 100 req/day.

    Batches all tickers into one OR query to stay within rate limits.
    With the 24/7 schedule, this uses ~48 of 100 daily requests.
    """

    source_name = "newsapi"
    source_tier = 2

    def __init__(self, api_key: str, max_tickers_per_query: int = 20):
        """
        Args:
            api_key: NewsAPI key.
            max_tickers_per_query: Split into batches if more tickers than this.
        """
        self.api_key = api_key
        self.base_url = "https://newsapi.org/v2/everything"
        self.max_tickers_per_query = max_tickers_per_query

    async def fetch(
        self, tickers: list[str], since: Optional[datetime] = None
    ) -> list[RawArticle]:
        """Fetch articles matching ticker keywords (batched OR queries)."""
        articles: list[RawArticle] = []
        since = since or datetime.now(timezone.utc) - timedelta(hours=6)

        # Split into batches to keep query strings reasonable
        batches = [
            tickers[i:i + self.max_tickers_per_query]
            for i in range(0, len(tickers), self.max_tickers_per_query)
        ]

        async with httpx.AsyncClient(timeout=30.0) as client:
            for batch in batches:
                query = " OR ".join(batch)
                try:
                    response = await client.get(
                        self.base_url,
                        params={
                            "q": query,
                            "from": since.strftime("%Y-%m-%dT%H:%M:%S"),
                            "sortBy": "publishedAt",
                            "language": "en",
                            "pageSize": 20,
                            "apiKey": self.api_key,
                        },
                    )
                    response.raise_for_status()
                    data = response.json()

                    if data.get("status") != "ok":
                        logger.warning(f"NewsAPI error: {data.get('message')}")
                        continue

                    for item in data.get("articles", []):
                        pub_date = None
                        if item.get("publishedAt"):
                            try:
                                pub_date = datetime.fromisoformat(
                                    item["publishedAt"].replace("Z", "+00:00")
                                )
                            except ValueError:
                                pass

                        article = RawArticle(
                            title=item.get("title", ""),
                            source=item.get("source", {}).get("name", "newsapi"),
                            source_tier=self._classify_source(
                                item.get("source", {}).get("name", "")
                            ),
                            url=item.get("url", ""),
                            published_at=pub_date,
                            text=item.get("description", "") or item.get("content", ""),
                        )
                        articles.append(article)

                except Exception as e:
                    logger.warning(f"NewsAPI fetch failed for batch: {e}")
                    continue

        logger.info(f"NewsAPI: fetched {len(articles)} articles in {len(batches)} request(s)")
        return self.deduplicate(articles)

    @staticmethod
    def _classify_source(source_name: str) -> int:
        """Classify source credibility: 1=top-tier, 2=good, 3=unknown."""
        top_tier = {"reuters", "bloomberg", "wall street journal", "financial times", "wsj"}
        good_tier = {"cnbc", "marketwatch", "seeking alpha", "barrons", "investor's business daily"}

        name_lower = source_name.lower()
        if any(t in name_lower for t in top_tier):
            return 1
        if any(t in name_lower for t in good_tier):
            return 2
        return 3


class GNewsFetcher(BaseFetcher):
    """Fetches from GNews (gnews.io). Free tier: 100 req/day, 10 articles/req.

    Batches all tickers into a single OR query to stay within rate limits.
    With the new 24/7 schedule (every 30 min = 48 fetches/day), this uses
    ~48-96 of the 100 daily requests.
    """

    source_name = "gnews"
    source_tier = 2

    def __init__(self, api_key: str, max_tickers_per_query: int = 14):
        """
        Args:
            api_key: GNews API key.
            max_tickers_per_query: Split into batches if more tickers than this.
        """
        self.api_key = api_key
        self.base_url = "https://gnews.io/api/v4/search"
        self.max_tickers_per_query = max_tickers_per_query

    async def fetch(
        self, tickers: list[str], since: Optional[datetime] = None
    ) -> list[RawArticle]:
        """Fetch articles from GNews for all tickers (batched into OR queries)."""
        articles: list[RawArticle] = []
        since = since or datetime.now(timezone.utc) - timedelta(hours=6)

        # Split tickers into batches to keep query URL reasonable
        batches = [
            tickers[i:i + self.max_tickers_per_query]
            for i in range(0, len(tickers), self.max_tickers_per_query)
        ]

        async with httpx.AsyncClient(timeout=30.0) as client:
            for batch in batches:
                # Build OR query: "(NVIDIA OR Intel OR TSMC OR ...)"
                query = " OR ".join(batch)
                try:
                    response = await client.get(
                        self.base_url,
                        params={
                            "q": query,
                            "from": since.strftime("%Y-%m-%dT%H:%M:%SZ"),
                            "lang": "en",
                            "max": 20,
                            "token": self.api_key,
                        },
                    )
                    response.raise_for_status()
                    data = response.json()

                    for item in data.get("articles", []):
                        pub_date = None
                        if item.get("publishedAt"):
                            try:
                                pub_date = datetime.fromisoformat(
                                    item["publishedAt"].replace("Z", "+00:00")
                                )
                            except ValueError:
                                pass

                        article = RawArticle(
                            title=item.get("title", ""),
                            source=item.get("source", {}).get("name", "gnews"),
                            source_tier=2,
                            url=item.get("url", ""),
                            published_at=pub_date,
                            text=item.get("description", "") or item.get("content", ""),
                        )
                        articles.append(article)

                except Exception as e:
                    logger.warning(f"GNews fetch failed for batch: {e}")
                    continue

        logger.info(f"GNews: fetched {len(articles)} articles in {len(batches)} request(s)")
        return self.deduplicate(articles)
