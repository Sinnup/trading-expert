# Feature Manager — Trading Expert

> Backlog and changelog for the Trading Expert AI project.

---

## Backlog

### Phase 1: Foundation ✅
- [x] GitHub repository created
- [x] uv project initialized with pyproject.toml
- [x] .env.example with all required keys
- [x] Dockerfile (multi-stage with uv)
- [x] docker-compose.yml (redis, worker, beat, app)
- [x] config/settings.yaml
- [x] config/companies.yaml (27 tickers with supply chain graph)
- [x] config/thresholds.yaml
- [x] Project directory structure created
- [x] IP plans folder with implementation plan
- [x] Agent role files (.claude/agents/)
- [x] SQLAlchemy database models (all 5 tables)
- [x] CLI skeleton with Click commands
- [x] uv sync success verified

### Phase 2: News Pipeline ✅
- [x] Abstract base fetcher class (RawArticle, BaseFetcher)
- [x] NewsAPI fetcher implementation
- [x] GNews fetcher implementation
- [x] RSS fetcher implementation
- [x] Article deduplication (SHA256 of title+source)
- [x] Ticker matching from article text
- [x] yfinance price fetcher
- [x] Unit tests for fetchers (12 tests)
- [x] Verified: CLI works, DB tables created

### Phase 3: AI Analysis ✅
- [x] VADER sentiment prefilter
- [x] Keyword trigger extraction (critical + high urgency)
- [x] Prefilter escalation logic (sentiment + keywords + tier-1 source)
- [x] DeepSeek API client (OpenAI SDK at api.deepseek.com)
- [x] Structured output via function calling (SignalSchema)
- [x] Supply chain cascade engine (directed graph, 27 nodes)
- [x] Signal scorer (weighted: AI 50%, volume 20%, credibility 15%, price 15%)
- [x] Unit tests (21 tests covering prefilter, cascade, signals)
- [x] All 33 tests passing

> **Note**: FinBERT skipped — PyTorch has no wheels for macOS x86_64. VADER alone is sufficient for pre-filtering since DeepSeek does the deep analysis.

### Phase 4: Notifications ✅
- [x] Telegram bot push alerts (httpx-based)
- [x] Rich alert message formatter (emoji, sections, cascade effects)
- [x] Alert cooldown logic (30 min per ticker)
- [x] Daily rate limiting (max 20 alerts/day)
- [x] Daily summary formatter (ranked signals, counts)
- [x] Portfolio summary formatter
- [x] Optional polling bot with 7 commands
- [x] Unit tests (10 tests for formatters)
- [x] All 43 tests passing

### Phase 5: Scheduling + Portfolio ✅
- [x] Celery app configuration (Redis broker)
- [x] Beat schedule: intraday (15 min), daily summary (market close), outcome check (daily)
- [x] Intraday monitoring task (full pipeline)
- [x] Daily summary task (aggregate + Telegram)
- [x] Signal outcome checker (7/30/90 day)
- [x] Paper trading tracker (virtual buy/sell with FIFO)
- [x] P&L calculation (realized + unrealized)
- [x] Portfolio snapshots
- [x] All 43 tests still passing

### Phase 6: Documentation ✅
- [x] Comprehensive README with setup, architecture, CLI, considerations
- [x] Batch processes documented
- [x] API costs estimated ($2-5/month)
- [x] Limitations documented
- [x] Deployment guide (local + cloud)
- [x] Feature manager updated with all phases

### Future
- [ ] PostgreSQL migration for cloud deployment
- [ ] Web dashboard (Flask)
- [ ] Real brokerage API integration (GBM, Interactive Brokers)
- [ ] Signal weight auto-tuning based on accuracy data
- [ ] Mobile push notifications via Firebase
- [ ] More news sources (Benzinga, Polygon.io — paid)
- [ ] Portfolio performance charts
- [ ] Options flow analysis

---

## Changelog

### 0.1.1 — 2026-06-24

**fix(fetchers)**: NewsAPIFetcher now batches tickers into OR queries (1-2 req/interval instead of 27), staying within 100/day free tier limit.
**fix(config)**: Quoted `"ON"` ticker in companies.yaml — YAML was parsing it as boolean `True`.
**fix(tasks)**: Added type safety in `build_tickers_map` to filter non-string variants.
**fix(imports)**: Corrected `models.database` → `models` import path across 4 files.
**fix(docker)**: Simplified Dockerfile to single stage, added README.md copy, removed `--frozen` flag.
**feat(tasks)**: Switched from market-hours-only to 24/7 monitoring (every 30 min). Covers Asian, European, and US time zones.
**fix(analysis)**: Prefilter now requires at least one tracked ticker match before escalating to DeepSeek. Cuts irrelevant API calls by 90%.
**docs(claude)**: Added rule — answer questions before implementing code.
**test**: 43 tests passing. System verified end-to-end with live Telegram alert.

### 0.1.0 — 2026-06-23

**Initial Release** — Complete AI trading advisor pipeline.

**feat(config)**: 
- uv project with Python 3.12, 16 dependencies
- Docker multi-stage build + docker-compose (4 services)
- 27 tickers with full supply chain graph
- Alert thresholds + urgency keyword triggers

**feat(fetchers)**:
- RSS, NewsAPI, GNews fetchers with async httpx
- SHA256 deduplication + ticker name matching
- yfinance price fetcher (current + historical)

**feat(analysis)**:
- VADER sentiment prefilter + keyword trigger detection
- DeepSeek API client via OpenAI SDK with function calling
- Supply chain cascade engine (directed graph)
- Weighted signal scorer (AI + volume + credibility + price)

**feat(notifications)**:
- Telegram push alerts with rich formatting
- Alert cooldown + daily rate limiting
- 7 bot commands (/status, /signals, /portfolio, /mute, /unmute, /threshold, /help)
- Daily summary + portfolio formatters

**feat(tasks)**:
- Celery with Redis + Beat scheduler
- Intraday monitoring (every 15 min during market hours)
- Daily summary (market close)
- Signal outcome checking (7/30/90 day accuracy)

**feat(portfolio)**:
- Paper trading with FIFO order matching
- P&L tracking (realized + unrealized)
- Backtest engine with accuracy stats by ticker/action/confidence

**feat(docs)**:
- Agent role files (architect, developer, qa, versioning-expert, devops, security-reviewer)
- Implementation plan in IP plans/
- Comprehensive README with setup, costs, limitations
- Batch processes documented

**test**: 43 unit tests covering fetchers, analysis, notifications — all passing
