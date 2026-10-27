---
name: price-return-formulas
description: Exact formulas Trading Expert uses for stock prices, daily change, per-period (1/7/30/90-day) signal returns, split handling, currency conversion, portfolio P&L and profit margins. Use whenever reading, changing, debugging or explaining any price, return, P&L, margin, win-rate or backtest number in this repo, or when answering "how is X calculated".
---

# Price, return and profit-margin formulas

All formulas live as pure functions in `trading_expert/analysis/returns.py`.
Reuse them; never re-implement a percentage inline. If you change a formula,
change it there, update this skill and `tests/test_returns.py`.

## 1. Where prices come from (`fetchers/prices.py`, Yahoo Finance via yfinance)

| Use | Call | Notes |
|---|---|---|
| Live quote (`PriceFetcher.get_current`) | `history(period="5d")` | `close` = latest bar (live price during the session). `auto_adjust=True` (default). `currency` from `history_metadata`. P/E and market cap from `info` (optional; failure doesn't drop the price). |
| Outcome history (`get_daily_history`) | `history(start, end, auto_adjust=False, actions=True)` | Closes are **split-adjusted, not dividend-adjusted** → price returns. Splits come from the `Stock Splits` column. |
| FX (`usd_rate`) | quote of `{CUR}USD=X` | Minor units (`GBp`, `ZAc`, `ILA`) are divided by 100. |

## 2. Day-over-day change (price confirmation factor)

```
change_pct = (close_latest - close_previous_session) / close_previous_session * 100
```
0.0 if there's no previous session. Feeds `compute_price_confirmation`
(`analysis/signals.py`): `normalized = clamp(change_pct / scale_pct, -1, 1)`.

**The quote must belong to `deepseek_signal.primary_ticker`** (the ticker the
signal is stored under), fetched on demand if outside the universe — not to the
first keyword-matched ticker.

## 3. Per-period signal returns (`portfolio/backtest.py: apply_outcome`)

Horizons: `OUTCOME_HORIZONS` in `constants.py` = 1, 7, 30, 90 **calendar** days.

```
target_date_N  = (signal.created_at as UTC).date() + N days
price_N        = close of the first COMPLETED session with date >= target_date_N
                 (sessions dated today are still trading → wait)
                 × split_factor_after(signal_date)
return_N_pct   = (price_N - price_at_signal) / price_at_signal * 100
```

- `split_factor_after(d)` = product of split ratios with split date > d. Yahoo
  closes are in today's share basis; this converts them back to the share basis
  of the signal so a 10:1 split doesn't read as −90%.
- Grading uses the horizon's close, **never** the price on the day the job
  happens to run.
- `created_at` comes back from SQLite naive — always wrap with `as_utc()`.
- Base validation: `reference = last close on/before signal date` (split
  basis); grade only if `|price_at_signal / reference - 1| <= MAX_BASE_PRICE_DEVIATION`
  (0.25). No reference → not graded. This filters signals stored with another
  company's quote.

Direction (`GRADING_HORIZON_DAYS` = 7):
```
buy  → was_correct = return_7d_pct > 0
sell → was_correct = return_7d_pct < 0
hold → was_correct = NULL
```
Calibration (`analysis/calibration.py`) labels a signal correct when
`score * return_7d_pct > 0`; alpha decay = `|return_1d| / |return_7d|`.

## 4. Paper portfolio (`portfolio/tracker.py`, all amounts in USD)

Every trade price and mark-to-market quote is converted with
`PriceFetcher.to_usd` (`close_local * usd_rate(currency)`) before it touches
the book. `PaperPortfolio.live_price_map(fetcher)` gives USD marks for open
positions; without a price map positions sit at cost (unrealized = 0).

```
cash                 = initial_capital - Σ buy_price·qty + Σ sell_price·qty
avg_price (per tkr)  = Σ open_lot_price·qty / Σ open_qty
unrealized_pnl       = (current_price - avg_price) · qty
unrealized_pct       = (current_price - avg_price) / avg_price * 100
realized_pnl (lot)   = (close_price - buy_price) · qty        # FIFO lot matching
total_value          = cash + Σ current_price·qty
pnl_total            = Σ realized_pnl + Σ unrealized_pnl      # == total_value - initial_capital
pnl_pct              = pnl_total / initial_capital * 100      # portfolio return
```

### Profit margins
```
realized_margin_pct   = Σ realized_pnl / Σ cost of closed lots * 100
unrealized_margin_pct = Σ unrealized_pnl / Σ cost of open lots * 100
win_rate_pct          = closed lots with realized_pnl > 0 / closed lots * 100
```
Each is `None` when its denominator is 0. Note `pnl_pct` (return on starting
capital) and the margins (return on capital deployed) answer different
questions — say which one you mean.

## Worked example
Buy 10 AMD @ 100, sell @ 120; buy 10 NVDA @ 50, now 45:
realized 200 on 1,000 → realized margin +20%; unrealized −50 on 500 → −10%;
pnl_total 150 → pnl_pct +0.15% of $100k; win rate 100%.

## Not covered
Company fundamentals (net/operating profit margin of the business) are not
fetched; DeepSeek only reads them from article text.
