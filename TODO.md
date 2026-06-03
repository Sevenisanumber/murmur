# Murmur — Running To-Do List
_Last updated: June 3, 2026_

---

## High Priority

- [ ] **Reddit API approval** — submitted May 31 via Reddit support form (account: Physical_Ad5496). Add live post scraper for r/WallStreetBets, r/stocks, r/investing, r/pennystocks, r/options once approved.
- [ ] **Favicon not rendering in some browsers** — code is correct, likely a cache issue. Investigate and fix.

---

## Medium Priority

- [ ] **Incorporate `is_bullish` into signal scorer** — direction tracking is now captured on all new classifications. Add as a scorer weight once the Mac classification batch completes. Est. ready Friday June 5.
- [ ] **Validate v7 signal alpha against 2022–2025 data** — price fetch in progress on Pi (`fetch_prices_2022_2025.py`). Run `score_signals.py` and `calc_returns.py` against new data once fetch completes.
- [ ] **Copy updated DB from Mac to Pi** — after classification batch finishes, SCP the updated `wsb.db` back to Pi. Reminder: use SCP, not git.
- [ ] **Adaptive intraday entry checks** — currently entries only fire once at 9am. Add intraday entry windows every 30–60 minutes during market hours (9am–3pm CDT, weekdays), with adaptive frequency based on market activity:
  - High-activity day (>X mentions, high velocity signals): check every 20–30 minutes
  - Normal day: check every 60 minutes
  - **Prerequisite:** requires intraday YoloStocks pulls before each check (currently pulled once at 6am) — fresh mention data is needed for intraday signals to be meaningful
  - **Guard:** same ticker can only be entered once per day regardless of how many checks fire
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
