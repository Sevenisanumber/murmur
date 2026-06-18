# Murmur

A Reddit sentiment-based trading signal system that detects community momentum in stocks before price moves.

*Named after a murmuration of starlings — thousands of voices forming a single signal.*

---

## What it does

- Monitors WallStreetBets and 4 related subreddits via Arctic-Shift API, every 30 minutes during market hours
- Classifies posts using a local Mistral 7B LLM (Ollama) — hype, thesis, options-yolo, news-reaction, meme, loss-porn
- Scores each ticker-day pair using a 5-factor signal algorithm (v7) backtested on 2M+ posts from 2012–2021
- Executes automated paper trades via Alpaca Markets based on signal type and score thresholds
- Sends Pushover notifications for trade entries, exits, morning briefings, and a weekly Claude AI digest
- Runs 24/7 on a Raspberry Pi 5 with no cloud dependencies

---

## Signal Scoring (v7)

Each ticker-day pair receives a composite score from 0–100:

| Factor | Weight | Notes |
|---|---|---|
| Mention velocity | 35% | Ratio vs. 30-day baseline; 3–5x is the sweet spot |
| Classification quality | 30% | Hype-heavy days outperform thesis-heavy in short windows |
| Subreddit diversity | 15% | Mentions across multiple communities signal broader consensus |
| Mention count | 12% | Percentile ranked |
| Avg post score | 8% | Upvotes as a proxy for community conviction |

**Backtested performance (47,411 ticker-day rows, 2012–2021):**
- High signal (score >70): +4.49% avg 7d return, +11.21% avg 30d return, 54.5% win rate
- Baseline: +1.14% avg 7d, +2.89% avg 30d
- Velocity sweet spot (3–5x): +2.19% avg 7d; above 5x shows reversal risk (-3.41%)
- Cross-community consensus (4+ subreddits): strongest single predictor — +3.76% avg 7d, +11.46% avg 30d

---

## Signal Flags

| Flag | Meaning |
|---|---|
| `HOT` | Velocity 3–5x normal; primary entry signal |
| `SLOW_BURN` | Velocity <0.5x; low-noise accumulation phase, edge at 30 days |
| `EXTREME` | Velocity >5x; never traded — historical reversal risk |
| `RISING` | Velocity 1.5–3x; building momentum, watch only |
| `SQUEEZE_WATCH` | Days-to-cover >5 AND active WSB mentions; +10 score bonus |
| `OPTIONS_ACTIVE` | Ticker appeared in `options_yolo`-classified posts |
| `EARNINGS_NEAR` | Earnings within 5 days; position size halved to $50 |

---

## Paper Trading Rules

**Entry:**
- `HOT_SCORE`: signal score >70 AND velocity 3–5x; suppressed in BEARISH market regime (SPY below 50-day SMA)
- `SLOW_BURN`: signal score ≥30 AND velocity <0.5x; unaffected by market regime
- `SQUEEZE_WATCH`: HOT entry with +10 score bonus for high short interest

**Exit:**
- Take profit: +15% (both signal types)
- Stop loss: −8% for HOT_SCORE / SQUEEZE_WATCH; −15% for SLOW_BURN (wider to let the 30-day edge play out)
- Time exit: 7 days for HOT/SQUEEZE_WATCH; 25 days for SLOW_BURN

**Sizing:**
- $100 per trade ($50 if `EARNINGS_NEAR`), max 15 open positions
- Penny stock filter: never trade below $3

---

## Hardware

- **Raspberry Pi 5** (16GB RAM) — primary server; runs scrapers, cron jobs, dashboard, and Ollama 24/7
- **External SSD** (931GB) — all data storage; `/data` symlinked to SSD
- **USB flash drive** (32GB) — Ollama models + Python venv
- **M1 Max Mac** — heavy batch classification jobs only; Pi handles all daily operations

---

## Data Sources

| Source | Use | Notes |
|---|---|---|
| [Arctic-Shift](https://arctic-shift.photon-reddit.com) | Live Reddit posts | Free, no API key; ~1hr lag; 100 posts/subreddit per pull |
| [YoloStocks](https://yolostocks.live) | Daily mention counts | Free CSV feed; pulled at 6am daily |
| [Alpaca Markets](https://alpaca.markets) | Paper trading + price data | Free tier; paper account |
| yfinance | Historical prices pre-2020 | Pinned at 0.2.58 |
| Reddit API | Live posts | Pending approval (submitted May 31, 2026) |

Subreddits monitored: r/wallstreetbets, r/stocks, r/investing, r/pennystocks, r/options

---

## Stack

Python · SQLite (WAL mode) · Flask · Ollama / Mistral 7B · Alpaca API · Claude API (weekly digest) · Pushover · Tailscale · Raspberry Pi OS

---

## Project Structure

```
scrapers/      Data collection, classification, signal scoring, paper trading
scripts/       Daily pipeline orchestrator, weekly digest, backup utilities
dashboard/     Flask dashboard (local network + Tailscale)
data/          SQLite database — wsb.db (~1.5GB, symlinked to SSD)
logs/          Per-run logs, daily reports, weekly digests
```

---

## Status

**Phase 5: Live paper trading validation** — started June 2, 2026

The signal model is backtested; the current phase measures whether historical alpha holds on live data. Paper trading runs automatically each market day.

---

## Roadmap

- **Layer 2** — technical indicator overlay (RSI, volume, short interest) as signal inputs
- **Layer 3** — LLM synthesis agent that combines signal score, technicals, and post classification into a natural-language trade thesis
- **Layer 4** — outcome feedback loop; weekly Claude digest proposes parameter adjustments based on live trade results; human approves before deployment
- **Phase 6** — real money pilot after 30+ closed trades with consistent positive alpha

---

## Disclaimer

Personal research project. Nothing here is financial advice. All trading uses Alpaca's paper environment — no real money involved.
