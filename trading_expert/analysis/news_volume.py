"""News-volume scoring — how unusual is a ticker's coverage this cycle?

The signal scorer accepts a ``news_volume_zscore`` per ticker: a positive value
means the ticker is generating more news than its peers right now, which should
amplify the signal. We compute it as a z-score of each ticker's article count
*within the current fetch cycle*, so a ticker dominating the news batch scores
high while quietly-covered tickers score near zero.

This is intentionally self-contained (no DB history dependency): each intraday
cycle is scored against its own distribution, which is cheap, deterministic, and
easy to reason about.
"""

import logging

logger = logging.getLogger(__name__)


def compute_batch_volume_zscores(matched_counts: dict[str, int]) -> dict[str, float]:
    """Z-score each ticker's article count against the batch distribution.

    Args:
        matched_counts: ``{ticker: number_of_articles_mentioning_it}`` for the
            current fetch cycle.

    Returns:
        ``{ticker: zscore}``. Returns all-zero scores when there are fewer than
        two tickers or when every ticker has the same count (no spread to
        measure), so the news-volume term stays neutral rather than noisy.
    """
    if len(matched_counts) < 2:
        return {ticker: 0.0 for ticker in matched_counts}

    counts = list(matched_counts.values())
    mean = sum(counts) / len(counts)
    variance = sum((c - mean) ** 2 for c in counts) / len(counts)
    std = variance ** 0.5

    if std == 0:
        return {ticker: 0.0 for ticker in matched_counts}

    return {ticker: (count - mean) / std for ticker, count in matched_counts.items()}


def count_articles_per_ticker(
    matched_tickers_per_article: list[list[str]],
) -> dict[str, int]:
    """Tally how many articles mention each ticker.

    Args:
        matched_tickers_per_article: One list of matched tickers per article
            (e.g. ``[r.matched_tickers for r in prefilter_results]``).

    Returns:
        ``{ticker: count}``.
    """
    counts: dict[str, int] = {}
    for tickers in matched_tickers_per_article:
        for ticker in tickers:
            counts[ticker] = counts.get(ticker, 0) + 1
    return counts
