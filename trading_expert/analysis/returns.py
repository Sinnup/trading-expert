"""Price-return and profit-margin formulas.

Single source of truth for every percentage the system reports about price
moves and P&L. Pure functions only (no I/O) so they are trivially testable and
shared by the outcome grader, the paper portfolio and the notifiers. The same
formulas are documented for agents in ``.claude/skills/price-return-formulas``.
"""

from datetime import date, datetime, timedelta, timezone
from typing import Optional, Sequence


def as_utc(dt: datetime) -> datetime:
    """Treat a naive datetime as UTC (SQLite drops tzinfo on round-trip)."""
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def pct_return(start: Optional[float], end: Optional[float]) -> Optional[float]:
    """Simple return in percent: ``(end - start) / start * 100``.

    Returns None when either price is missing or the base is not positive.
    """
    if start is None or end is None or start <= 0:
        return None
    return (end - start) / start * 100


def horizon_target_date(signal_at: datetime, horizon_days: int) -> date:
    """Calendar date a ``horizon_days`` outcome is measured on (signal date + N)."""
    return (as_utc(signal_at) + timedelta(days=horizon_days)).date()


def split_factor_after(splits: Sequence[tuple[date, float]], after: date) -> float:
    """Product of split ratios effective strictly after ``after``.

    Yahoo closes are split-adjusted to today's share basis; multiplying one by
    this factor converts it back to the share basis of ``after`` (e.g. a 10:1
    split after a signal turns a post-split $120 close into $1,200 so it can be
    compared with the pre-split ``price_at_signal``).
    """
    factor = 1.0
    for split_date, ratio in splits:
        if split_date > after and ratio > 0:
            factor *= ratio
    return factor


def close_at_horizon(
    closes: Sequence[tuple[date, float]],
    splits: Sequence[tuple[date, float]],
    signal_at: datetime,
    horizon_days: int,
    today: date,
) -> Optional[float]:
    """Close of the first *completed* session on/after signal date + N days.

    ``closes`` must be ascending ``(session_date, split_adjusted_close)``. Only
    sessions strictly before ``today`` count as completed, so a still-trading
    bar is never graded. The result is expressed in the signal's share basis
    (see ``split_factor_after``). Returns None until the horizon has elapsed.
    """
    target = horizon_target_date(signal_at, horizon_days)
    for session_date, close in closes:
        if session_date >= today:
            return None
        if session_date >= target:
            signal_date = as_utc(signal_at).date()
            return close * split_factor_after(splits, signal_date)
    return None


def close_on_signal_date(
    closes: Sequence[tuple[date, float]],
    splits: Sequence[tuple[date, float]],
    signal_at: datetime,
) -> Optional[float]:
    """Last close on/before the signal date, in the signal's share basis."""
    signal_date = as_utc(signal_at).date()
    reference = None
    for session_date, close in closes:
        if session_date > signal_date:
            break
        reference = close
    if reference is None:
        return None
    return reference * split_factor_after(splits, signal_date)


def base_price_is_plausible(
    price_at_signal: float,
    reference_close: Optional[float],
    max_deviation: float,
) -> bool:
    """Whether a stored signal price matches the market close it claims to be.

    Guards against signals that were stored with another company's quote:
    ``|price_at_signal / reference_close - 1| <= max_deviation``. With no
    reference close (no market data on/before the signal date) the base can't
    be verified, so it is rejected.
    """
    if reference_close is None or reference_close <= 0:
        return False
    return abs(price_at_signal / reference_close - 1) <= max_deviation


def position_return_pct(avg_price: float, current_price: float) -> Optional[float]:
    """Unrealized return of a long position: ``(current - avg) / avg * 100``."""
    return pct_return(avg_price, current_price)


def profit_margin_pct(pnl: float, cost_basis: float) -> Optional[float]:
    """Profit margin on capital deployed: ``pnl / cost_basis * 100``."""
    if cost_basis <= 0:
        return None
    return pnl / cost_basis * 100
