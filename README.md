# WSB Signal Lab

Collects WallStreetBets Reddit sentiment alongside stock price data.
Stores everything locally on a Raspberry Pi 5. No trades. No LLM. Just data.

## Phase 1 Goal
Reliable daily collection of WSB posts and stock prices, with a basic dashboard.

## Setup

1. Copy `.env.example` to `.env` and fill in your credentials
2. Activate the virtual environment: `source venv/bin/activate`
3. Run tests: `python scripts/test_connections.py`

## Structure

- `data/`        SQLite database
- `scrapers/`    Reddit and market data scripts
- `dashboard/`   Flask dashboard
- `logs/`        Run logs
- `scripts/`     Scheduler and utilities
- `venv/`        Python virtual environment (not committed)
