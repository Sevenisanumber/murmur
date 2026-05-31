# Murmur

A personal research project that listens for signal in the noise of retail sentiment.

Murmur collects WallStreetBets posts, classifies them with a local LLM, scores each ticker-day pair against a composite signal model, and tracks simulated trades against real forward returns. Everything runs on a Raspberry Pi 5 with no cloud dependencies.

## What it does

- Ingests WSB post data (Kaggle archive + daily live scrape via YoloStocks)
- Classifies posts by type — hype, thesis, options-yolo, news-reaction, meme, loss-porn — using Mistral running locally via Ollama
- Scores each (ticker, date) pair on a 0–100 composite signal: mention velocity, classification mix, subreddit diversity, mention count, and avg post score
- Fetches historical prices from Alpaca (post-2020) and Yahoo Finance (pre-2020) and computes 1d / 7d / 30d forward returns
- Runs a paper trading simulation via Alpaca: enters on HOT or SLOW\_BURN signals above score thresholds, exits after 7 days or at profit/loss limits
- Serves a live dashboard over the local network showing the signal pulse, history, paper trades, and data health

## Tech stack

| Layer | Tool |
|---|---|
| Hardware | Raspberry Pi 5 |
| Database | SQLite (WAL mode) |
| LLM classification | Mistral via Ollama (local, no API cost) |
| Price data | Alpaca Markets API (post-2020), Yahoo Finance via yfinance (pre-2020) |
| Live mentions | YoloStocks daily CSV feed |
| Dashboard | Flask + plain HTML/CSS, auto-refresh every 5 min |
| Notifications | ntfy (self-hosted) |

## Current phase status

| Phase | Description | Status |
|---|---|---|
| 1 | Data collection — posts, prices, ticker extraction | Complete |
| 2 | LLM classification of post intent | Complete |
| 3 | Signal scoring v7 — composite model with subreddit diversity | Active |
| 4 | Paper trading simulation via Alpaca | Active |

The signal scorer is on v7. Each version is benchmarked against 7d and 30d forward returns; the version history and alpha figures are written to `logs/signal_analysis.txt` on each run.

## Structure

```
data/          SQLite database (wsb.db)
scrapers/      Data collection, classification, scoring, and paper trading
dashboard/     Flask dashboard (app.py + templates/)
scripts/       Daily scheduler (run_daily.py) and utilities
logs/          Per-run logs and signal analysis report
archive/       Raw source data files
```

## Setup

1. Copy `.env.example` to `.env` and fill in your credentials
2. Create and activate a virtual environment: `python3 -m venv venv && source venv/bin/activate`
3. Install dependencies: `pip install -r requirements.txt`
4. Run connection tests: `python scripts/test_connections.py`
5. Initialise the database: `python scripts/init_db.py`

## Disclaimer

This is a personal research project. Nothing here constitutes financial advice. The paper trading simulation uses Alpaca's paper environment and involves no real money.
