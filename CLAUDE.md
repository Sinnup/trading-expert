# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Key Collaboration Rule

**Answer first, code later.** When the user asks a question, answer it directly — do NOT start implementing. Wait for explicit confirmation before making any code changes.

## Commands

```bash
# Install dependencies
uv sync

# Run all tests
uv run pytest

# Run a single test file
uv run pytest tests/test_analysis.py -v

# Run a single test by name (tests are class-based)
uv run pytest tests/test_scoring_factors.py::TestSignalLabel::test_labels -v

# Run the CLI (every command accepts --universe {semiconductor,bmv,auto};
# `auto` infers the universe from the ticker's .MX suffix)
uv run trading-agent --help
uv run trading-agent analyze --ticker NVDA
uv run trading-agent analyze --ticker BIMBOA.MX   # auto-routes to BMV universe
uv run trading-agent watch                        # one intraday fetch/analyze cycle
uv run trading-agent daily                        # generate the daily summary
uv run trading-agent signals --ticker NVDA --days 7
uv run trading-agent portfolio
uv run trading-agent backtest --days 30
uv run trading-agent bot                           # start the polling Telegram chat bot

# Docker (production deployment)
docker compose up -d           # start redis + worker + beat + bot + app
docker compose logs -f worker  # tail worker logs
docker compose down
```

There is no linter configured; use standard Python conventions. No type-checker command is wired up either, but all functions must have type hints.

## Architecture

### Pipeline Overview

The system runs two parallel pipelines (semiconductor/tech and BMV/Mexican stocks) that share the same infrastructure but operate independently:

```
News Sources (RSS, NewsAPI, GNews)
    ↓
VADER Prefilter  →  escalated articles only
    ↓
DeepSeek Analysis (deepseek-chat or deepseek-reasoner)
    ↓
Signal Scorer (weighted: AI 50%, volume 20%, credibility 15%, price 15%)
    ↓
Cascade Engine  →  secondary signals for supply-chain-impacted tickers
    ↓
SQLite DB  +  Telegram Alerts
```

The intraday Celery task (`trading_expert/tasks/intraday.py`) wires all these steps together. The BMV variant (`tasks/intraday_bmv.py`) follows the same flow but skips cascade reasoning (BMV stocks span unrelated sectors).

### Two DeepSeek Models

- **`deepseek-chat`** — used by `DeepSeekAnalyzer` for all article analysis. Returns structured JSON via function calling (`emit_trading_signal`). Low temperature (0.1) for consistency.
- **`deepseek-reasoner`** — used exclusively by `TradingChatAdvisor` for the Telegram chat interface. Does chain-of-thought reasoning; does NOT support function calling, so it returns free-form text.

The `settings.yaml` key `deepseek.reasoning_threshold: 0.8` documents the intent to upgrade individual articles to the reasoner when prefilter score > 0.8, but this escalation is not yet implemented in the task.

### Signal Scoring

`SignalScorer` (`analysis/signals.py`) combines four factors into a score in `[-1.0, 1.0]`:

| Factor | Default weight | Source |
|---|---|---|
| DeepSeek sentiment | 50% | R1/chat model output |
| News volume z-score | 20% | Article count vs 30-day baseline |
| Source credibility | 15% | Tier 1=1.0, Tier 2=0.5, Tier 3=0.2 |
| Price confirmation | 15% | yfinance daily % move |

Alert fires when `|score| >= 0.5` AND confidence >= "medium". Thresholds live in `config/settings.yaml` under `signals.thresholds` and are overridable per universe.

All magic numbers (clamps, scale factors, label bands) live in `trading_expert/constants.py`.

### Supply Chain Cascade

`SupplyChainGraph` (`analysis/cascade.py`) is a directed graph loaded from `config/companies.yaml` under the `supply_chain` key. Edges encode `depends_on`, `supplies_to`, and `competes_with` relationships. When a primary signal fires, the cascade engine generates secondary `FinalSignal` objects for impacted companies with `cascade_source=True` and `cascade_parent` set to the triggering ticker.

### Two Universes

| | Semiconductor | BMV |
|---|---|---|
| Config | `config/companies.yaml` + `config/settings.yaml` | `config/companies_bmv.yaml` + `config/settings_bmv.yaml` |
| Tasks | `tasks/intraday.py`, `tasks/daily.py` | `tasks/intraday_bmv.py`, `tasks/daily_bmv.py` |
| Signal type tag | `"intraday"` / `"cascade"` | `"intraday_bmv"` |
| News language | English | Spanish |
| Cascade reasoning | Yes | No |
| Market hours | NYSE (ET) | BMV (CT) |

Tickers ending in `.MX` are auto-routed to the BMV universe throughout the CLI and Telegram bot.

### Telegram Bot

`TelegramNotifier` (push-only, uses raw httpx) is separate from `start_command_bot` (polling, uses python-telegram-bot). Both live in `notifications/telegram.py`. The polling bot is launched via `trading-agent bot` (CLI) or the `bot` service in `docker-compose.yml`; `start_command_bot` is a blocking call that runs `app.run_polling()` with its own event loop, so it must not be called from inside `asyncio.run()`.

The polling bot handles:
- Slash commands: `/status`, `/signals`, `/portfolio`, `/mute`, `/unmute`, `/threshold`, `/help`, `/ask`, `/reset`
- **Free-text messages**: any non-command text is routed to `TradingChatAdvisor` (DeepSeek-R1). The bot keeps a typing indicator looping every 4 s while R1 reasons, then splits responses > 4000 chars across multiple messages.
- `TradingChatAdvisor` maintains per-`chat_id` conversation history (capped at 10 messages) and refuses non-trading topics via system prompt enforcement.

### Celery Schedule

Beat schedules (defined in `tasks/celery_app.py`):
- `intraday-fetch` — every 15 min, 24/7 (semiconductor)
- `intraday-fetch-bmv` — every 30 min, 24/7 (BMV)
- `daily-summary` — 4:30 PM ET weekdays
- `daily-summary-bmv` — 3:15 PM CT weekdays
- `check-signal-outcomes` — daily (7/30/90-day accuracy check)

### Database

SQLAlchemy with SQLite locally (`data/trading.db`), designed for Postgres in cloud. Five tables: `articles`, `signals`, `signal_outcomes`, `paper_trades`, `portfolio_snapshots`. `models/__init__.py` exposes `init_db()` and `get_session()`.

## Configuration Files

- `config/settings.yaml` — market hours, fetch intervals, DeepSeek model selection, signal weights/thresholds, RSS feeds, Telegram limits
- `config/companies.yaml` — 29 semiconductor/tech tickers with sector, role, region, weight, and the full `supply_chain` edge graph
- `config/thresholds.yaml` — urgency keyword triggers (critical/high/medium priority) used by the VADER prefilter
- `config/companies_bmv.yaml` + `config/settings_bmv.yaml` — BMV equivalents

## Environment Variables

Required in `.env`:
```
DEEPSEEK_API_KEY=
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
NEWSAPI_KEY=           # optional but recommended
GNEWS_API_KEY=         # optional but recommended
```

## Agent Roles

See `.claude/agents/` for role-specific instructions:
- `architect.md` — architecture decisions and tech choices
- `developer.md` — coding standards and patterns
- `qa.md` — testing strategy and verification
- `versioning-expert.md` — git workflow and versioning (conventional commits)
- `devops.md` — Docker, deployment, environment
- `security-reviewer.md` — secrets, dependencies, code security

## Feature Tracking

See `FEATURE_MANAGER.md` for full backlog and changelog. All 6 phases (Foundation → Notifications → Scheduling → Docs) are complete. Current version: 0.2.0.
