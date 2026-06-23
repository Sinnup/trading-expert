# Trading Expert

AI-powered trading advisor for semiconductor and tech stocks. Uses DeepSeek API to analyze news sentiment and supply chain cascades, sending buy/sell/hold alerts to your Android phone via Telegram.

## Architecture

```
News → Prefilter (VADER) → DeepSeek API → Signal Scorer → Telegram Alert
  │                              │
  └── Supply Chain Graph ←──────┘
         (cascade effects)
```

- **News ingestion**: RSS feeds (unlimited, free) + NewsAPI + GNews
- **Fast filter**: VADER sentiment + urgency keyword detection
- **Deep reasoning**: DeepSeek API with function calling for structured output
- **Cascade engine**: Directed graph of 27 semiconductor companies — traces how events propagate through the supply chain
- **Signal scoring**: Weighted combination of AI sentiment (50%), news volume (20%), source credibility (15%), price confirmation (15%)
- **Delivery**: Rich Telegram messages with emoji, cascade effects, risk factors, and action recommendations

## Quick Start

### Prerequisites
- Python 3.12+
- Docker & Docker Compose
- [DeepSeek API key](https://platform.deepseek.com/api_keys)
- [Telegram Bot Token](https://t.me/BotFather) + your chat ID (from [@userinfobot](https://t.me/userinfobot))
- [NewsAPI key](https://newsapi.org/register) (free tier: 100 req/day)
- [GNews API key](https://gnews.io/) (free tier: 100 req/day, optional)

### Setup

```bash
# 1. Clone the repo
git clone https://github.com/Sinnup/trading-expert.git
cd trading-expert

# 2. Configure environment
cp .env.example .env
# Edit .env with your API keys:
#   DEEPSEEK_API_KEY=sk-...
#   TELEGRAM_BOT_TOKEN=...
#   TELEGRAM_CHAT_ID=...
#   NEWSAPI_KEY=...
#   GNEWS_API_KEY=...

# 3. Install dependencies
uv sync

# 4. Start the system
docker compose up -d

# 5. Verify
uv run trading-agent --help
```

### CLI Commands

```bash
uv run trading-agent analyze --ticker NVDA  # Analyze one ticker
uv run trading-agent watch                  # Start intraday monitoring
uv run trading-agent daily                  # Run daily summary
uv run trading-agent signals --ticker NVDA  # Recent signals
uv run trading-agent portfolio              # Paper trading P&L
uv run trading-agent backtest               # Signal accuracy stats
```

### Telegram Commands
Once the bot is running, message it on Telegram:
- `/status` — Current signal summary
- `/signals NVDA` — Last 5 NVIDIA signals
- `/portfolio` — Paper trading P&L
- `/mute 4h` — Snooze alerts
- `/unmute` — Resume alerts
- `/threshold 0.8` — Set minimum alert score
- `/help` — Show all commands

## Tracked Companies (27 tickers)

### US Semiconductor Designers
NVDA, AMD, INTC, AVGO, QCOM, MRVL, MU, TXN, ADI, ON, MCHP

### Equipment Makers
ASML, AMAT, LRCX, KLAC

### Foundries
TSM, SMIC, 688981.SS (Hua Hong)

### Memory (Asia)
005930.KS (Samsung), 000660.KS (SK Hynix)

### Hyperscalers / Big Tech
AAPL, MSFT, GOOGL, AMZN, META

### Mobile / IP / Auto
2454.TW (MediaTek), ARM, IFNNY (Infineon), STM

## Project Structure

```
trading-expert/
├── pyproject.toml                 # uv project config
├── Dockerfile                     # Multi-stage Docker build
├── docker-compose.yml             # redis, worker, beat, app
├── .env.example                   # API key template
├── config/
│   ├── settings.yaml              # Global settings
│   ├── companies.yaml             # Ticker universe + supply chain
│   └── thresholds.yaml            # Alert thresholds + keywords
├── trading_expert/
│   ├── cli.py                     # Click CLI
│   ├── fetchers/                  # News + price data ingestion
│   ├── analysis/                  # Prefilter, DeepSeek, signals, cascade
│   ├── models/                    # SQLAlchemy ORM (5 tables)
│   ├── notifications/             # Telegram bot + formatter
│   ├── portfolio/                 # Paper trading + backtesting
│   └── tasks/                     # Celery async tasks
├── tests/                         # Unit tests (43 passing)
├── IP plans/                      # Architecture docs
├── .claude/agents/                # Agent role definitions
└── FEATURE_MANAGER.md             # Backlog + changelog
```

## Database Schema

5 tables in SQLite (auto-migrates to PostgreSQL):

| Table | Purpose |
|---|---|
| `articles` | Every fetched news article with sentiment scores |
| `signals` | Buy/sell/hold signals per ticker |
| `signal_outcomes` | Accuracy tracking at 7/30/90 days |
| `paper_trades` | Virtual buy/sell execution records |
| `portfolio_snapshots` | Daily portfolio value snapshots |

## Considerations

### Data Quality
- **NewsAPI free tier** returns only 100 articles/day and has 15-minute delay. Not suitable for sub-minute trading.
- **RSS feeds** are free and unlimited but limited to sources that provide them (Reuters, CNBC, Seeking Alpha).
- **VADER sentiment** is fast but not perfect on financial text. It serves as a pre-filter; DeepSeek is the real analyzer.
- **yfinance** data may have delays of up to 15 minutes during market hours.

### API Costs
- **DeepSeek**: ~$0.14 per 1M input tokens, ~$0.28 per 1M output. With ~20 escalated articles/day, cost is approximately **$0.05-0.15/day**.
- **Telegram Bot API**: Free, unlimited.
- **NewsAPI**: Free tier (100 req/day). Paid starts at $449/month.
- **GNews**: Free tier (100 req/day). Paid starts at $5/month.
- **Total estimated cost**: **$2-5/month** (free tier APIs + DeepSeek usage).

### Limitations
- **Not financial advice**: This system provides analysis and signals based on news sentiment. It is not a substitute for professional financial advice.
- **No real trading**: The system does not connect to brokerage APIs. Trades must be executed manually via GBM or your broker.
- **Paper trading only**: The portfolio tracker simulates trades. Real execution has slippage, fees, and liquidity constraints.
- **Signal latency**: News may already be priced in by the time the system processes it, especially with RSS delays.
- **Model risk**: DeepSeek and VADER can misinterpret news. Always read the reasoning before acting.
- **Concentration risk**: All 27 tracked companies are in tech/semiconductors. If the sector has a systemic shock, all signals may be correlated.
- **No backtest history yet**: Signal accuracy can only be measured after 7+ days of live operation.

## Batch Processes

| Process | Frequency | What it does |
|---|---|---|
| **Intraday fetch** | Every 15 min (market hours) | Fetch news → filter → analyze → alert |
| **Daily summary** | 4:30 PM ET (market close) | Aggregate day's signals, send Telegram report |
| **Outcome check** | Daily at 6:00 AM | Check past signals for accuracy (7/30/90d) |
| **Portfolio snapshot** | Manual or via daily task | Save portfolio value for performance tracking |

## Deployment

### Local (macOS)
```bash
docker compose up -d
```

### Cloud (any Linux host)
```bash
# 1. Clone repo + copy .env
# 2. Update DATABASE_URL in .env to PostgreSQL if using cloud DB
# 3. docker compose up -d
# Identical operation — no code changes needed.
```

### Without Docker
```bash
# Start Redis manually, then:
celery -A trading_expert.tasks.celery_app worker --loglevel=info &
celery -A trading_expert.tasks.celery_app beat --loglevel=info &
```

## Development

```bash
uv sync                    # Install all deps
uv run pytest              # Run all tests (43 tests)
uv run trading-agent --help  # CLI help
```

### Agent Roles
See `.claude/agents/` for role-specific instructions used during development:
- `architect.md` — System architecture decisions
- `developer.md` — Coding standards and patterns
- `qa.md` — Testing strategy
- `versioning-expert.md` — Git workflow and commit conventions
- `devops.md` — Docker, deployment, environment
- `security-reviewer.md` — Secrets, dependencies, code security

### Versioning
Follows [Conventional Commits](https://www.conventionalcommits.org/):
```
feat(analysis): implement DeepSeek signal analysis
fix(fetchers): handle RSS timeout gracefully
docs(readme): add deployment instructions
```
