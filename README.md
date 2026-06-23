# Trading Expert

AI-powered trading advisor for semiconductor and tech stocks. Uses DeepSeek API to analyze news sentiment and supply chain cascades, sending buy/sell/hold alerts to your Android phone via Telegram.

## Quick Start

```bash
cp .env.example .env
# Fill in your API keys in .env
uv sync
docker compose up -d
uv run trading-agent --help
```

## Documentation

See `IP plans/` for architecture and implementation details.

## Requirements

- Python 3.12+
- Docker & Docker Compose
- DeepSeek API key
- Telegram Bot Token
- NewsAPI key (free tier)
