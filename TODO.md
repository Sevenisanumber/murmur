# Murmur — Running To-Do List
_Last updated: June 18, 2026_

---

## High Priority

- [ ] **Reddit API approval** — submitted May 31 via Reddit support form (account: Physical_Ad5496); follow-up email sent June 18. Arctic-Shift is the working interim solution. Add live post scraper for r/WallStreetBets, r/stocks, r/investing, r/pennystocks, r/options once approved.
- [ ] **Favicon not rendering in some browsers** — code is correct, likely a cache issue. Investigate and fix.
- [ ] **Move Docker data directory to SSD** — Docker is consuming 17GB on the 29GB SD card, keeping the Pi at 95% disk usage. Move `/var/lib/docker` to `/mnt/media` to free up SD card space. Requires stopping all containers, moving data, updating Docker daemon config, and restarting. ARM ripper containers must be verified working after the move. Do this during a planned maintenance window, not mid-trading-week.

---

## Medium Priority

- [ ] **Incorporate `is_bullish` into signal scorer** — direction tracking is now captured on all new classifications. Add as a scorer weight once the Mac classification batch completes. Est. ready Friday June 5.
- [ ] **Validate v7 signal alpha against 2022–2025 data** — price fetch in progress on Pi (`fetch_prices_2022_2025.py`). Run `score_signals.py` and `calc_returns.py` against new data once fetch completes.
- [ ] **Adaptive intraday entry checks** — currently entries only fire once at 9am. Add intraday entry windows every 30–60 minutes during market hours (9am–3pm CDT, weekdays), with adaptive frequency based on market activity:
  - High-activity day (>X mentions, high velocity signals): check every 20–30 minutes
  - Normal day: check every 60 minutes
  - **Prerequisite previously blocked:** fresh mention data needed before each check — now partially met by Arctic-Shift pulling every 30 min. YoloStocks still only pulled once at 6am.
  - **Guard:** same ticker can only be entered once per day regardless of how many checks fire
- [ ] **Evaluate Arctic-Shift data lag** — currently ~1 hour behind live Reddit. Monitor whether this affects signal quality vs real-time Reddit API. Compare HOT_SCORE entries made on Arctic-Shift-classified posts vs YoloStocks-only entries once a meaningful sample accumulates.
- [ ] **Investigate MU intermittent "no Alpaca price data"** — MU ($983) is a liquid ticker but occasionally appears as having no price data despite the fix to `get_latest_bar()`. May be a rate limit or feed availability issue on the free Alpaca tier. Add retry logic or a delay between price fetches if it persists.
- [ ] **Expand EXCLUDED_TICKERS as new ETFs/indices appear in logs** — SRXH and SPCX flagged this week. Audit signal logs periodically and add non-tradeable tickers to the exclusion list in `paper_trader.py`.
- [ ] **Options flow data** — Unusual Whales API requires paid subscription ($250/mo). Evaluate Alpaca options chain as a free alternative before committing.
- [ ] **Subreddit-specific classifier tuning** — r/options posts use different language patterns and need their own label set. Currently using WSB labels everywhere.
- [ ] **News sentiment from financial RSS feeds** — add as a signal input layer. Potential sources: Reuters, Bloomberg RSS, SEC EDGAR filings.
- [ ] **More 2020 post classification** — 2020 is the highest-volume year in the dataset and the most underclassified. Prioritize when scheduling next batch run.
- [ ] **Weekly digest continuity — pass previous week's analysis to Claude** — currently each Friday digest is a fresh context window with no memory of prior weeks. Pass the last 2 weeks of digest responses alongside the new data payload so Claude can identify trends and follow up on prior suggestions.
  - Read the 2 most recent `logs/weekly_digest_YYYY-MM-DD.txt` files
  - Prepend to API call as "Previous weeks' analysis:" section
  - Gives Claude continuity: "last week I flagged X, here's whether it held"
  - Lightweight change, ~15–20 lines in `scripts/weekly_digest.py`
  - **Implement after first digest runs clean (week of June 9)**

---

## Low Priority

- [ ] **Fear and greed index integration** — add CNN F&G index as a regime-level input alongside the SPY 50-SMA filter.
- [ ] **Rename `~/wsb-signal-lab` to `~/murmur` on Pi** — low priority, potentially disruptive to cron paths, symlinks, and SSH muscle memory. Do during a maintenance window with full path audit first.

---

## Ideas / Future Consideration

- [ ] **Real money pilot (Phase 6)** — after consistent paper trading results over a meaningful sample (suggested: 20+ closed trades, positive realized P&L). Requires separate Alpaca live account.
- [ ] **Crypto track (Phase 7)** — separate signal pipeline for crypto via Coinbase Advanced or Kraken API. Needs its own velocity baseline and scorer calibration.
- [ ] **Intraday entry logic (post Arctic-Shift)** — the data prerequisite for intraday entries is now partially met: Arctic-Shift pulls post text every 30 min during market hours. Build intraday entry logic once current paper trading results validate the signal over several more weeks of data.
