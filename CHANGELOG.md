# Changelog

All notable changes to Murmur are documented here. Dates reflect when work
was completed. Signal alpha figures are measured against a buy-and-hold
baseline across the full 47,211 ticker-day dataset (2012–2021).

---

## [Unreleased]

- Short interest integration (SQUEEZE_WATCH flag) — data collected, not yet wired into scorer

---

## 2026-05-31

### Signal Scorer — v7

**Change:** Promoted subreddit diversity from an additive points bonus to a
full 15% weighted component in the composite score formula.

**Why:** In v6, tickers mentioned across 4+ subreddits showed +11.46% 30d
returns, but the flat +15 pt additive bonus wasn't large enough to push
them past the >70 high-signal threshold. As an additive bonus the signal
couldn't compete on equal footing with velocity and classification quality.
Making it a proper weighted component (scored 0–100) fixes that.

**Weights (v7):**

| Component | Weight | Scoring |
|---|---|---|
| Mention velocity | 35% | Shaped 0–100; peak 3–5×, penalty >5× |
| Classification mix | 30% | Hype/options-yolo+, thesis −0.2 penalty |
| Subreddit diversity | 15% | 1 sub=0, 2=33, 3=67, 4+=100 |
| Mention count | 12% | Log-scale, percentile-ranked |
| Avg post score | 8% | Upvotes, percentile-ranked |

**Result:** Alpha 7d = **+3.35%** / 30d = **+8.31%** (high-signal >70, n=208)

---

## 2026-05-30

### Signal Scorer — v6

**Change:** Replaced the subreddit quality multiplier (v5) with an additive
cross-community diversity bonus.

**Why:** v5 revealed that mid-mix subreddit tickers (0.7–0.9 average weight)
outperformed pure WSB: +3.01% 7d vs +0.74% for pure WSB. The multiplier
approach penalised non-WSB posts rather than rewarding consensus. The bonus
approach directly rewards agreement across communities: 1 sub = +0 pts,
2 subs = +5 pts, 3 subs = +10 pts, 4+ subs = +15 pts.

**Result:** Alpha 7d = **+3.01%** / 30d = **+11.46%**

**Key finding:** 4+ subreddit tickers returned +11.46% at 30d — the strongest
30d signal observed to date. Led directly to v7.

---

### Multi-subreddit data — leukipp dataset

Imported the leukipp multi-subreddit dataset (830k posts across r/wallstreetbets,
r/stocks, r/investing, r/options, r/pennystocks). Added `posts.subreddit` column
and backfilled all existing rows. This data is what enables subreddit diversity
scoring in v6 and v7.

---

### Signal Scorer — v5

**Change:** Added a subreddit quality multiplier to the composite score.
Tickers with higher proportions of non-WSB mentions received a score boost.

**Why:** Hypothesis that cross-community attention indicates more substantive
interest than pure WSB hype.

**Result:** Alpha 7d = **+3.01%** / 30d = **+8.33%**

**Key finding:** Mid-mix (0.7–0.9 subreddit weight) outperformed both pure WSB
and pure non-WSB, suggesting the sweet spot is overlapping community attention,
not any single community.

---

### Phase 4 — Paper Trading

Automated paper trading simulation against Alpaca's paper environment.

**Entry rules:**
- Score > 70 and velocity tag = HOT (3–5× rolling average) → BUY
- Score > 60 and velocity tag = SLOW\_BURN (<0.5× rolling average) → BUY
- Velocity tag = EXTREME (>5×) → always skip

**Position limits:** $100 per trade, max 3 open positions, $500 total exposure.  
**Exit rules:** 7 calendar days, +15% profit target, or −8% stop loss.  
**Hard limits:** No price < $5, no shorting, market must be open.

Status as of v7: actively running on Pi, tracking open positions and P&L
via dashboard and Pushover notifications.

---

### Signal Scorer — v4

**Change:** Eased thesis weight from −0.5 to −0.2. Added `slow_burn` flag
(velocity ratio < 0.5).

**Why:** Pearson r(thesis fraction, 7d return) measured −0.018 on the full
expanded dataset — statistically near-neutral. The −0.5 penalty applied in v3
was calibrated on a smaller 20k-row sample and was too aggressive. Eased to
−0.2 to reflect the actual signal strength. The `slow_burn` flag captures a
separate pattern: below-average velocity tickers show stronger 30d returns.

**Result:** Alpha 7d = **+2.76%** / 30d = **+5.29%**

---

### Signal Scorer — v3

**Change:** Applied thesis = −0.5 penalty to classification mix. Added a
velocity ceiling (penalises >5× spikes) with a shaped piecewise scoring curve
peaking at 3–5×.

**Why:** Extremely high velocity (>5× rolling average) often precedes reversal.
Pure momentum without a ceiling was rewarding overheated situations. The thesis
penalty reflected early data suggesting DD posts correlated with lower returns.

**Result:** Alpha 7d = **+2.92%** / 30d = **+5.11%**

---

### Signal Scorer — v2

**Change:** Momentum-first weight design. Thesis contribution set to zero
(removed penalty). Mention velocity became the dominant component.

**Why:** v1 put classification quality first and used a thesis diversity weight
of 15%, producing negative alpha. Switching to a momentum-first design (velocity
as leading signal, classification as secondary) immediately recovered alpha.

**Result:** Alpha 7d = **+2.43%** / 30d = **+1.59%**

---

### Signal Scorer — v1 (initial)

**Design:** Thesis/classification quality first, 15% diversity weight,
velocity secondary.

**Result:** Alpha 7d = **−0.07%** / 30d = **−2.99%** — worse than the
buy-and-hold baseline. Thesis-first approach selected for deliberate analysis
posts, which turned out to predict lower returns than hype posts.

**Learning:** Retail sentiment alpha comes from momentum and crowd energy,
not analytical quality. This finding drove all subsequent versions.

---

## 2026-05-29

### Phase 1 — Data Pipeline (complete)

- SQLite database with WAL mode on Raspberry Pi 5
- Reddit WSB post ingestion from Kaggle archive (reddit\_wsb.csv, 2012–2021)
- Ticker extraction from post titles and bodies
- Historical price data from Alpaca Markets (post-2020) and Yahoo Finance (pre-2020, via yfinance)
- Forward return calculation: 1d, 7d, 30d per (ticker, date) pair
- Daily mention data from YoloStocks live CSV feed
- Dashboard: Flask app with live pulse, signal history, and data health sections
- Cron jobs for daily data collection on Pi

### Phase 2 — LLM Classification (complete)

- Post classification using Mistral via Ollama (fully local, no API cost)
- Labels: `hype`, `options_yolo`, `thesis`, `news_reaction`, `meme`, `loss_porn`, `other`
- Batch classification of 830k+ posts across the full dataset
- Alias normalisation for common Mistral response variants

### Phase 3 — Signal Scoring (active)

- Composite signal scorer producing a 0–100 score per (ticker, date)
- Currently on v7; full version history above
- `slow_burn` flag: below-average velocity tickers with stronger 30d pattern
- `sub_diversity` stored per row for cross-community analysis
- Short interest data collected (SQUEEZE\_WATCH flag); pending integration into scorer

---

## Backtesting — Key Findings

All figures measured on 47,211 ticker-day rows, 2012–2021 WSB dataset.
Baseline (buy all signals equally): 7d = +1.14% / 30d = +2.89%.

| Finding | Detail |
|---|---|
| Hype > thesis | Hype-heavy posts (+1.85% 7d) outperform thesis-heavy (+0.75% 7d). Retail alpha is momentum, not analysis. |
| Velocity sweet spot | 3–5× rolling average is the signal. <0.5× is slow-burn (strong 30d). >5× often precedes reversal. |
| Slow-burn 30d | Velocity <0.5× tickers: +4.30% avg 30d vs +2.78% for normal/high velocity. |
| Cross-community consensus | 4+ subreddit mentions: +3.76% 7d / +11.46% 30d. The strongest single factor discovered. |
| Thesis as weak negative | Pearson r(thesis fraction, 7d return) = −0.0085. Near-neutral; slight negative direction confirmed across all versions. |
| Extreme velocity risk | >5× spikes average only +0.34% 7d with 48.4% win rate — below baseline. |
| v7 high-signal bucket | Score >70: +4.49% 7d / +11.21% 30d on 208 ticker-days (n=208 / 47,211 total). |
