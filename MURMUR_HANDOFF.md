# Murmur — Project Handoff Document
**Last updated: May 30, 2026**
*Drop this into a new chat to continue where we left off.*

---

## What is Murmur?

Murmur is a Reddit sentiment trading signal system running on a Raspberry Pi 5. 
It analyzes WallStreetBets and related subreddits to detect momentum signals 
in stock tickers, scores them using a backtested algorithm, and automatically 
places paper trades via Alpaca. Named after a murmuration of starlings — 
thousands of individuals moving together, forming something larger than any one.

**GitHub:** https://github.com/Sevenisanumber/murmur
**Dashboard:** http://192.168.1.45:5001 (local) or http://100.99.175.88:5001 (Tailscale)
**Pi SSH:** plex@192.168.1.45

---

## Hardware

- **Raspberry Pi 5** — 16GB RAM, running 24/7
  - Hosts the dashboard, scrapers, cron jobs, Ollama LLM
  - OS drive: 29GB SD card (nearly full, data moved to SSD)
  - Data folder: symlinked to `/mnt/media/wsb-signal-lab-data` (931GB SSD)
  - USB flash drive at `/mnt/ollama` — stores Ollama models (32GB)
- **Mac (M1 Max, 32GB)** — used for heavy batch jobs (classification)
- **Ollama models on Pi:** Mistral 7B (used for post classification)
- **Ollama models on Mac:** Mistral 7B + Mixtral 8x7B (Mixtral too slow for this task)

---

## Project Structure

```
~/wsb-signal-lab/          (on Pi, data/ symlinked to SSD)
  scrapers/
    kaggle_import.py       — imports Kaggle WSB CSV datasets
    import_leukipp.py      — imports leukipp multi-subreddit dataset
    load_tickers.py        — fetches ticker list from Alpaca
    extract_tickers.py     — regex ticker extraction from posts
    fetch_prices.py        — Alpaca price data fetcher
    fetch_prices_yfinance.py — yfinance historical prices (pre-2020)
    calc_returns.py        — calculates 1d/3d/7d/30d forward returns
    classify_posts.py      — Mistral LLM post classifier (Ollama)
    score_signals.py       — v7 signal scorer
    daily_report.py        — generates daily watchlist report
    paper_trader.py        — Alpaca paper trading automation
    check_positions.py     — intraday position monitor
    fetch_short_interest.py — yfinance short interest data
    fetch_daily_mentions.py — live YoloStocks data fetcher
    import_archive.py      — YoloStocks historical archive importer
    notify.py              — Pushover notifications
  scripts/
    init_db.py             — SQLite schema
    run_daily.py           — daily pipeline orchestrator
    weekly_stats.py        — Sunday stats summary
    weekly_digest.py       — Friday Claude API feedback digest
  dashboard/
    app.py                 — Flask dashboard
    templates/index.html   — dashboard UI
    static/images/         — Murmur branding assets
  data/                    — symlink to /mnt/media/wsb-signal-lab-data
    wsb.db                 — main SQLite database (1.4GB)
  logs/                    — all run logs
```

---

## Database State (as of May 30, 2026)

- **Total posts:** 1,982,153
- **Date range:** April 2012 to December 2021
- **Subreddits:** wallstreetbets, gamestop, stocks, pennystocks, investing, options
- **Sources:** kaggle (1,151,459) + leukipp (830,694)
- **Unique authors:** 599,156+
- **Post classifications:** ~115,000 posts classified by Mistral
  - hype: ~35%, news_reaction: ~28%, thesis: ~28%, options_yolo: ~5%, 
    loss_porn: ~4%, meme: ~3%, other: ~0.2%
- **Ticker mentions:** 814,317 in post_tickers table
- **Forward returns:** 687,791 rows with price data
- **Price rows:** 4,318,636 (Alpaca + yfinance, 2012-2021)
- **Signal scores (v7):** 47,411 ticker-day rows
- **Daily mentions (YoloStocks):** 435,755 rows, 2021-2025 + live

---

## Signal Scorer v7

The scorer assigns a 0-100 score to each ticker-day combination.

**Weights:**
- Mention velocity: 35% (shaped: 3-5x = sweet spot, >5x penalized)
- Classification quality: 30% (hype weighted positive, thesis neutral)
- Subreddit diversity: 15% (1 sub=0, 2=33, 3=67, 4+=100 points)
- Mention count: 12% (percentile ranked)
- Avg post score: 8% (upvotes)

**Backtested performance (47,411 rows, 2012-2021):**
- High signal (>70): +4.49% avg 7d, +11.21% avg 30d, 54.5% win rate
- Baseline: +1.14% avg 7d, +2.89% avg 30d
- Alpha: +3.35% at 7d, +8.31% at 30d

**Key findings:**
- Hype beats thesis: hype-heavy days return +1.83% 7d vs +0.74% thesis-heavy
- Velocity sweet spot: 3-5x normal = +2.19% 7d; >5x = +0.34% (reversal risk)
- Cross-community consensus: 4+ subreddits = +3.76% 7d, +11.46% 30d
- Slow burn + hype: below-avg velocity + hype posts = +2.36% 7d, +4.99% 30d
- 1-25% thesis mix: sweet spot returning +3.68% 7d, +13.23% 30d

---

## Paper Trading Rules (Phase 4)

- **Entry — HOT_SCORE:** signal score >70 AND velocity 3-5x
- **Entry — SLOW_BURN:** signal score >60 AND velocity <0.5x
- **Entry — SQUEEZE_WATCH bonus:** +10 score if days-to-cover >5
- **Never trade:** EXTREME velocity (>5x), penny stocks (<$5), no Alpaca data
- **Position size:** $100 per trade
- **Max positions:** 3 open simultaneously
- **Max exposure:** $500 total
- **Exit:** +15% take profit, -8% stop loss, 7 days held
- **No shorting in v1**

---

## Automated Schedule (Pi crontab, CDT)

```
0 6    * * *   Daily pipeline (fetch → extract → calc → report → trade)
0 8    * * 0   Weekly stats summary (Sunday)
*/30 8-14 * * 1-5  Intraday position monitor (every 30 min, weekdays)
0 15   * * 1-5     Final position check + daily summary notification (4pm ET)
0 16:30 * * 5      Weekly Claude API digest (Friday after market close)
0 7    1,15 * *    Short interest fetch (1st and 15th of month)
```

---

## Live Data Sources

- **YoloStocks** — free, no API key, updates every 15 min
  - URLs: https://yolostocks.live/downloads/wallstreetbets.csv (+ stocks, investing, pennystocks, options)
  - Covers: WSB, stocks, investing, pennystocks, options subreddits
  - Currently pulled once daily at 6am

- **Alpaca** — paper trading + market data
  - Paper account connected, $100/trade
  - Free tier, rate limited at 200 req/min
  - Base URL: https://paper-api.alpaca.markets

- **Reddit API** — PENDING APPROVAL
  - Submitted request via Reddit support form
  - Will add live post scraping when approved
  - Account: Physical_Ad5496

- **yfinance** — historical prices pre-2020
  - Cache location: /tmp/yf_cache (important: avoids SQLite conflict)
  - Version pinned at 0.2.58 (avoids websockets conflict with alpaca)

---

## Notifications

- **Pushover** — configured on Pi
  - Fires on: BUY placed, SELL executed, daily summary at 4pm ET
  - Credentials in .env as PUSHOVER_USER_KEY and PUSHOVER_API_TOKEN

---

## Weekly Claude API Digest

- Runs every Friday at 4:30pm CDT
- Sends 7-day performance summary to claude-sonnet-4-20250514
- Saves response to logs/weekly_digest_YYYY-MM-DD.txt
- Sends Pushover notification when complete
- Credential in .env as ANTHROPIC_API_KEY
- **Important:** digest is read-only analysis. Changes to scorer/prompts 
  require human review and manual Claude Code implementation.

---

## Credentials (.env on Pi)

All stored at ~/wsb-signal-lab/.env — never committed to GitHub.
```
ALPACA_API_KEY=...
ALPACA_SECRET_KEY=...
ALPACA_BASE_URL=https://paper-api.alpaca.markets
PUSHOVER_USER_KEY=...
PUSHOVER_API_TOKEN=...
ANTHROPIC_API_KEY=...
```

---

## Branding

Project renamed from "WSB Signal Lab" to **Murmur**.
Logo assets in dashboard/static/images/:
- murmur_square_icon.png (favicon)
- murmur_horizontal_lockup.png (header logo)
- murmur_dashboard_header_banner.png (alternate)

---

## Known Issues / Todo List

**High priority:**
- [ ] Reddit API approval still pending — add live scraper when approved
- [ ] Favicon not rendering in some browsers (code is correct, likely cache)
- [ ] More 2020 posts need classification to improve signal coverage

**Medium priority:**
- [ ] Options flow data (Unusual Whales free feed) as additional signal layer
- [ ] Intraday YoloStocks pulls (every 30 min during market hours) for Phase 5
- [ ] Crypto track via separate exchange API (Coinbase Advanced or Kraken)
- [ ] Subreddit-specific classifier tuning (r/options posts need different labels)
- [ ] News sentiment from financial RSS feeds

**Lower priority:**
- [ ] Fear and greed index integration
- [ ] Real money pilot after consistent paper trading results
- [ ] Rename ~/wsb-signal-lab directory to ~/murmur on Pi (low priority, risky)

---

## Phase Completion Status

- [x] Phase 1: Data pipeline, SQLite, Alpaca connection, dashboard
- [x] Phase 2: Local LLM classification (Mistral via Ollama)
- [x] Phase 3: Signal scorer v7 with backtested alpha
- [x] Phase 4: Paper trading automation + intraday monitoring
- [x] Phase 4.5: Short interest integration, Pushover notifications
- [x] Phase 4.6: Weekly Claude API digest
- [ ] Phase 5: Live signal validation (in progress — starts Monday June 2)
- [ ] Phase 6: Real money pilot (after consistent paper trading results)
- [ ] Phase 7: Crypto track

---

## Next Session Priorities

1. Check Monday's first live paper trades and signal report
2. Review Friday's first weekly digest
3. Add options flow data (Unusual Whales)
4. Run more 2020 post classification to improve signal coverage
5. Consider intraday YoloStocks pulls once paper trading validates signals

---

## Key Commands

```bash
# SSH into Pi
ssh plex@192.168.1.45

# Check dashboard
http://192.168.1.45:5001

# Check today's signal report
ssh plex@192.168.1.45 "cat ~/wsb-signal-lab/logs/daily_report_$(date +%Y-%m-%d).txt"

# Check paper trade status
ssh plex@192.168.1.45 "cd ~/wsb-signal-lab && venv/bin/python scrapers/paper_trader.py --status"

# Check classification progress
tail -f ~/wsb-signal-lab/logs/classify_nohup.out

# Check position monitor log
ssh plex@192.168.1.45 "tail -20 ~/wsb-signal-lab/logs/cron_positions.log"

# Run full pipeline manually
ssh plex@192.168.1.45 "cd ~/wsb-signal-lab && venv/bin/python scripts/run_daily.py"

# Copy updated database from Mac to Pi
scp ~/wsb-signal-lab/data/wsb.db plex@192.168.1.45:~/wsb-signal-lab/data/wsb.db

# Start Claude Code
cd ~/wsb-signal-lab && claude
```

---

*Built over two days, May 29-30 2026, with Claude Sonnet.*
*"Like a murmuration — thousands of voices forming a single signal."*
