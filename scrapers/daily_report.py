#!/usr/bin/env python3
"""
Daily Signal Report — WSB Signal Lab

Reads today's mention counts from daily_mentions, computes a live signal score,
and prints a watchlist report formatted for Pi morning output.

Live signal score uses three components (post-classification data unavailable):
  - Velocity       50%  (shaped piecewise, same curve as historical scorer)
  - Subreddit hype 30%  (WSB/options/pennystocks = higher hype weight)
  - Mention rank   20%  (percentile of log-mention across today's tickers)

Velocity baseline: 30-day rolling window in daily_mentions.  When the calendar
window is empty (e.g. data gap), falls back to the most-recent 30 data points
found for each ticker and flags the report accordingly.

Usage:
    python scrapers/daily_report.py              # today, read-only
    python scrapers/daily_report.py --fetch      # fetch fresh data first
    python scrapers/daily_report.py --date 2026-05-29
    python scrapers/daily_report.py --top 20
"""

import argparse
import bisect
import math
import os
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timedelta

ROOT     = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH  = os.path.join(ROOT, 'data', 'wsb.db')
LOG_DIR  = os.path.join(ROOT, 'logs')

os.makedirs(LOG_DIR, exist_ok=True)

MAX_VELOCITY_CAP = 10.0

# Subreddit → hype weight (proxy for post-classification hype_mix score)
SUBREDDIT_HYPE = {
    'wallstreetbets': 1.00,
    'options':        0.90,
    'pennystocks':    0.80,
    'shortsqueeze':   0.85,
    'stocks':         0.40,
    'investing':      0.20,
}

# Short squeeze watch threshold
SQUEEZE_DTC_MIN = 5.0   # days-to-cover above this triggers SQUEEZE_WATCH flag

# Velocity tag thresholds (from Phase 3 findings)
#   >5×  → EXTREME  (historical avg 7d: -3.41%, reversal risk)
#   3-5× → HOT      (historical avg 7d: +1.79%, sweet spot)
#   1.5× → RISING
#   0.5× → NORMAL
#   <0.5 → SLOW_BURN (historical avg 30d: +7.29%)

LIVE_WEIGHTS = {
    'velocity':  0.50,
    'sub_hype':  0.30,
    'mention':   0.20,
}


# ── Scoring helpers ──────────────────────────────────────────────────────────

def _velocity_score(ratio: float) -> float:
    """Shaped velocity score 0-100 (identical curve to historical scorer v4)."""
    if ratio <= 0.5:
        return ratio / 0.5 * 20
    elif ratio <= 3.0:
        return 20.0 + (ratio - 0.5) / 2.5 * 60.0
    elif ratio <= 5.0:
        return 80.0 + (ratio - 3.0) / 2.0 * 20.0
    else:
        return max(100.0 - (ratio - 5.0) * 12.0, 20.0)


def _velocity_tag(ratio: float) -> str:
    if ratio > 5.0:
        return 'EXTREME'
    elif ratio >= 3.0:
        return 'HOT'
    elif ratio >= 1.5:
        return 'RISING'
    elif ratio >= 0.5:
        return 'NORMAL'
    else:
        return 'SLOW_BURN'


def _pct_rank(values: list[float]) -> list[float]:
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    if n <= 1:
        return [50.0] * n
    return [bisect.bisect_left(sorted_vals, v) / (n - 1) * 100 for v in values]


# ── Options activity helpers ──────────────────────────────────────────────────

def load_options_active(conn: sqlite3.Connection, tickers: list[str]) -> set[str]:
    """
    Return tickers that appeared in options_yolo classified posts during the
    last 7 days of the historical dataset.  Uses MAX(created_utc) dynamically
    so it stays correct if more data is ever imported.
    """
    if not tickers:
        return set()
    max_utc = conn.execute(
        "SELECT MAX(created_utc) FROM posts WHERE classification IS NOT NULL"
    ).fetchone()[0]
    if not max_utc:
        return set()
    cutoff = max_utc - 7 * 86400
    placeholders = ','.join('?' * len(tickers))
    rows = conn.execute(
        f"""SELECT DISTINCT pt.ticker
              FROM post_tickers pt
              JOIN posts p ON p.post_id = pt.post_id
             WHERE p.classification = 'options_yolo'
               AND p.created_utc >= ?
               AND pt.ticker IN ({placeholders})""",
        (cutoff, *tickers),
    ).fetchall()
    return {r[0] for r in rows}


# ── Short interest helpers ────────────────────────────────────────────────────

def load_short_interest(conn: sqlite3.Connection, tickers: list[str]) -> dict:
    """
    Return most-recent short interest record per ticker.
    {ticker: {short_interest, days_to_cover, float_percent}}
    """
    if not tickers:
        return {}
    placeholders = ','.join('?' * len(tickers))
    rows = conn.execute(
        f"""SELECT ticker, short_interest, days_to_cover, float_percent
              FROM short_interest
             WHERE ticker IN ({placeholders})
             ORDER BY report_date DESC""",
        tickers,
    ).fetchall()
    result: dict = {}
    for ticker, si, dtc, fp in rows:
        if ticker not in result:
            result[ticker] = {'short_interest': si, 'days_to_cover': dtc, 'float_percent': fp}
    return result


# ── Data loading ─────────────────────────────────────────────────────────────

def load_today(conn: sqlite3.Connection, date: str) -> dict:
    """Return {ticker: {total, by_subreddit}} for the given date."""
    rows = conn.execute(
        "SELECT ticker, subreddit, mention_count FROM daily_mentions WHERE date = ?",
        (date,),
    ).fetchall()
    tickers: dict[str, dict] = {}
    for ticker, sub, count in rows:
        if ticker not in tickers:
            tickers[ticker] = {'total': 0, 'by_sub': {}}
        tickers[ticker]['total'] += count
        tickers[ticker]['by_sub'][sub] = tickers[ticker]['by_sub'].get(sub, 0) + count
    return tickers


def compute_velocity(
    conn: sqlite3.Connection, date: str, ticker_data: dict
) -> tuple[dict, str]:
    """
    Compute velocity_ratio per ticker.  Returns (ratios_dict, baseline_note).

    Tries 30-day calendar window first; falls back to most-recent 30 data points
    when the window is empty (data gap).
    """
    cutoff = (datetime.strptime(date, '%Y-%m-%d') - timedelta(days=30)).strftime('%Y-%m-%d')

    # Check whether any prior data exists in the 30-day window
    any_recent = conn.execute(
        "SELECT 1 FROM daily_mentions WHERE date >= ? AND date < ? LIMIT 1",
        (cutoff, date),
    ).fetchone()

    use_fallback = any_recent is None
    ratios: dict[str, float] = {}

    for ticker, td in ticker_data.items():
        today_count = td['total']

        if not use_fallback:
            rows = conn.execute(
                """SELECT SUM(mention_count) FROM daily_mentions
                   WHERE ticker = ? AND date >= ? AND date < ?
                   GROUP BY date""",
                (ticker, cutoff, date),
            ).fetchall()
            prior = [r[0] for r in rows if r[0]]
        else:
            rows = conn.execute(
                """SELECT SUM(mention_count) FROM daily_mentions
                   WHERE ticker = ? AND date < ?
                   GROUP BY date ORDER BY date DESC LIMIT 30""",
                (ticker, date),
            ).fetchall()
            prior = [r[0] for r in rows if r[0]]

        avg = sum(prior) / len(prior) if prior else today_count
        ratios[ticker] = min(today_count / avg if avg > 0 else 1.0, MAX_VELOCITY_CAP)

    if use_fallback:
        # Find when the most recent prior data was
        last = conn.execute(
            "SELECT MAX(date) FROM daily_mentions WHERE date < ?", (date,)
        ).fetchone()[0]
        note = f"no data in 30d window — using archive baseline (last available: {last})"
    else:
        note = "30-day rolling window"

    return ratios, note


def compute_sub_hype_score(by_sub: dict) -> float:
    """Weighted subreddit mix → 0-100 hype proxy score."""
    total = sum(by_sub.values())
    if not total:
        return 0.0
    weighted = sum(
        count * SUBREDDIT_HYPE.get(sub, 0.3) for sub, count in by_sub.items()
    )
    return (weighted / total) * 100  # max weight is 1.0 → 100


# ── Report generation ─────────────────────────────────────────────────────────

def build_rows(
    ticker_data: dict,
    velocities: dict,
    si_data: dict | None = None,
    options_active: set | None = None,
) -> list[dict]:
    tickers = list(ticker_data.keys())
    mention_pct = _pct_rank([math.log1p(ticker_data[t]['total']) for t in tickers])
    si  = si_data or {}
    opt = options_active or set()

    rows = []
    for i, ticker in enumerate(tickers):
        td  = ticker_data[ticker]
        vel = velocities[ticker]
        sub_hype = compute_sub_hype_score(td['by_sub'])
        vel_s = _velocity_score(vel)
        live_score = (
            LIVE_WEIGHTS['velocity'] * vel_s
            + LIVE_WEIGHTS['sub_hype'] * sub_hype
            + LIVE_WEIGHTS['mention'] * mention_pct[i]
        )
        si_row = si.get(ticker, {})
        dtc    = si_row.get('days_to_cover')
        rows.append({
            'ticker':          ticker,
            'total':           td['total'],
            'by_sub':          td['by_sub'],
            'velocity':        round(vel, 2),
            'vel_tag':         _velocity_tag(vel),
            'sub_hype':        round(sub_hype, 1),
            'mention_pct':     round(mention_pct[i], 1),
            'live_score':      round(min(max(live_score, 0), 100), 1),
            'slow_burn':       vel < 0.5,
            'n_subs':          len(td['by_sub']),
            'days_to_cover':   dtc,
            'float_percent':   si_row.get('float_percent'),
            'squeeze_watch':   dtc is not None and dtc > SQUEEZE_DTC_MIN,
            'options_active':  ticker in opt,
        })

    rows.sort(key=lambda r: r['live_score'], reverse=True)
    return rows


def _sub_abbrev(sub: str) -> str:
    return {
        'wallstreetbets': 'WSB',
        'stocks':         'STK',
        'investing':      'INV',
        'pennystocks':    'PP',
        'options':        'OPT',
        'shortsqueeze':   'SS',
    }.get(sub, sub[:3].upper())


def _sub_list(by_sub: dict, max_show: int = 4) -> str:
    ordered = sorted(by_sub.items(), key=lambda x: -x[1])
    parts = [_sub_abbrev(s) for s, _ in ordered[:max_show]]
    if len(ordered) > max_show:
        parts.append('…')
    return ' '.join(parts)


def format_report(rows: list[dict], date: str, velocity_note: str, top: int) -> str:
    sep  = '═' * 68
    dash = '─' * 68
    dt   = datetime.strptime(date, '%Y-%m-%d')
    day_name = dt.strftime('%A')
    now  = datetime.now().strftime('%Y-%m-%d %H:%M')
    total_mentions = sum(r['total'] for r in rows)

    lines = [
        sep,
        f"Murmur  —  Daily Watchlist  {date} ({day_name})",
        f"Generated: {now}  |  Active tickers: {len(rows)}  |  Mentions: {total_mentions:,}",
        sep,
    ]

    # Velocity baseline note (only show if gap)
    if 'no data' in velocity_note:
        lines += [
            '',
            f'[!] Velocity baseline: {velocity_note}.',
            '    First week of daily fetches will build a proper live baseline.',
        ]

    # ── Top signals table ────────────────────────────────────────────────────
    top_rows = rows[:top]
    lines += [
        '',
        f'── TOP {top} SIGNALS ──────────────────────────────────────────────────────',
        f"  {'#':>2}  {'Ticker':<6}  {'Mentions':>8}  {'Vel':>5}  {'Score':>5}  {'Tag':<10}  {'Flags':<14}  Subs",
        '  ' + '─' * 78,
    ]
    for i, r in enumerate(top_rows, 1):
        tag = r['vel_tag']
        if r['slow_burn']:
            tag = 'SLOW_BURN'
        flags = ' '.join(f for f in [
            'OPTIONS_ACTIVE' if r['options_active'] else '',
            'SQUEEZE_WATCH'  if r['squeeze_watch']  else '',
        ] if f)
        lines.append(
            f"  {i:>2}  {r['ticker']:<6}  {r['total']:>8,}  "
            f"{r['velocity']:>4.1f}x  {r['live_score']:>5.1f}  "
            f"{tag:<10}  {flags:<14}  {_sub_list(r['by_sub'])}"
        )

    # ── Velocity alert sections ──────────────────────────────────────────────
    extreme   = [r for r in rows if r['velocity'] > 5.0]
    hot       = [r for r in rows if 3.0 <= r['velocity'] <= 5.0]
    slow_burn = [r for r in rows if r['slow_burn']]

    lines += ['', dash, '── VELOCITY ALERTS ─────────────────────────────────────────────────────', dash]

    lines.append('EXTREME (>5×) — CAUTION: historical avg 7d = -3.41% (reversal risk)')
    if extreme:
        for r in extreme:
            flag = '  [OPTIONS_ACTIVE]' if r['options_active'] else ''
            lines.append(
                f"  {r['ticker']:<6}  {r['total']:>7,} mentions  "
                f"{r['velocity']:.1f}x  [{_sub_list(r['by_sub'])}]{flag}"
            )
    else:
        lines.append('  (none)')

    lines.append('')
    lines.append('HOT (3-5×) — WATCH: historical avg 7d = +1.79% (sweet spot)')
    if hot:
        for r in hot:
            flag = '  [OPTIONS_ACTIVE]' if r['options_active'] else ''
            lines.append(
                f"  {r['ticker']:<6}  {r['total']:>7,} mentions  "
                f"{r['velocity']:.1f}x  [{_sub_list(r['by_sub'])}]{flag}"
            )
    else:
        lines.append('  (none)')

    lines.append('')
    lines.append('SLOW BURN (<0.5×) — HOLD: historical avg 30d = +7.29%  win% 57.2%')
    if slow_burn:
        for r in slow_burn:
            flag = '  [OPTIONS_ACTIVE]' if r['options_active'] else ''
            lines.append(
                f"  {r['ticker']:<6}  {r['total']:>7,} mentions  "
                f"{r['velocity']:.2f}x  [{_sub_list(r['by_sub'])}]{flag}"
            )
    else:
        lines.append('  (none)')

    # ── Short squeeze watch ──────────────────────────────────────────────────
    squeeze = [r for r in rows if r.get('squeeze_watch')]
    lines += [
        '',
        dash,
        '── SHORT SQUEEZE WATCH ─────────────────────────────────────────────────',
        f'   WSB mentions + days-to-cover > {SQUEEZE_DTC_MIN:.0f}  (potential squeeze setup)',
        dash,
    ]
    if squeeze:
        lines.append(
            f"  {'Ticker':<6}  {'Mentions':>8}  {'Vel':>5}  {'Score':>5}  "
            f"{'DtC':>5}  {'Float%':>7}  Tag"
        )
        lines.append('  ' + '─' * 60)
        for r in sorted(squeeze, key=lambda x: (-(x['days_to_cover'] or 0), -x['live_score'])):
            dtc_str = f"{r['days_to_cover']:.1f}d" if r['days_to_cover'] else '  N/A'
            fp_str  = f"{r['float_percent']:.1f}%" if r['float_percent'] else '   N/A'
            tag     = 'SLOW_BURN' if r['slow_burn'] else r['vel_tag']
            lines.append(
                f"  {r['ticker']:<6}  {r['total']:>8,}  {r['velocity']:>4.1f}x  "
                f"{r['live_score']:>5.1f}  {dtc_str:>5}  {fp_str:>7}  {tag}  *** SQUEEZE_WATCH ***"
            )
    else:
        lines.append('  (no tickers today with DtC > 5 and active WSB mentions)')
        lines.append('  Tip: run fetch_short_interest.py to populate short interest data')

    # ── Multi-subreddit tickers ──────────────────────────────────────────────
    multi = [r for r in rows if r['n_subs'] >= 2]
    if multi:
        lines += [
            '',
            dash,
            '── MULTI-SUBREDDIT TICKERS ─────────────────────────────────────────────',
            f"  {'Ticker':<6}  {'Subs':>4}  Distribution",
            '  ' + '─' * 50,
        ]
        for r in sorted(multi, key=lambda x: -x['n_subs'])[:10]:
            dist = '  '.join(
                f"{_sub_abbrev(s)}:{c:,}"
                for s, c in sorted(r['by_sub'].items(), key=lambda x: -x[1])
            )
            lines.append(f"  {r['ticker']:<6}  {r['n_subs']:>4}  {dist}")

    # ── Summary ──────────────────────────────────────────────────────────────
    high_score   = sum(1 for r in rows if r['live_score'] > 60)
    rising       = sum(1 for r in rows if r['vel_tag'] == 'RISING')
    squeeze_ct   = sum(1 for r in rows if r.get('squeeze_watch'))
    options_ct   = sum(1 for r in rows if r.get('options_active'))

    lines += [
        '',
        sep,
        '── SUMMARY ─────────────────────────────────────────────────────────────',
        f"  Active tickers : {len(rows)}   Total mentions: {total_mentions:,}",
        f"  High-score (>60): {high_score}   Hot velocity (3-5×): {len(hot)}   "
        f"Extreme (>5×): {len(extreme)}",
        f"  Rising (1.5-3×): {rising}   Slow-burn (<0.5×): {len(slow_burn)}   "
        f"Multi-sub: {len(multi)}   Squeeze watch: {squeeze_ct}   Options active: {options_ct}",
        '',
        '  Signal legend (from Phase 3 backtests, 2021-2024):',
        '    HOT (3-5×)       →  +1.79% avg 7d   58.6% win rate',
        '    EXTREME (>5×)    →  -3.41% avg 7d   CAUTION',
        '    SLOW_BURN (<0.5) →  +7.29% avg 30d  57.2% win  (strongest edge)',
        '    OPTIONS_ACTIVE   →  ticker appeared in options_yolo posts (Dec 2021)',
        '                        70% of r/options live mentions match this flag',
        '                        (r/options sub mention is a proxy for active options interest)',
        sep,
    ]

    return '\n'.join(lines)


# ── Main ─────────────────────────────────────────────────────────────────────

def generate_report(date: str | None = None, top: int = 15, db_path: str = DB_PATH) -> str:
    if date is None:
        date = datetime.now().strftime('%Y-%m-%d')

    conn = sqlite3.connect(db_path)
    conn.execute('PRAGMA journal_mode=WAL')

    ticker_data = load_today(conn, date)
    if not ticker_data:
        conn.close()
        return (
            f'No data in daily_mentions for {date}.\n'
            'Run: python scrapers/fetch_daily_mentions.py'
        )

    velocities, vel_note = compute_velocity(conn, date, ticker_data)
    ticker_list = list(ticker_data.keys())
    si_data      = load_short_interest(conn, ticker_list)
    opts_active  = load_options_active(conn, ticker_list)
    conn.close()

    rows = build_rows(ticker_data, velocities, si_data=si_data, options_active=opts_active)
    return format_report(rows, date, vel_note, top)


def main():
    parser = argparse.ArgumentParser(description='WSB daily signal report')
    parser.add_argument('--date',  default=None, help='Date YYYY-MM-DD (default: today)')
    parser.add_argument('--fetch', action='store_true', help='Fetch fresh data first')
    parser.add_argument('--top',   type=int, default=15, help='Top N tickers to show')
    args = parser.parse_args()

    if args.fetch:
        sys.path.insert(0, ROOT)
        from scrapers.fetch_daily_mentions import fetch_daily_mentions
        print('Fetching fresh daily mentions...')
        fetch_daily_mentions()
        print()

    date = args.date or datetime.now().strftime('%Y-%m-%d')
    report = generate_report(date=date, top=args.top)

    print(report)

    out_path = os.path.join(LOG_DIR, f'daily_report_{date}.txt')
    with open(out_path, 'w') as f:
        f.write(report)
        f.write('\n')
    print(f'\nReport saved → {out_path}')


if __name__ == '__main__':
    main()
