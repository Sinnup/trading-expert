# Trading Expert AI — Implementation Plan

> **Status**: Approved | **Date**: 2026-06-23 | **Version**: 1.0

## Context

Build an AI-powered trading advisor that monitors news about tech/semiconductor companies (25+ tickers globally), analyzes sentiment using DeepSeek's API, and pushes actionable buy/sell/hold alerts to the user's Android phone via Telegram. The system runs locally in Docker but can replicate to any cloud host. Primary signal is news sentiment with cascade analysis across the semiconductor supply chain.

**User**: Retail investor trading via GBM app on Android
**Goal**: Get phone notifications like: *"Invest in NVIDIA — they signed a deal with Tesla, which means more chip orders from TSMC. Also Intel may lose socket share, consider selling."*

---

## Tech Stack

| Layer | Technology | Rationale |
|---|---|---|
| Project manager | **uv** | Fast, lockfile, Python version pinning |
| Language | **Python 3.12** | Best financial + ML ecosystem |
| AI reasoning | **DeepSeek API** (`deepseek-chat` daily, `deepseek-reasoner` for high-impact) via `openai` SDK pointed at `api.deepseek.com` | OpenAI-compatible, cheap, function calling for structured output |
| Fast sentiment | **VADER** (`vaderSentiment`) + **FinBERT** (`ProsusAI/finbert` via HuggingFace `transformers`) | Pre-filter noise before hitting API |
| Stock data | **yfinance** | Free, covers all global tickers |
| News | **NewsAPI** (free tier) + **GNews** (free tier) + **RSS feeds** (unlimited) | Multi-source with free tiers |
| Task queue | **Celery + Redis** | Scheduling, retries, rate limiting |
| Database | **SQLite → Postgres** via **SQLAlchemy** ORM | Swap one connection string, zero code changes |
| Containerization | **Docker + Docker Compose** | Runs anywhere |
| CLI | **Click** | Terminal interface for queries + manual triggers |
| Phone notifications | **Telegram Bot API** | Free, Android-native, rich formatting, no app to build |
| Config | **.env** (secrets) + **YAML** (settings) | Cloud-friendly |

### Dependencies omitted
- **No LangChain** — DeepSeek via `openai` SDK + Pydantic structured output is simpler, lighter, and fully debuggable
- **No web framework initially** — CLI only. Flask added only if/when a dashboard is needed.

---

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                     Docker Container                          │
│                                                              │
│  ┌──────────┐   ┌───────────┐   ┌──────────┐               │
│  │ Celery   │   │  Celery   │   │  Redis   │               │
│  │ Beat     │──▶│  Workers  │   │  (cache) │               │
│  │(scheduler)│   │           │   │          │               │
│  └──────────┘   └─────┬─────┘   └──────────┘               │
│                       │                                      │
│          ┌────────────┼────────────┐                         │
│          ▼            ▼            ▼                         │
│  ┌──────────┐ ┌────────────┐ ┌──────────┐                  │
│  │  News    │ │  Sentiment │ │  Signal  │                  │
│  │Fetchers  │ │  Engines   │ │  Scorer  │                  │
│  └────┬─────┘ └─────┬──────┘ └────┬─────┘                  │
│       │              │              │                        │
│       └──────────────┼──────────────┘                        │
│                      ▼                                       │
│              ┌──────────────┐                               │
│              │   SQLite     │                               │
│              │  (signals,   │                               │
│              │  news, P&L)  │                               │
│              └──────┬───────┘                               │
└─────────────────────┼───────────────────────────────────────┘
                      │
                      ▼
              ┌──────────────┐
              │  Telegram    │
              │  Bot → Your  │
              │  Android     │
              │  Phone       │
              └──────────────┘
```

---

## Data Flow

```
Every 15-30 min (market hours):
RSS feeds → NewsAPI → GNews
              ↓
    Deduplication + Company matching
              ↓
    VADER + FinBERT + keyword triggers
              │
    ┌─────────┼─────────┐
    ▼         ▼         ▼
  Neutral   Extreme    Tier-1
  (skip)    sentiment  source
              │         │
              └────┬────┘
                   ▼
         DeepSeek API (deepseek-chat)
         Function calling → JSON
                   ▼
         Structured Signal
         {ticker, sentiment, confidence, action, cascade[], reasoning}
                   ▼
         Signal Scorer
         DeepSeek(50%) + volume(20%) + credibility(15%) + price(15%)
         → Score -1.0 to +1.0
                   ▼
         Threshold check
         |score| > 0.6 → ALERT
         |score| > 0.4 → WATCH
                   ▼
         Telegram Bot → Android Phone
```

---

## Database Schema

5 tables: `articles`, `signals`, `signal_outcomes`, `paper_trades`, `portfolio_snapshots`

---

## Project Structure

```
trading-expert/
├── pyproject.toml
├── uv.lock
├── .python-version
├── Dockerfile
├── docker-compose.yml
├── .env.example
├── .gitignore
├── README.md
├── FEATURE_MANAGER.md
├── IP plans/
│   └── implementation-plan.md
├── .claude/
│   └── agents/
│       ├── architect.md
│       ├── developer.md
│       ├── qa.md
│       ├── versioning-expert.md
│       ├── devops.md
│       └── security-reviewer.md
├── config/
│   ├── settings.yaml
│   ├── companies.yaml
│   └── thresholds.yaml
├── src/
│   ├── __init__.py
│   ├── cli.py
│   ├── fetchers/
│   │   ├── __init__.py
│   │   ├── base.py
│   │   ├── news.py
│   │   └── prices.py
│   ├── analysis/
│   │   ├── __init__.py
│   │   ├── prefilter.py
│   │   ├── deepseek.py
│   │   ├── signals.py
│   │   └── cascade.py
│   ├── models/
│   │   ├── __init__.py
│   │   ├── database.py
│   │   ├── article.py
│   │   ├── signal.py
│   │   └── portfolio.py
│   ├── notifications/
│   │   ├── __init__.py
│   │   ├── telegram.py
│   │   └── formatter.py
│   ├── portfolio/
│   │   ├── __init__.py
│   │   ├── tracker.py
│   │   └── backtest.py
│   └── tasks/
│       ├── __init__.py
│       ├── celery_app.py
│       ├── daily.py
│       └── intraday.py
├── tests/
└── data/
```

---

## Implementation Phases

1. **Foundation** — Docker, DB, CLI skeleton, config
2. **News Pipeline** — Fetchers (NewsAPI, GNews, RSS) + yfinance
3. **AI Analysis** — VADER/FinBERT prefilter, DeepSeek client, signal scorer, cascade engine
4. **Notifications** — Telegram bot with commands and rich formatting
5. **Scheduling + Portfolio** — Celery tasks, paper trading, backtesting
6. **Learning Loop + Polish** — Outcome tracking, weight tuning, documentation

---

## Verification Plan

1. `docker compose up` → all services healthy
2. `uv run trading-agent fetch` → real articles in SQLite
3. `uv run trading-agent analyze --ticker NVDA` → DeepSeek signal generated
4. Real Telegram alert on Android phone
5. Daily summary auto-sent at 16:30 ET
6. `uv run trading-agent backtest` shows accuracy after 7+ days
7. Clone on second machine → works identically with `.env` config
