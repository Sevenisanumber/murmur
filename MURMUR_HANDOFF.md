# Murmur — Project Handoff Document
**Last updated: June 18, 2026**
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
  - **venv moved to USB flash drive** at `/mnt/ollama/wsb-venv`, symlinked back to `~/wsb-signal-lab/venv`
  - SD card now at ~95% capacity — Docker (17GB) is the main culprit; needs to move to SSD in a planned maintenance window
- **Mac (M1 Max, 32GB)** — used for heavy batch jobs (classification) only
  - Pi is fully self-sufficient for daily operations
  - Mac only needed for large one-time classification runs
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
    daily_report.py        — generates daily watchlist report (Murmur branded)
    paper_trader.py        — Alpaca paper trading automation
    check_positions.py     — intraday position monitor
    fetch_short_interest.py — yfinance short interest data
    fetch_daily_mentions.py — live YoloStocks data fetcher
    fetch_reddit_posts.py  — Arctic-Shift live Reddit scraper (pulls every 30 min during market hours)
    fetch_earnings.py      — yfinance earnings calendar for active tickers
    import_archive.py      — YoloStocks historical archive importer
    notify.py              — Pushover notifications (incl. morning briefing)
  scripts/
    init_db.py             — SQLite schema (includes short_interest table)
    run_daily.py           — daily pipeline orchestrator
    weekly_stats.py        — Sunday stats summary
    weekly_digest.py       — Friday Claude API feedback digest
    backup_db.py           — weekly database backup to /mnt/media/backups/murmur/
  dashboard/
    app.py                 — Flask dashboard (Murmur branded)
    templates/index.html   — dashboard UI
    static/images/         — Murmur branding assets
  data/                    — symlink to /mnt/media/wsb-signal-lab-data
    wsb.db                 — main SQLite database (~1.5GB)
  logs/                    — all run logs
```

---

## Database State (as of May 31, 2026)

- **Total posts:** 1,982,153
- **Date range:** April 2012 to December 2021
- **Subreddits:** wallstreetbets, gamestop, stocks, pennystocks, investing, options
- **Sources:** kaggle (1,151,459) + leukipp (830,694)
- **Unique authors:** 698,777
- **Post classifications:** ~115,050 posts classified by Mistral
  - hype: ~35%, news_reaction: ~28%, thesis: ~28%, options_yolo: ~5%,
    loss_porn: ~4%, meme: ~3%, other: ~0.2%
  - 487,468 unclassified ticker-mention posts remain; background batch job
    running on Mac via `--priority-unclassified` (ordered by score DESC)
  - `posts` table now has `is_bullish INTEGER` column (1=bullish, 0=bearish,
    NULL=neutral/unclear); populated on new classifications going forward
- **Ticker mentions:** 814,317 in post_tickers table
- **Forward returns:** 687,791 rows with price data
- **Price rows:** 4,318,636 (Alpaca + yfinance, 2012-2021)
- **Signal scores (v7):** 47,411 ticker-day rows
- **Daily mentions (YoloStocks):** 435,920 rows, 2021-2025 + live
- **Short interest:** 51-53 records (fetched manually May 31, auto-fetches 1st/15th)

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

**Upcoming (not yet in scorer):**
- `is_bullish` direction tracking (1=bullish, 0=bearish, NULL=neutral) is now
  captured on all new classifications. Will be incorporated into scorer weights
  in a future version once the classification batch completes.

---

## Daily Report Flags

The daily watchlist report shows these signal flags per ticker:

- **HOT** — velocity 3-5x, historical +1.79% avg 7d, 58.6% win rate
- **EXTREME** — velocity >5x, CAUTION, historical -3.41% avg 7d
- **RISING** — velocity 1.5-3x, building momentum
- **SLOW_BURN** — velocity <0.5x, historical +7.29% avg 30d, 57.2% win rate
- **SQUEEZE_WATCH** — days-to-cover >5 AND active WSB mentions
- **OPTIONS_ACTIVE** — ticker appeared in options_yolo classified posts
- **EARNINGS_NEAR** — earnings within 5 days; position size reduced to $50

---

## Paper Trading Rules (Phase 4)

- **Entry — HOT_SCORE:** signal score >70 AND velocity 3-5x
- **Entry — SLOW_BURN:** signal score >=30 AND velocity <0.5x
  *(threshold lowered to 30 on June 13, 2026, to gather live validation data.
  The original >60 threshold was mathematically unreachable: the velocity scoring
  curve caps SLOW_BURN tickers at a live_score of ~58, since vel<0.5 contributes
  <10 points out of 50 possible from the velocity component.)*
- **Entry — SQUEEZE_WATCH bonus:** +10 score if days-to-cover >5
- **Market regime filter:** HOT_SCORE entries suppressed when SPY is below its
  50-day SMA (BEARISH regime). SLOW_BURN and SQUEEZE_WATCH are unaffected.
  Fails open to BULLISH if Alpaca data is unavailable.
- **SPY regime data:** `refresh_spy_price()` fetches 90 days of SPY bars from Alpaca
  via `INSERT OR REPLACE` before each regime check — prevents stale cached values
  from repeating. SPY is not in `post_tickers` and `fetch_prices.py` uses a
  historical date range, so without this step the regime check reads week-old data.
- **Excluded tickers:** `EXCLUDED_TICKERS` set in `paper_trader.py` — never entered
  regardless of signal score. Logged as `[SKIP] TICKER | excluded (index/ETF/crypto)`.
  Current list: SPY, QQQ, IWM, DIA, SPX, VIX, NDX, RUT, VTI, VOO, VTV, VXUS, BND,
  GLD, SLV, USO, TLT, BTC, ETH, BRK.B, IJR, IVV, FTSE, DRAM, CORN, WHEAT, OIL, GOLD, SILVER
- **Never trade:** EXTREME velocity (>5x), price below $3, no Alpaca data
- **Penny stock pre-filter:** local `prices` table is queried before any Alpaca API
  call; tickers with a cached close below $3 are skipped immediately with
  `[SKIP] ... below $3.00 minimum (local DB)` — avoids wasted API calls for tickers
  like CXAI ($0.19) that would otherwise appear as "no Alpaca price data"
- **Price fetch:** `api.get_latest_bar(ticker).c` is the primary method; falls back
  to `get_bars('1Day', limit=1)`. Replaced `get_bars('1Min')` which returned empty
  for most tickers on the free Alpaca tier, especially outside market hours.
- **SLOW_BURN logging:** candidates evaluated but below the score threshold log
  `[SKIP-SB]` at INFO level with score and velocity — visible in `paper_trades.log`
- **Market closed handling:** paper_trader checks Alpaca clock API; exits cleanly
  on weekends and holidays — expected behavior, not an error
- **Position size:** $100 per trade ($50 if EARNINGS_NEAR)
- **Max positions:** 10 open simultaneously
- **Max exposure:** $500 reference cap (not hard-enforced; paper data collection
  intentionally allows all 10 slots to fill)
- **Exit — HOT_SCORE / SQUEEZE_WATCH:** +15% take profit, -8% stop loss, 7 days held
- **Exit — SLOW_BURN:** +15% take profit, -15% stop loss, 25 days held
  (edge is at 30d, not 7d — was exiting too early before Phase 4.8; wider stop added
  Phase 4.10 because low-velocity stocks have more early noise and need room to breathe)
- **No shorting in v1**

---

## Notifications (Pushover)

All notifications via notify.py using PUSHOVER_USER_KEY and PUSHOVER_API_TOKEN.

| Event | When | Notes |
|---|---|---|
| Morning briefing | ~6:30am CDT daily | Top signals, HOT/SQUEEZE/OPTIONS flags |
| BUY placed | On trade entry | Suppressed on --dry-run |
| SELL executed | On trade exit | Suppressed on --dry-run |
| Daily summary | 3pm CDT weekdays | P&L, open positions |
| Weekly digest ready | Friday ~4:35pm CDT | Claude API analysis complete |
| Backup failure | Friday ~5pm CDT | Silent on success |

**Morning briefing format:**
```
Murmur 06-02 | 42 tickers
🔥 TSLA 89.2 HOT OPTIONS_ACTIVE
📈 SPCE 75.3 RISING
📊 GME 64.5 SQUEEZE_WATCH
Slow burns: 9 | Squeeze: 4 | Opts: 5
📈 Regime: BULLISH — entries enabled
```
Regime line shows `📉 Regime: BEARISH — HOT_SCORE entries suppressed` when SPY
is below its 50-day SMA. Omitted entirely if the SPY check fails.

---

## Automated Schedule (Pi crontab, CDT)

```
0 6    * * *       Daily pipeline (fetch → extract → calc → report → trade → notify)
0 8    * * 0       Weekly stats summary (Sunday)
*/30 8-14 * * 1-5  Intraday position monitor (every 30 min, weekdays)
*/30 8-16 * * 1-5  Reddit post scraper — Arctic-Shift (every 30 min, market hours)
0 15   * * 1-5     Final position check + daily summary notification (4pm ET)
30 16  * * 5       Weekly Claude API digest (Friday 4:30pm CDT)
0 17   * * 5       Weekly database backup (Friday 5pm CDT, after digest)
0 7    1,15 * *    Short interest fetch (1st and 15th of month)
```

---

## Git Deployment

The Pi is now a proper git repo. Deploy code changes with:

```bash
ssh plex@192.168.1.45 "cd ~/wsb-signal-lab && git pull"
```

SSH deploy key configured at ~/.ssh/github_murmur on the Pi.
The database (wsb.db) is NOT in git — too large. Use SCP for database updates.

---

## Live Data Sources

- **YoloStocks** — free, no API key, updates every 15 min
  - URLs: https://yolostocks.live/downloads/wallstreetbets.csv (+ stocks, investing, pennystocks, options)
  - Currently pulled once daily at 6am

- **Alpaca** — paper trading + market data
  - Paper account connected, $100/trade
  - Free tier, rate limited at 200 req/min
  - Base URL: https://paper-api.alpaca.markets

- **Arctic-Shift** — free public Reddit archive API, no key required
  - Endpoint: https://arctic-shift.photon-reddit.com/api/posts/search
  - Data current to within ~1 hour; pulls last 100 posts per subreddit per run
  - Per-subreddit fetch state tracked in `logs/reddit_fetch_state.json` (timestamp of last-seen post)
  - Used alongside YoloStocks — YoloStocks provides mention counts, Arctic-Shift provides post text for classification
  - Subreddits: wallstreetbets, stocks, investing, pennystocks, options

- **Reddit API** — PENDING APPROVAL
  - Submitted May 31, 2026 via Reddit support form; follow-up email sent June 18
  - Subreddits requested: r/WallStreetBets, r/stocks, r/investing, r/pennystocks, r/options
  - Account: Physical_Ad5496
  - Arctic-Shift is the working interim solution; official API will enable real-time access without archive lag

- **yfinance** — historical prices pre-2020
  - Cache location: /tmp/yf_cache (important: avoids SQLite conflict)
  - Version pinned at 0.2.58 (avoids websockets conflict with alpaca)

---

## Weekly Claude API Digest

- Runs every Friday at 4:30pm CDT
- Sends 7-day performance summary to claude-sonnet-4-6
- Saves response to logs/weekly_digest_YYYY-MM-DD.txt
- Sends Pushover notification when complete
- Credential in .env as ANTHROPIC_API_KEY
- **Important:** digest is read-only analysis. Changes to scorer/prompts
  require human review and manual Claude Code implementation.

---

## Database Backups

- Script: scripts/backup_db.py
- Schedule: Every Friday at 5pm CDT (after weekly digest)
- Location: /mnt/media/backups/murmur/wsb_YYYY-MM-DD.db
- Retention: Last 4 backups kept, older ones pruned automatically
- Notification: Pushover on failure only (silent on success)

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

Project named **Murmur** (renamed from WSB Signal Lab May 30, 2026).
Logo assets in dashboard/static/images/:
- murmur_square_icon.png (favicon)
- murmur_horizontal_lockup.png (header logo — used in dashboard)
- murmur_dashboard_header_banner.png (alternate)

---

## Known Issues / Todo List

See **TODO.md** in the project root for the full task list (high / medium / low / ideas).

Current open items as of June 18, 2026: Docker data directory move to SSD (Pi at 95% SD capacity), Reddit API approval pending, favicon cache issue, is_bullish scorer integration, 2022–2025 signal validation, adaptive intraday entry checks, and Arctic-Shift data lag evaluation. See TODO.md for full list.

---

## Phase Completion Status

- [x] Phase 1: Data pipeline, SQLite, Alpaca connection, dashboard
- [x] Phase 2: Local LLM classification (Mistral via Ollama)
- [x] Phase 3: Signal scorer v7 with backtested alpha
- [x] Phase 4: Paper trading automation + intraday monitoring
- [x] Phase 4.5: Short interest integration, Pushover notifications
- [x] Phase 4.6: Weekly Claude API digest
- [x] Phase 4.7: Morning briefing notification, OPTIONS_ACTIVE flag,
      weekend/holiday market handling, git deployment on Pi, weekly DB backup
- [x] Phase 4.8: SLOW_BURN hold fix, market regime filter, is_bullish
      classification, earnings calendar flag, 2022-2025 price fetch
- [x] Phase 4.9: Fix stale SPY regime price (refresh_spy_price() before each check),
      fix SLOW_BURN entry threshold (>=30, was mathematically unreachable at >60),
      add EXCLUDED_TICKERS for indices/ETFs/crypto, fix price fetch to use
      get_latest_bar(), add penny stock pre-filter before Alpaca API call,
      add [SKIP-SB] INFO logging for SLOW_BURN candidates
- [x] Phase 4.10: Arctic-Shift live Reddit scraper (fetch_reddit_posts.py, 30-min intraday
      cron), split stop losses by signal type (HOT_SCORE -8%, SLOW_BURN -15%),
      expanded EXCLUDED_TICKERS with commodity/index names, venv moved to USB drive
      to free SD card space, classify_posts.py --post-ids mode with lock file
- [ ] Phase 5: Live signal validation (starts Monday June 2, 2026)
- [ ] Phase 6: Real money pilot (after consistent paper trading results)
- [ ] Phase 7: Crypto track

---

## Next Session Priorities

1. Check Monday June 2 first live paper trades and morning briefing
2. Verify EARNINGS_NEAR flag appearing correctly in daily report
3. Check fetch_prices_2022_2025.py progress on Pi
4. Check classify_posts.py batch progress on Mac
5. Review Friday June 5 first weekly digest from Claude API

---

## Key Commands

```bash
# SSH into Pi
ssh plex@192.168.1.45

# Check dashboard
http://192.168.1.45:5001

# Deploy code updates to Pi (use this instead of SCP for scripts)
ssh plex@192.168.1.45 "cd ~/wsb-signal-lab && git pull"

# Check today's signal report
ssh plex@192.168.1.45 "cat ~/wsb-signal-lab/logs/daily_report_$(date +%Y-%m-%d).txt"

# Check paper trade status
ssh plex@192.168.1.45 "cd ~/wsb-signal-lab && venv/bin/python scrapers/paper_trader.py --status"

# Run full pipeline manually
ssh plex@192.168.1.45 "cd ~/wsb-signal-lab && venv/bin/python scripts/run_daily.py"

# Check position monitor log
ssh plex@192.168.1.45 "tail -20 ~/wsb-signal-lab/logs/cron_positions.log"

# Copy updated database from Mac to Pi (scripts use git pull instead)
scp ~/wsb-signal-lab/data/wsb.db plex@192.168.1.45:~/wsb-signal-lab/data/wsb.db

# Fetch short interest manually
ssh plex@192.168.1.45 "cd ~/wsb-signal-lab && venv/bin/python scrapers/fetch_short_interest.py"

# Run paper trader dry run
ssh plex@192.168.1.45 "cd ~/wsb-signal-lab && venv/bin/python scrapers/paper_trader.py --dry-run"

# Start Claude Code
cd ~/wsb-signal-lab && claude
```

---

*Built May 29-31, 2026, with Claude Sonnet.*
*"Like a murmuration — thousands of voices forming a single signal."*
