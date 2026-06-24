# Trading Expert — Project Instructions

This project is an AI-powered trading advisor for semiconductor and tech stocks.
It uses DeepSeek API for news sentiment analysis and supply chain cascade reasoning.

## Tracked Sectors
Semiconductors, AI, cloud, and consumer tech: NVIDIA, AMD, Intel, Broadcom, Qualcomm,
Marvell, Micron, Texas Instruments, Analog Devices, ON Semi, Microchip, ASML,
Applied Materials, Lam Research, KLA, TSMC, SMIC, Hua Hong, Samsung, SK Hynix,
Apple, Microsoft, Alphabet, Amazon, Meta, MediaTek, Arm, Infineon, STMicro.

## Architecture
- Python 3.12 with uv
- DeepSeek API via openai SDK (NO LangChain)
- VADER for fast sentiment pre-filter, DeepSeek for deep reasoning
- yfinance for stock data, NewsAPI + GNews + RSS for news
- Celery + Redis for scheduling
- SQLite (local) → PostgreSQL (cloud) via SQLAlchemy
- Docker + Docker Compose
- Telegram Bot for Android phone notifications
- Click CLI for terminal interface

## Key Rules
- **Answer first, code later**: When the user asks a question, answer it — do NOT start implementing. Wait for explicit confirmation before making any code changes.
- ALL secrets in .env, never committed
- Atomic commits with conventional commit format
- Test each feature before advancing to next phase
- Type hints on all functions
- Async/await for I/O operations
- Docker is the deployment unit — must work identically everywhere

## Agent Roles
See .claude/agents/ for role-specific instructions:
- architect.md — architecture decisions and tech choices
- developer.md — coding standards and patterns
- qa.md — testing strategy and verification
- versioning-expert.md — git workflow and versioning
- devops.md — Docker, deployment, environment
- security-reviewer.md — secrets, dependencies, code security

## Feature Tracking
See FEATURE_MANAGER.md for backlog and changelog.

## Planning Documents
See IP plans/ for architecture and implementation plans.
