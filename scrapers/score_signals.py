#!/usr/bin/env python3
"""
Phase 3: Signal Scorer  (v6 — subreddit diversity bonus)
Computes composite signal scores (0-100) for WSB ticker-day pairs and runs
predictive analysis against forward returns.

v1→v2: momentum-first weights; alpha +2.43% 7d.
v2→v3: thesis=-0.5, shaped velocity; alpha +2.92% 7d on 20k-row dataset.
v3→v4: Pearson r(thesis, ret) weakened to -0.018 on fuller data → thesis
        weight eased from -0.5 to -0.2.  Added slow_burn flag (velocity<0.5)
        as a stored label capturing the slow-burn 30d return pattern.
v4→v5: subreddit quality multiplier; mid-mix (0.7–0.9) outperformed pure WSB.
v5→v6: cross-community data shows mid-mix +3.01% 7d vs +0.74% pure WSB.
        Replace subreddit multiplier with an additive diversity bonus that
        rewards consensus across communities rather than penalising non-WSB.

Score components (weights):
  - Mention velocity     40%  (shaped: peak 3–5×, penalty >5×)
  - Hype/momentum mix   25%  (hype/options_yolo +, thesis -0.2 penalty)
  - Mention count        20%  (log-scale, percentile-ranked)
  - Avg post score       15%  (upvotes, percentile-ranked)
  + Subreddit diversity   additive bonus: 1=+0, 2=+5, 3=+10, 4+=+15 pts

Additional stored flags:
  - slow_burn            1 if velocity_ratio < 0.5 (below-avg, strong 30d pattern)
  - sub_diversity        count of unique subreddits mentioning ticker that day
"""

import bisect
import math
import os
import logging
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta

ROOT      = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH   = os.path.join(ROOT, 'data', 'wsb.db')
REPORT_PATH = os.path.join(ROOT, 'logs', 'signal_analysis.txt')

os.makedirs(os.path.dirname(REPORT_PATH), exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[logging.StreamHandler()],
)
log = logging.getLogger(__name__)

WEIGHTS = {
    'velocity':  0.40,
    'hype_mix':  0.25,
    'mention':   0.20,
    'upvote':    0.15,
}

# v4: thesis r weakened to -0.018 on expanded dataset → eased to -0.2.
HYPE_MIX_WEIGHTS = {
    'hype':          3.0,
    'options_yolo':  2.5,
    'meme':          1.0,
    'news_reaction': 0.5,
    'loss_porn':     0.25,
    'thesis':       -0.2,  # mild penalty; near-neutral on full dataset
    'other':         0.1,
}
SLOW_BURN_THRESHOLD = 0.5  # velocity_ratio below this → slow_burn flag
MAX_HYPE_WEIGHT  = 3.0   # normalise against all-hype ceiling
MAX_VELOCITY_CAP = 10.0  # cap raw ratio before scoring

# v6: subreddit diversity bonus (additive points added to composite score).
# Cross-community consensus outperforms single-source signals.
# 1 subreddit: baseline (no bonus)
# 2 subreddits: +5 pts
# 3 subreddits: +10 pts
# 4+ subreddits: +15 pts
_SUB_DIVERSITY_BONUS = {1: 0, 2: 5, 3: 10}
_SUB_DIVERSITY_BONUS_MAX = 15  # 4+ subs


def create_signals_table(conn: sqlite3.Connection):
    conn.execute("DROP TABLE IF EXISTS signals")
    conn.execute("""
        CREATE TABLE signals (
            ticker             TEXT NOT NULL,
            date               TEXT NOT NULL,
            signal_score       REAL,
            mention_count      INTEGER,
            thesis_count       INTEGER,
            hype_count         INTEGER,
            avg_post_score     REAL,
            unique_authors     INTEGER,
            velocity_ratio     REAL,
            slow_burn          INTEGER,
            sub_diversity      INTEGER,
            forward_return_7d  REAL,
            forward_return_30d REAL,
            PRIMARY KEY (ticker, date)
        )
    """)
    conn.commit()


def load_ticker_day_data(conn: sqlite3.Connection) -> list[dict]:
    """Aggregate classified posts into (ticker, date) rows with forward returns.

    Only includes posts within the actual price data window; pre-window posts
    produce false 0.0 returns because calc_returns.py snaps to the earliest
    available price for both the base and the forward date.
    """
    price_start = conn.execute("SELECT MIN(date) FROM prices").fetchone()[0]
    log.info(f"  Price data starts {price_start} — filtering posts to this window")

    rows = conn.execute("""
        SELECT
            pt.ticker,
            date(p.created_utc, 'unixepoch')                                    AS day,
            COUNT(*)                                                              AS mention_count,
            COUNT(DISTINCT CASE WHEN p.author NOT IN ('[deleted]','AutoModerator')
                                 AND p.author IS NOT NULL
                                THEN p.author END)                               AS unique_authors,
            AVG(p.score)                                                          AS avg_post_score,
            SUM(CASE WHEN p.classification = 'thesis'        THEN 1 ELSE 0 END)  AS thesis_count,
            SUM(CASE WHEN p.classification = 'options_yolo'  THEN 1 ELSE 0 END)  AS options_yolo_count,
            SUM(CASE WHEN p.classification = 'news_reaction' THEN 1 ELSE 0 END)  AS news_reaction_count,
            SUM(CASE WHEN p.classification = 'hype'          THEN 1 ELSE 0 END)  AS hype_count,
            SUM(CASE WHEN p.classification = 'loss_porn'     THEN 1 ELSE 0 END)  AS loss_porn_count,
            SUM(CASE WHEN p.classification = 'meme'          THEN 1 ELSE 0 END)  AS meme_count,
            SUM(CASE WHEN p.classification = 'other'         THEN 1 ELSE 0 END)  AS other_count,
            AVG(pt.forward_return_7d)                                             AS forward_return_7d,
            AVG(pt.forward_return_30d)                                            AS forward_return_30d,
            COUNT(DISTINCT p.subreddit)                                           AS sub_diversity
        FROM post_tickers pt
        JOIN posts p ON p.post_id = pt.post_id
        WHERE p.classification IS NOT NULL
          AND pt.forward_return_7d IS NOT NULL
          AND pt.forward_return_7d != 0.0
          AND date(p.created_utc, 'unixepoch') >= ?
        GROUP BY pt.ticker, day
        ORDER BY pt.ticker, day
    """, (price_start,)).fetchall()

    cols = [
        'ticker', 'date', 'mention_count', 'unique_authors', 'avg_post_score',
        'thesis_count', 'options_yolo_count', 'news_reaction_count', 'hype_count',
        'loss_porn_count', 'meme_count', 'other_count',
        'forward_return_7d', 'forward_return_30d', 'sub_diversity',
    ]
    return [dict(zip(cols, r)) for r in rows]


def compute_velocity(rows: list[dict]) -> dict[tuple, float]:
    """
    For each (ticker, date), compute velocity_ratio = today_count / 30d_rolling_avg.
    Returns dict keyed by (ticker, date).
    """
    by_ticker: dict[str, list[tuple]] = defaultdict(list)
    for r in rows:
        by_ticker[r['ticker']].append((r['date'], r['mention_count']))

    velocity: dict[tuple, float] = {}
    for ticker, days in by_ticker.items():
        days.sort()
        for i, (date, count) in enumerate(days):
            cutoff = (
                datetime.strptime(date, '%Y-%m-%d') - timedelta(days=30)
            ).strftime('%Y-%m-%d')
            prior = [c for d, c in days[:i] if d >= cutoff]
            avg = sum(prior) / len(prior) if prior else count
            ratio = (count / avg) if avg > 0 else 1.0
            velocity[(ticker, date)] = min(ratio, MAX_VELOCITY_CAP)
    return velocity


def pct_rank(values: list[float]) -> list[float]:
    """Map each value to its percentile rank in [0, 100]."""
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    if n <= 1:
        return [50.0] * n
    return [
        bisect.bisect_left(sorted_vals, v) / (n - 1) * 100
        for v in values
    ]


def _velocity_score(ratio: float) -> float:
    """Shaped velocity score (0-100) that peaks at 3-5× and penalises >5×.

    Piecewise linear:
      0–0.5×  →  0–20   (well below avg, low signal)
      0.5–3×  →  20–80  (building momentum)
      3–5×    →  80–100 (sweet spot)
      >5×     →  100 – 12*(ratio-5), floor 20  (reversal risk)
    """
    if ratio <= 0.5:
        return ratio / 0.5 * 20
    elif ratio <= 3.0:
        return 20.0 + (ratio - 0.5) / 2.5 * 60.0
    elif ratio <= 5.0:
        return 80.0 + (ratio - 3.0) / 2.0 * 20.0
    else:
        return max(100.0 - (ratio - 5.0) * 12.0, 20.0)


def score_signals(rows: list[dict], velocity: dict[tuple, float]) -> list[dict]:
    mention_pct = pct_rank([math.log1p(r['mention_count']) for r in rows])
    upvote_pct  = pct_rank([r['avg_post_score'] or 0.0 for r in rows])
    vel_raw     = [velocity.get((r['ticker'], r['date']), 1.0) for r in rows]

    scored = []
    for i, r in enumerate(rows):
        total = r['mention_count'] or 1

        raw_hype = (
            r['hype_count']          * HYPE_MIX_WEIGHTS['hype']          +
            r['options_yolo_count']  * HYPE_MIX_WEIGHTS['options_yolo']  +
            r['meme_count']          * HYPE_MIX_WEIGHTS['meme']          +
            r['news_reaction_count'] * HYPE_MIX_WEIGHTS['news_reaction'] +
            r['loss_porn_count']     * HYPE_MIX_WEIGHTS['loss_porn']     +
            r['thesis_count']        * HYPE_MIX_WEIGHTS['thesis']        +  # -0.5 penalty
            r['other_count']         * HYPE_MIX_WEIGHTS['other']
        ) / total
        hype_mix_score = (raw_hype / MAX_HYPE_WEIGHT) * 100

        sub_diversity = r.get('sub_diversity') or 1
        diversity_bonus = _SUB_DIVERSITY_BONUS.get(sub_diversity, _SUB_DIVERSITY_BONUS_MAX)
        composite = (
            WEIGHTS['velocity']  * _velocity_score(vel_raw[i]) +
            WEIGHTS['hype_mix']  * hype_mix_score               +
            WEIGHTS['mention']   * mention_pct[i]               +
            WEIGHTS['upvote']    * upvote_pct[i]
        ) + diversity_bonus  # v6: additive cross-community consensus bonus

        scored.append({
            **r,
            'signal_score':   round(min(max(composite, 0.0), 100.0), 2),
            'velocity_ratio': round(vel_raw[i], 4),
            'slow_burn':      1 if vel_raw[i] < SLOW_BURN_THRESHOLD else 0,
            'sub_diversity':  sub_diversity,
        })
    return scored


def store_signals(conn: sqlite3.Connection, scored: list[dict]):
    conn.executemany(
        """INSERT OR REPLACE INTO signals
           (ticker, date, signal_score, mention_count, thesis_count, hype_count,
            avg_post_score, unique_authors, velocity_ratio, slow_burn, sub_diversity,
            forward_return_7d, forward_return_30d)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            (
                r['ticker'], r['date'], r['signal_score'],
                r['mention_count'], r['thesis_count'], r['hype_count'],
                r['avg_post_score'], r['unique_authors'], r['velocity_ratio'],
                r['slow_burn'], r['sub_diversity'],
                r['forward_return_7d'], r['forward_return_30d'],
            )
            for r in scored
        ],
    )
    conn.commit()


def _pearson(xs: list[float], ys: list[float]) -> float:
    """Pearson correlation coefficient between two equal-length lists."""
    n = len(xs)
    if n < 2:
        return float('nan')
    mx = sum(xs) / n
    my = sum(ys) / n
    num   = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    denom = (sum((x - mx) ** 2 for x in xs) * sum((y - my) ** 2 for y in ys)) ** 0.5
    return num / denom if denom else float('nan')


def analyze_signals(conn: sqlite3.Connection) -> str:
    lines = []
    sep = "=" * 62

    lines += [
        sep,
        "WSB Signal Lab — Phase 3: Signal Analysis  (v6)",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        sep,
        "",
        "Scorer evolution:",
        "  v1  thesis-quality first      alpha 7d = -0.07%   30d = -2.99%",
        "  v2  momentum-first, thesis=0  alpha 7d = +2.43%   30d = +1.59%",
        "  v3  thesis=-0.5, vel ceiling  alpha 7d = +2.92%   30d = +5.11%",
        "  v4  thesis=-0.2, slow_burn flag  alpha 7d = +2.76%   30d = +5.29%",
        "  v5  subreddit weight multiplier  alpha 7d = +3.01%   30d = +8.33%",
        "  v6  subreddit diversity bonus  (this run)",
        "",
        "v6 changes vs v5:",
        "  Replaced subreddit quality multiplier with an additive diversity bonus.",
        "  v5 revealed mid-mix (0.7-0.9 avg weight) outperforms pure WSB:",
        "    mid-mix +3.01% 7d / +8.33% 30d  vs  pure WSB +0.74% 7d",
        "  v6 rewards cross-community consensus directly:",
        "    1 subreddit: +0 pts  2 subreddits: +5 pts",
        "    3 subreddits: +10 pts  4+ subreddits: +15 pts",
    ]

    # Dataset summary
    r = conn.execute("""
        SELECT COUNT(*), MIN(date), MAX(date),
               AVG(signal_score), MIN(signal_score), MAX(signal_score)
        FROM signals
    """).fetchone()
    price_range = conn.execute("SELECT MIN(date), MAX(date) FROM prices").fetchone()
    total_rows = r[0]
    lines += [
        f"\nDataset: {total_rows:,} ticker-day rows  |  post window: {r[1]} to {r[2]}",
        f"Price data: {price_range[0]} to {price_range[1]}  (posts filtered to this window)",
        f"Signal scores — min: {r[4]:.1f}  avg: {r[3]:.1f}  max: {r[5]:.1f}",
    ]

    # Score distribution
    lines.append("\nScore distribution (v6):")
    for lo, hi in [(0, 20), (20, 40), (40, 60), (60, 80), (80, 100)]:
        n = conn.execute(
            "SELECT COUNT(*) FROM signals WHERE signal_score >= ? AND signal_score < ?",
            (lo, hi if hi < 100 else 101),
        ).fetchone()[0]
        bar = "#" * (n * 40 // (total_rows or 1))
        lines.append(f"  {lo:3d}-{hi:3d}: {n:6,}  {bar}")

    # ── 1. Signal score buckets vs forward returns ────────────────────────────
    lines += ["", "-" * 62,
              "[1] Signal Score Buckets vs Forward Returns  (v6)", "-" * 62,
              f"  {'Bucket':<22} {'N':>6}  {'Avg 7d':>8}  {'Avg 30d':>8}  {'Win%':>6}",
              f"  {'v1:-0.07%α v2:+2.43%α v3:+2.92%α v4:+2.76%α v5:+3.01%α (see [4])':<58}"]
    for label, cond in [
        ("High  (>70)",    "signal_score > 70"),
        ("Medium (40–70)", "signal_score BETWEEN 40 AND 70"),
        ("Low   (<40)",    "signal_score < 40"),
    ]:
        r = conn.execute(f"""
            SELECT COUNT(*),
                   AVG(forward_return_7d)  * 100,
                   AVG(forward_return_30d) * 100,
                   SUM(CASE WHEN forward_return_7d > 0 THEN 1 ELSE 0 END) * 100.0 / COUNT(*)
            FROM signals WHERE {cond} AND forward_return_7d IS NOT NULL
        """).fetchone()
        lines.append(
            f"  {label:<22} {r[0]:>6,}  {r[1]:>+7.2f}%  {r[2]:>+7.2f}%  {r[3]:>5.1f}%"
        )

    # ── 2. Composition mix vs returns ─────────────────────────────────────────
    lines += ["", "-" * 62,
              "[2] Composition Mix vs Forward Returns", "-" * 62,
              f"  {'Mix type':<40} {'N':>6}  {'Avg 7d':>8}  {'Avg 30d':>8}  {'Win%':>6}"]
    for label, cond in [
        ("Hype-heavy    (>50% hype)",     "hype_count * 1.0 / mention_count > 0.5"),
        ("Thesis-heavy  (>50% thesis)",   "thesis_count * 1.0 / mention_count > 0.5"),
        ("Mixed / Other",                 "thesis_count * 1.0 / mention_count <= 0.5 "
                                          "AND hype_count * 1.0 / mention_count <= 0.5"),
    ]:
        r = conn.execute(f"""
            SELECT COUNT(*),
                   AVG(forward_return_7d)  * 100,
                   AVG(forward_return_30d) * 100,
                   SUM(CASE WHEN forward_return_7d > 0 THEN 1 ELSE 0 END) * 100.0 / COUNT(*)
            FROM signals WHERE {cond} AND forward_return_7d IS NOT NULL
        """).fetchone()
        lines.append(
            f"  {label:<40} {r[0]:>6,}  {r[1]:>+7.2f}%  {r[2]:>+7.2f}%  {r[3]:>5.1f}%"
        )

    # ── 3. Thesis as negative signal test ─────────────────────────────────────
    lines += ["", "-" * 62,
              "[3] Is Thesis a Negative Signal? (Thesis Fraction Bins)", "-" * 62,
              "  thesis_fraction = thesis_count / mention_count",
              f"  {'Thesis fraction':<25} {'N':>6}  {'Avg 7d':>8}  {'Avg 30d':>8}  {'Win%':>6}"]
    for label, lo, hi in [
        ("0%   (no thesis)",    0.0,  0.001),
        ("1–25%",               0.001, 0.25),
        ("25–50%",              0.25,  0.50),
        ("50–75%",              0.50,  0.75),
        ("75–100% (all thesis)", 0.75, 1.01),
    ]:
        r = conn.execute("""
            SELECT COUNT(*),
                   AVG(forward_return_7d)  * 100,
                   AVG(forward_return_30d) * 100,
                   SUM(CASE WHEN forward_return_7d > 0 THEN 1 ELSE 0 END) * 100.0 / COUNT(*)
            FROM signals
            WHERE thesis_count * 1.0 / mention_count >= ?
              AND thesis_count * 1.0 / mention_count <  ?
              AND forward_return_7d IS NOT NULL
        """, (lo, hi)).fetchone()
        lines.append(
            f"  {label:<25} {r[0]:>6,}  {r[1]:>+7.2f}%  {r[2]:>+7.2f}%  {r[3]:>5.1f}%"
        )

    # Pearson correlation: thesis fraction vs 7d return
    pairs = conn.execute("""
        SELECT thesis_count * 1.0 / mention_count, forward_return_7d
        FROM signals WHERE forward_return_7d IS NOT NULL
    """).fetchall()
    tf  = [p[0] for p in pairs]
    ret = [p[1] for p in pairs]
    corr = _pearson(tf, ret)
    corr_label = "negative (thesis → lower returns)" if corr < 0 else "positive"
    lines += [
        f"",
        f"  Pearson r (thesis_fraction vs 7d_return): {corr:+.4f}  [{corr_label}]",
        f"  Verdict: thesis weight should be {'NEGATIVE' if corr < -0.02 else 'ZERO (neutral)'}",
    ]

    # ── 4. Baseline vs signal alpha — v1/v2/v3/v4/v5/v6 comparison ──────────
    lines += ["", "-" * 62,
              "[4] Baseline vs Signal Performance  (v1 / v2 / v3 / v4 / v5 / v6)", "-" * 62]
    base = conn.execute(
        "SELECT AVG(forward_return_7d)*100, AVG(forward_return_30d)*100 "
        "FROM signals WHERE forward_return_7d IS NOT NULL"
    ).fetchone()
    high = conn.execute(
        "SELECT AVG(forward_return_7d)*100, AVG(forward_return_30d)*100 "
        "FROM signals WHERE signal_score > 70 AND forward_return_7d IS NOT NULL"
    ).fetchone()
    v6_alpha_7d  = high[0] - base[0]
    v6_alpha_30d = high[1] - base[1]
    lines += [
        f"  Baseline (all rows)               : 7d = {base[0]:+.2f}%   30d = {base[1]:+.2f}%",
        f"",
        f"  {'Ver':<4}  {'Weight design':<42}  {'Alpha 7d':>8}  {'Alpha 30d':>9}",
        f"  {'---':<4}  {'-'*42}  {'--------':>8}  {'---------':>9}",
        f"  {'v1':<4}  {'thesis-first, diversity 15%':<42}  {'-0.07%':>8}  {'-2.99%':>9}",
        f"  {'v2':<4}  {'momentum-first, thesis=0':<42}  {'+2.43%':>8}  {'+1.59%':>9}",
        f"  {'v3':<4}  {'thesis=-0.5, vel ceiling':<42}  {'+2.92%':>8}  {'+5.11%':>9}",
        f"  {'v4':<4}  {'thesis=-0.2, slow_burn flag':<42}  {'+2.76%':>8}  {'+5.29%':>9}",
        f"  {'v5':<4}  {'+ subreddit weight multiplier':<42}  {'+3.01%':>8}  {'+8.33%':>9}",
        f"  {'v6':<4}  {'+ subreddit diversity bonus (this run)':<42}  {v6_alpha_7d:>+7.2f}%  {v6_alpha_30d:>+8.2f}%",
        f"",
        f"  v6 high-signal (>70)              : 7d = {high[0]:+.2f}%   30d = {high[1]:+.2f}%",
    ]

    # ── 5. Velocity detail ────────────────────────────────────────────────────
    lines += ["", "-" * 62,
              "[5] Mention Velocity vs 7d Return  (40% of score, shaped)", "-" * 62,
              f"  {'Velocity bucket':<30} {'N':>6}  {'Avg 7d':>8}  {'Avg 30d':>8}  {'Win%':>6}"]
    for label, cond in [
        ("Extreme  (>5× avg)",    "velocity_ratio > 5"),
        ("High     (3–5×)",       "velocity_ratio BETWEEN 3 AND 5"),
        ("Normal   (0.5–3×)",     "velocity_ratio BETWEEN 0.5 AND 3"),
        ("Below avg (<0.5×)",     "velocity_ratio < 0.5"),
    ]:
        r = conn.execute(f"""
            SELECT COUNT(*),
                   AVG(forward_return_7d)  * 100,
                   AVG(forward_return_30d) * 100,
                   SUM(CASE WHEN forward_return_7d > 0 THEN 1 ELSE 0 END) * 100.0 / COUNT(*)
            FROM signals WHERE {cond} AND forward_return_7d IS NOT NULL
        """).fetchone()
        lines.append(
            f"  {label:<30} {r[0]:>6,}  {r[1]:>+7.2f}%  {r[2]:>+7.2f}%  {r[3]:>5.1f}%"
        )

    # ── 6. Top 20 highest v6 signal days ──────────────────────────────────────
    lines += ["", "-" * 62,
              "[6] Top 20 Highest Signal Score Days  (v6, all years)", "-" * 62,
              f"  {'Ticker':<8} {'Date':<12} {'Score':>6} {'Mentions':>8} "
              f"{'Vel':>5} {'SB':>3} {'7d Ret':>8} {'30d Ret':>8}",
              "  " + "-" * 64]
    for row in conn.execute("""
        SELECT ticker, date, signal_score, mention_count, velocity_ratio,
               slow_burn, forward_return_7d * 100, forward_return_30d * 100
        FROM signals
        WHERE forward_return_7d IS NOT NULL
        ORDER BY signal_score DESC LIMIT 20
    """):
        r7  = f"{row[6]:+.1f}%" if row[6] is not None else "N/A"
        r30 = f"{row[7]:+.1f}%" if row[7] is not None else "N/A"
        sb  = "✓" if row[5] else " "
        lines.append(
            f"  {row[0]:<8} {row[1]:<12} {row[2]:>6.1f} {row[3]:>8,} "
            f"{row[4]:>4.1f}x {sb:>3} {r7:>8} {r30:>8}"
        )

    # ── 7. High-velocity + high-hype intersection ─────────────────────────────
    lines += ["", "-" * 62,
              "[7] High-Velocity AND Hype-Heavy Days  (best combined signal)", "-" * 62]
    for label, cond in [
        ("High vel + hype-heavy",
         "velocity_ratio > 3 AND hype_count * 1.0 / mention_count > 0.3"),
        ("High vel + thesis-heavy",
         "velocity_ratio > 3 AND thesis_count * 1.0 / mention_count > 0.3"),
        ("High vel only (any mix)",
         "velocity_ratio > 3"),
        ("Hype-heavy only (any vel)",
         "hype_count * 1.0 / mention_count > 0.3"),
    ]:
        r = conn.execute(f"""
            SELECT COUNT(*),
                   AVG(forward_return_7d)  * 100,
                   AVG(forward_return_30d) * 100,
                   SUM(CASE WHEN forward_return_7d > 0 THEN 1 ELSE 0 END) * 100.0 / COUNT(*)
            FROM signals WHERE {cond} AND forward_return_7d IS NOT NULL
        """).fetchone()
        lines += [
            f"  {label}",
            f"    n={r[0]:,}  avg_7d={r[1]:+.2f}%  avg_30d={r[2]:+.2f}%  win%={r[3]:.1f}%",
        ]

    # ── 8. Slow-burn signal analysis ──────────────────────────────────────────
    sb_n = conn.execute(
        "SELECT COUNT(*) FROM signals WHERE slow_burn = 1"
    ).fetchone()[0]
    lines += ["", "-" * 62,
              f"[8] Slow-Burn Flag Analysis  (velocity < 0.5×, n={sb_n:,})", "-" * 62,
              "  slow_burn=1 means fewer mentions today than the ticker's 30d avg.",
              "  Strong 30d signal observed in v3 data (+7.29%); testing on full set.",
              f"  {'Group':<28} {'N':>6}  {'Avg 7d':>8}  {'Avg 30d':>8}  {'Win%':>6}"]
    for label, cond in [
        ("slow_burn=1 (below avg vel)",  "slow_burn = 1"),
        ("slow_burn=0 (normal/high vel)", "slow_burn = 0"),
    ]:
        r = conn.execute(f"""
            SELECT COUNT(*),
                   AVG(forward_return_7d)  * 100,
                   AVG(forward_return_30d) * 100,
                   SUM(CASE WHEN forward_return_7d > 0 THEN 1 ELSE 0 END) * 100.0 / COUNT(*)
            FROM signals WHERE {cond} AND forward_return_7d IS NOT NULL
        """).fetchone()
        lines.append(
            f"  {label:<28} {r[0]:>6,}  {r[1]:>+7.2f}%  {r[2]:>+7.2f}%  {r[3]:>5.1f}%"
        )

    # Slow-burn broken down by hype vs thesis mix
    lines += ["", "  Slow-burn breakdown by composition:"]
    for label, cond in [
        ("  slow_burn + hype-heavy",   "slow_burn=1 AND hype_count*1.0/mention_count > 0.3"),
        ("  slow_burn + thesis-heavy", "slow_burn=1 AND thesis_count*1.0/mention_count > 0.5"),
        ("  slow_burn + high score",   "slow_burn=1 AND signal_score > 50"),
    ]:
        r = conn.execute(f"""
            SELECT COUNT(*),
                   AVG(forward_return_7d)  * 100,
                   AVG(forward_return_30d) * 100,
                   SUM(CASE WHEN forward_return_7d > 0 THEN 1 ELSE 0 END) * 100.0 / COUNT(*)
            FROM signals WHERE {cond} AND forward_return_7d IS NOT NULL
        """).fetchone()
        if r[0]:
            lines.append(
                f"  {label:<28} n={r[0]:,}  7d={r[1]:+.2f}%  30d={r[2]:+.2f}%  win={r[3]:.1f}%"
            )

    # ── 9. Subreddit diversity bonus breakdown (v6) ───────────────────────────
    lines += ["", "-" * 62,
              "[9] Subreddit Diversity vs Forward Returns  (v6 new)", "-" * 62,
              "  sub_diversity = unique subreddits mentioning ticker on that day.",
              "  Bonus: 1=+0pts  2=+5pts  3=+10pts  4+=+15pts",
              f"  {'Bucket':<30} {'N':>6}  {'Avg 7d':>8}  {'Avg 30d':>8}  {'Win%':>6}"]
    for label, cond in [
        ("1 sub  (no bonus, +0)",    "sub_diversity = 1"),
        ("2 subs (+5 pts)",          "sub_diversity = 2"),
        ("3 subs (+10 pts)",         "sub_diversity = 3"),
        ("4+ subs (+15 pts)",        "sub_diversity >= 4"),
    ]:
        r = conn.execute(f"""
            SELECT COUNT(*),
                   AVG(forward_return_7d)  * 100,
                   AVG(forward_return_30d) * 100,
                   SUM(CASE WHEN forward_return_7d > 0 THEN 1 ELSE 0 END) * 100.0 / COUNT(*)
            FROM signals
            WHERE {cond} AND forward_return_7d IS NOT NULL
        """).fetchone()
        if r[0]:
            lines.append(
                f"  {label:<30} {r[0]:>6,}  {r[1]:>+7.2f}%  {r[2]:>+7.2f}%  {r[3]:>5.1f}%"
            )

    # Distribution of sub_diversity in dataset
    lines += ["", "  sub_diversity distribution across scored ticker-days:"]
    div_rows = conn.execute(
        "SELECT AVG(sub_diversity), MIN(sub_diversity), MAX(sub_diversity) FROM signals"
    ).fetchone()
    lines.append(
        f"    mean={div_rows[0]:.2f}  min={div_rows[1]}  max={div_rows[2]}"
    )

    lines.append("\n" + sep)
    return "\n".join(lines)


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.execute('PRAGMA journal_mode=WAL')

    log.info("Creating signals table...")
    create_signals_table(conn)

    log.info("Loading ticker-day aggregations from classified posts...")
    rows = load_ticker_day_data(conn)
    log.info(f"  {len(rows):,} ticker-day rows loaded")

    log.info("Computing mention velocity (30d rolling avg per ticker)...")
    velocity = compute_velocity(rows)

    log.info("Scoring signals...")
    scored = score_signals(rows, velocity)
    scores = [s['signal_score'] for s in scored]
    log.info(f"  min={min(scores):.1f}  avg={sum(scores)/len(scores):.1f}  max={max(scores):.1f}")

    log.info("Storing to signals table...")
    store_signals(conn, scored)
    log.info(f"  {len(scored):,} rows written")

    log.info("Running analysis...")
    report = analyze_signals(conn)

    with open(REPORT_PATH, 'w') as f:
        f.write(report)

    print(report)
    log.info(f"Report written to {REPORT_PATH}")

    conn.close()


if __name__ == '__main__':
    main()
