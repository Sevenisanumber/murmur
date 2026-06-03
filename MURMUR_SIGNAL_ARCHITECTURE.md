# Murmur — Signal Architecture & System Design
**Last updated: June 2, 2026**

---

## North Star

An autonomous hybrid quant + AI agent system that synthesizes Reddit sentiment signals
and traditional market data to make intelligent, data-backed trades. The system continuously
improves on itself through logged outcome feedback, eventually managing options and crypto
as confidence in its core signal matures.

---

## System Layers Overview

```
LAYER 1: Sentiment Signal (Murmur — exists, being validated)
LAYER 2: Market Data Signal (to be built after live validation)
LAYER 3: LLM Synthesis Agent (future — quality ceiling set by layers 1 & 2)
LAYER 4: Outcome Feedback Loop (connective tissue, enables self-improvement)
```

---

## Layer 1: Sentiment Signal

**Purpose:** Detect community excitement patterns that precede price movement.
The core insight is that *hype beats thesis* on WSB, and cross-community consensus
is a meaningful leading indicator.

### Data Sources
- YoloStocks (live, daily pulls — WSB, stocks, investing, pennystocks, options)
- Reddit API (pending approval — will add live post scraping)
- Kaggle / leukipp historical datasets (2012–2021, training and backtest base)

### Data Collected Per Ticker-Day
- Mention count (raw volume)
- Mention velocity (ratio vs rolling 30-day average)
- Subreddit diversity (how many distinct communities mentioning)
- Post upvote scores (proxy for community engagement quality)
- Classification breakdown: hype / thesis / news_reaction / options_yolo / loss_porn / meme
- is_bullish ratio (what % of classified posts have bullish sentiment)
- Short interest / days-to-cover (bi-monthly, yfinance)
- Earnings proximity flag (within 5 days)

### Positive Signal Weights
| Factor | Strength | Notes |
|---|---|---|
| Velocity 3-5x | Strong | Backtested sweet spot |
| 4+ subreddit consensus | Strong | +3.76% 7d, +11.46% 30d historically |
| High hype classification ratio | Moderate | Hype beats thesis on WSB |
| Bullish is_bullish majority | Moderate | Direction confirmation |
| SLOW_BURN (velocity <0.5x + hype posts) | Strong at 30d | +7.29% avg 30d |
| High short interest + active mentions | Situational | Squeeze potential |

### Negative Signal Weights
| Factor | Strength | Notes |
|---|---|---|
| Velocity >5x (EXTREME) | Strong negative | Reversal risk, -3.41% avg 7d |
| High loss_porn ratio | Moderate | Sentiment deteriorating |
| Single subreddit only | Moderate | Low conviction, no consensus |
| Bearish is_bullish majority | Moderate | Direction contradiction |
| Earnings within 5 days | Risk flag | Reduces position size to $50 |
| Bear market regime (SPY < 50 SMA) | Strong | Suppresses HOT_SCORE entries |

### Reasoning Function
**Signal Scorer v7 (0–100)**

Weights:
- Mention velocity: 35% (shaped curve — 3-5x = sweet spot, >5x penalized)
- Classification quality: 30% (hype weighted positive, thesis neutral)
- Subreddit diversity: 15% (1 sub = 0, 2 = 33, 3 = 67, 4+ = 100 pts)
- Mention count: 12% (percentile ranked)
- Avg post score: 8% (upvotes)

### Output Per Ticker-Day
- `signal_score` (0–100)
- `signal_flag`: HOT / SLOW_BURN / EXTREME / RISING / SQUEEZE_WATCH / OPTIONS_ACTIVE
- `velocity_ratio`
- `classification_breakdown` (% each category)
- `is_bullish_ratio`
- `subreddit_count`
- `short_interest_dtc` (days-to-cover)
- `earnings_near` (boolean)

---

## Layer 2: Market Data Signal

**Purpose:** Confirm or contradict the sentiment signal with price and volume behavior.
"WSB loves this AND the chart agrees" is a meaningfully higher-conviction setup than
either signal alone.

**Status:** Not yet built. Design below is the target architecture.

### Data Sources
- Alpaca (OHLCV, live and historical intraday)
- yfinance (historical prices, earnings dates)
- SPY / QQQ / IWM (market regime and sector context)
- Sector ETFs (XLK, XLF, XLE, XLV, etc.) for sector-level tailwinds

### Data Collected Per Ticker-Day
- OHLCV (open, high, low, close, volume)
- 20 / 50 / 200-day SMA
- RSI (14-day)
- MACD (12 / 26 / 9) — value and signal line crossover status
- Volume vs 20-day average (volume ratio)
- Average True Range (ATR) — volatility proxy
- Bollinger Band position (upper / mid / lower)
- 52-week high/low proximity
- Distance from key support and resistance levels
- Sector ETF relative strength
- Market regime status (SPY vs 50-day SMA — already implemented in Layer 1)

### Positive Signal Weights
| Factor | Strength | Notes |
|---|---|---|
| Price above 50 and 200 SMA | Strong | Trend confirmation |
| RSI 40–65 | Moderate | Momentum without overbought risk |
| MACD bullish crossover | Moderate | Momentum initiation |
| Volume 1.5x+ with price gain | Strong | Institutional / real interest |
| Price breaking 52-week high | Strong | Continuation breakout pattern |
| Strong sector ETF performance | Moderate | Macro tailwind |
| Healthy market regime | Moderate | Rising tide |

### Negative Signal Weights
| Factor | Strength | Notes |
|---|---|---|
| Price below 50 and 200 SMA | Strong | Fighting the trend |
| RSI >75 | Strong | Overbought, reversal risk |
| MACD bearish crossover | Moderate | Momentum fading |
| Declining volume on price rally | Moderate | Weak / unsupported move |
| Price near major resistance | Moderate | Headwind |
| Weak sector ETF | Moderate | Macro headwind |
| Bear market regime | Strong | Systemic risk (already suppresses entries) |

### Reasoning Function
**Technical Score (0–100)** — to be designed and backtested

Proposed weights (subject to backtesting):
- Trend (price vs MAs): 35%
- Momentum (RSI + MACD): 30%
- Volume confirmation: 20%
- Sector / regime context: 15%

### Output Per Ticker-Day
- `technical_score` (0–100)
- `trend_status`: BULLISH / NEUTRAL / BEARISH
- `momentum_status`: BUILDING / NEUTRAL / FADING / OVERBOUGHT
- `volume_confirmation`: CONFIRMED / WEAK / CONTRADICTED
- `regime_status`: BULL / NEUTRAL / BEAR
- `volatility_level`: LOW / NORMAL / HIGH
- `key_levels`: nearest support and resistance prices
- `sector_strength`: relative ETF performance rank

---

## Layer 3: LLM Synthesis Agent

**Purpose:** Act as a reasoning layer that synthesizes both signal streams, weighs context,
flags contradictions, generates traceable trade recommendations, and eventually manages
the full trade lifecycle autonomously.

**Status:** Future. Cannot be meaningfully built until Layer 1 is live-validated
and Layer 2 is designed and backtested. LLM quality ceiling is set by its inputs.

### Inputs
From Layer 1: signal_score, flag, velocity_ratio, is_bullish_ratio,
classification_breakdown, subreddit_count, short_interest_dtc, earnings_near

From Layer 2: technical_score, trend_status, momentum_status,
volume_confirmation, regime_status, volatility_level, key_levels, sector_strength

Portfolio context: open positions, available capital, current exposure by sector,
recent trade outcomes, time of day / week / market cycle

External context (future): relevant news headlines, macro events, earnings calendar

### Reasoning Functions
**Signal agreement check**
Do sentiment and technical signals agree? Agreement = higher conviction.
Contradiction = explicit flag and reduced position size or skip.

**Horizon matching**
SLOW_BURN signals (30-day edge) need different technical context than HOT signals
(7-day edge). The LLM needs to reason about which technical indicators are relevant
at which time horizon.

**Risk assessment**
Earnings proximity, volatility level, position concentration, correlation with existing
open positions. High volatility + earnings near = hard reduce or skip.

**Contradiction flagging**
Explicit, traceable warnings like: "Community highly bullish but price is below 200 SMA
and RSI is overbought. Sentiment may be lagging price." These surface in the report
and the reasoning log.

**Portfolio context weighting**
Is there already exposure to this sector? Does this ticker correlate with something
already open? The system should think in portfolio terms, not just per-ticker.

**Pattern matching against outcome history**
As the feedback loop (Layer 4) accumulates data: "Setups with these characteristics
have historically underperformed in this regime type." The LLM uses that as context.

### Output Per Decision
- `conviction_score` (0–100, composite of layers 1 and 2)
- `recommendation`: BUY / WATCH / SKIP / SELL / REDUCE
- `position_size`: dollar amount, accounting for volatility and risk flags
- `hold_horizon`: SHORT (7d) / MEDIUM (14d) / LONG (30d)
- `rationale`: plain English, traceable reasoning (stored in log)
- `signal_agreement`: ALIGNED / MIXED / CONTRADICTED
- `risk_flags`: list of active concerns
- `confidence`: HIGH / MEDIUM / LOW

---

## Layer 4: Outcome Feedback Loop

**Purpose:** The connective tissue that makes self-improvement possible. Every prediction
needs to be logged against what actually happened. Without this, there is nothing to
improve against.

**Status:** Partial. Paper trades are logged. Formal prediction-vs-outcome comparison
and weight recalibration are not yet built.

### What Gets Logged Per Decision
- Timestamp and full market context snapshot
- Layer 1 score and inputs at time of decision
- Layer 2 score and inputs at time of decision (once built)
- LLM rationale and conviction score (once built)
- Predicted outcome (expected price move, horizon, direction)
- Actual outcome (realized return, exit reason)
- Delta (how wrong or right was the conviction level?)

### What This Enables Over Time
**Score recalibration:** If SLOW_BURN signals consistently outperform HOT at 30 days
in live data, the scorer weights can be adjusted to reflect reality, not just backtest.

**Regime pattern recognition:** "HOT signals in bear regimes have underperformed
their historical average by X% in live data." Filters tighten automatically.

**Signal degradation detection:** Reddit sentiment patterns drift as the community
and market environment change. Outcome tracking catches when the signal stops working
before too much capital is at risk.

**Performance attribution:** Which layer adds the most alpha? Sentiment alone?
Technical confirmation? Their combination? This tells you where to invest development
effort.

**LLM calibration:** Is the LLM's stated conviction level actually predictive? A 90
conviction score should outperform a 60 conviction score consistently. If it doesn't,
the reasoning function needs rework.

---

## Signal Flow Summary

```
Reddit / YoloStocks data
        ↓
  LAYER 1: Sentiment Score (0–100) + Flag
        ↓
  LAYER 2: Technical Score (0–100) + Status  ← price/volume/MA data
        ↓
  LAYER 3: LLM Synthesis Agent
    - Checks signal agreement
    - Weighs risk and context
    - Matches to portfolio state
    - Generates conviction score + rationale
        ↓
  Trade Decision → Execute / Watch / Skip
        ↓
  LAYER 4: Log prediction + context
        ↓
  Actual Outcome Recorded
        ↓
  Weight Recalibration ← feeds back into Layers 1, 2, 3
```

---

## Build Order (Patient Version)

1. **Now:** Let Phase 5 run. Accumulate live paper trade data. Do not add features.
2. **After 4–8 weeks of live data:** Evaluate whether sentiment signal alpha is holding.
3. **Then:** Design and backtest Layer 2 technical score. Run alongside sentiment,
   compare combined vs solo performance on paper trades.
4. **Then:** Begin designing Layer 3 LLM synthesis. Start with rules-based version
   before introducing full LLM reasoning.
5. **Then:** Build Layer 4 formal feedback and recalibration architecture.
6. **Later:** Introduce options, crypto, and real money once system confidence is earned.

---

*The goal is a system that is right more often over time — not one that is complex.*
*Complexity earns its place by improving outcomes, not by existing.*
