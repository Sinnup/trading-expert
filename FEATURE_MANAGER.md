# Feature Manager — Trading Expert

> Backlog and changelog for the Trading Expert AI project.

---

## Backlog

### Phase 1: Foundation
- [x] GitHub repository created
- [x] uv project initialized with pyproject.toml
- [x] .env.example with all required keys
- [x] Dockerfile (multi-stage with uv)
- [x] docker-compose.yml (redis, worker, beat, app)
- [x] config/settings.yaml
- [x] config/companies.yaml (25+ tickers with supply chain graph)
- [x] config/thresholds.yaml
- [x] Project directory structure created
- [x] IP plans folder with implementation plan
- [x] Agent role files (.claude/agents/)
- [ ] SQLAlchemy database models (all 5 tables)
- [ ] CLI skeleton with Click commands
- [ ] uv sync success + docker compose up verified

### Phase 2: News Pipeline
- [ ] Abstract base fetcher class
- [ ] NewsAPI fetcher implementation
- [ ] GNews fetcher implementation
- [ ] RSS fetcher implementation
- [ ] Article deduplication (SHA256 of title+source)
- [ ] Ticker matching from article text
- [ ] yfinance price fetcher
- [ ] Unit tests for fetchers
- [ ] Verified: real articles land in SQLite

### Phase 3: AI Analysis
- [ ] VADER sentiment prefilter
- [ ] FinBERT model integration
- [ ] Keyword trigger extraction
- [ ] Prefilter escalation logic
- [ ] DeepSeek API client (OpenAI SDK)
- [ ] Structured output via function calling
- [ ] DeepSeek batch analysis
- [ ] Supply chain cascade engine
- [ ] Signal scorer (weighted combination)
- [ ] Unit tests with mocked DeepSeek
- [ ] Verified: end-to-end signal generation

### Phase 4: Notifications
- [ ] Telegram bot setup
- [ ] Alert message formatter
- [ ] Bot command handlers (/status, /signals, /portfolio, /mute, /unmute, /threshold)
- [ ] Alert cooldown logic
- [ ] Verified: real alert on Android phone

### Phase 5: Scheduling + Portfolio
- [ ] Celery app configuration
- [ ] Intraday monitoring task (every 15 min)
- [ ] Daily summary task (market close)
- [ ] Paper trading tracker (virtual buy/sell)
- [ ] P&L calculation
- [ ] Portfolio snapshot daily
- [ ] Verified: autonomous operation for 1 day

### Phase 6: Learning Loop + Polish
- [ ] Signal outcome checker (7/30/90 days)
- [ ] Accuracy statistics
- [ ] Weight auto-tuning
- [ ] CLI polish (rich tables, colors)
- [ ] README with setup instructions
- [ ] Batch processes documented

### Future
- [ ] PostgreSQL migration for cloud deployment
- [ ] Web dashboard (Flask)
- [ ] Real brokerage API integration
- [ ] Mobile app (React Native)

---

## Changelog

### 0.1.0 — 2026-06-23
- **feat(config)**: Initial project setup with uv, Docker, config files
- **feat(docs)**: Agent role files (architect, developer, qa, versioning-expert, devops, security-reviewer)
- **feat(docs)**: Feature manager with backlog
- **feat(docs)**: Implementation plan in IP plans folder
- **feat(config)**: Company universe with 27 tickers and supply chain graph
- **feat(config)**: Alert thresholds and urgency keyword triggers
- **feat(docker)**: Multi-stage Dockerfile with uv
- **feat(docker)**: Docker Compose with redis, worker, beat, app services
