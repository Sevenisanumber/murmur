#!/usr/bin/env python3
"""
Weekly Digest — Murmur

Collects the past 7 days of paper trading performance, signal quality,
and pipeline health, then asks Claude to analyze the data and give
actionable recommendations.

Steps:
  1. Collect structured data from wsb.db (paper_trades, daily_mentions,
     scrape_log, prices)
  2. Call claude-sonnet-4-20250514 with the data (system prompt cached)
  3. Save full response to logs/weekly_digest_YYYY-MM-DD.txt
  4. Send Pushover notification when complete

Usage:
  python scripts/weekly_digest.py              # run for today's week
  python scripts/weekly_digest.py --dry-run    # collect data, skip API call
  python scripts/weekly_digest.py --date 2026-05-25

Cron (Sunday 9am CDT = 10am ET, after weekly_stats.py at 8am):
  0 9 * * 0  cd /home/plex/wsb-signal-lab && venv/bin/python scripts/weekly_digest.py >> logs/cron.log 2>&1
"""

import argparse
import logging
import math
import os
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from dotenv import load_dotenv
load_dotenv(os.path.join(ROOT, '.env'))

DB_PATH  = os.path.join(ROOT, 'data', 'wsb.db')
LOG_DIR  = os.path.join(ROOT, 'logs')

os.makedirs(LOG_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[logging.StreamHandler()],
)
log = logging.getLogger(__name__)

MODEL    = 'claude-sonnet-4-20250514'
MAX_TOKENS = 4096

SYSTEM_PROMPT = (
    "You are analyzing the weekly performance of a WSB sentiment trading signal "
    "system. Review the data provided and give specific, actionable recommendations "
    "to improve signal quality and trading rules. Be direct and critical. Focus on "
    "what is and isn't working."
)


# ── Data collection ───────────────────────────────────────────────────────────

def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone())


def collect_paper_trading(conn: sqlite3.Connection, since: str, today: str) -> str:
    lines = ['=== PAPER TRADING PERFORMANCE ===']

    if not _table_exists(conn, 'paper_trades'):
        lines.append('Table not yet created — paper trading has not started.')
        return '\n'.join(lines)

    total = conn.execute("SELECT COUNT(*) FROM paper_trades").fetchone()[0]
    if total == 0:
        lines.append('No trades yet — paper trading just started. '
                     'System is monitoring signals and will execute first trades '
                     'when HOT_SCORE (score>70, 3-5x velocity) or SLOW_BURN '
                     '(score>60, <0.5x velocity) conditions are met.')
        return '\n'.join(lines)

    # ── Trades opened this week ───────────────────────────────────────────────
    opened = conn.execute("""
        SELECT ticker, signal_type, entry_date, entry_price, position_size, status
          FROM paper_trades
         WHERE entry_date >= ? AND entry_date <= ?
         ORDER BY entry_date
    """, (since, today)).fetchall()

    lines.append(f'\nTrades opened this week: {len(opened)}')
    if opened:
        for row in opened:
            ticker, sig, edate, eprice, size, status = row
            lines.append(f'  {edate}  {ticker:<6}  {sig:<12}  '
                         f'entry=${eprice:.2f}  ${size:.0f}  [{status}]')

    # ── Trades closed this week ───────────────────────────────────────────────
    closed_week = conn.execute("""
        SELECT ticker, signal_type, entry_date, exit_date,
               entry_price, exit_price, pnl, pnl_pct, exit_reason
          FROM paper_trades
         WHERE status='closed' AND exit_date >= ? AND exit_date <= ?
         ORDER BY exit_date
    """, (since, today)).fetchall()

    lines.append(f'\nTrades closed this week: {len(closed_week)}')
    if closed_week:
        for row in closed_week:
            ticker, sig, edate, xdate, eprice, xprice, pnl, pnl_pct, reason = row
            sign = '+' if (pnl or 0) >= 0 else ''
            lines.append(f'  {xdate}  {ticker:<6}  {sig:<12}  '
                         f'entry=${eprice:.2f} → ${xprice:.2f}  '
                         f'P&L: {sign}${pnl:.2f} ({sign}{pnl_pct:.1f}%)  '
                         f'exit={reason}')

    # ── All-time summary ──────────────────────────────────────────────────────
    stats = conn.execute("""
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN status='closed' THEN 1 ELSE 0 END) AS closed,
            SUM(CASE WHEN status='open'   THEN 1 ELSE 0 END) AS open,
            SUM(CASE WHEN status='closed' AND pnl >= 0 THEN 1 ELSE 0 END) AS wins,
            COALESCE(SUM(CASE WHEN status='closed' THEN pnl ELSE 0 END), 0) AS realized_pnl,
            COALESCE(SUM(position_size), 0) AS total_deployed
          FROM paper_trades
    """).fetchone()
    total, n_closed, n_open, wins, realized_pnl, deployed = stats
    win_rate = (wins / n_closed * 100) if n_closed > 0 else None

    lines.append(f'\nAll-time summary:')
    lines.append(f'  Total trades: {total}  (open: {n_open}, closed: {n_closed})')
    if n_closed > 0:
        lines.append(f'  Win rate: {win_rate:.0f}%  ({wins}/{n_closed} trades)')
        lines.append(f'  Realized P&L: {"+" if realized_pnl >= 0 else ""}${realized_pnl:.2f}  '
                     f'on ${deployed:.0f} deployed')

        # Best / worst
        best = conn.execute("""
            SELECT ticker, signal_type, pnl, pnl_pct, exit_reason
              FROM paper_trades WHERE status='closed' ORDER BY pnl DESC LIMIT 1
        """).fetchone()
        worst = conn.execute("""
            SELECT ticker, signal_type, pnl, pnl_pct, exit_reason
              FROM paper_trades WHERE status='closed' ORDER BY pnl ASC LIMIT 1
        """).fetchone()
        if best:
            lines.append(f'  Best trade:  {best[0]} {best[1]}  '
                         f'+${best[2]:.2f} (+{best[3]:.1f}%)  exit={best[4]}')
        if worst and worst[0] != (best[0] if best else None):
            sign = '+' if (worst[2] or 0) >= 0 else ''
            lines.append(f'  Worst trade: {worst[0]} {worst[1]}  '
                         f'{sign}${worst[2]:.2f} ({sign}{worst[3]:.1f}%)  exit={worst[4]}')

    # ── Signal type breakdown ─────────────────────────────────────────────────
    breakdown = conn.execute("""
        SELECT signal_type,
               COUNT(*) AS n,
               SUM(CASE WHEN status='closed' AND pnl >= 0 THEN 1 ELSE 0 END) AS wins,
               SUM(CASE WHEN status='closed' THEN 1 ELSE 0 END) AS closed,
               COALESCE(SUM(CASE WHEN status='closed' THEN pnl ELSE 0 END), 0) AS pnl
          FROM paper_trades
         GROUP BY signal_type
    """).fetchall()
    if breakdown:
        lines.append('\nBy signal type:')
        for sig, n, w, c, p in breakdown:
            wr = f'{w/c*100:.0f}% win' if c > 0 else 'no closed'
            sign = '+' if (p or 0) >= 0 else ''
            lines.append(f'  {sig:<12}  {n} trades  {wr}  P&L: {sign}${p:.2f}')

    # ── Exit reason breakdown ─────────────────────────────────────────────────
    exits = conn.execute("""
        SELECT exit_reason, COUNT(*) AS n
          FROM paper_trades WHERE status='closed'
         GROUP BY exit_reason ORDER BY n DESC
    """).fetchall()
    if exits:
        lines.append('\nExit reasons (all-time):  ' +
                     '  '.join(f'{r}: {n}' for r, n in exits))

    return '\n'.join(lines)


def collect_signal_quality(conn: sqlite3.Connection, since: str, today: str) -> str:
    lines = ['=== SIGNAL QUALITY ===']

    # ── Velocity distribution this week ──────────────────────────────────────
    days_with_data = conn.execute("""
        SELECT DISTINCT date FROM daily_mentions
         WHERE date >= ? AND date <= ?
         ORDER BY date
    """, (since, today)).fetchall()
    days_with_data = [r[0] for r in days_with_data]

    if not days_with_data:
        lines.append('No daily_mentions data found for this week.')
        return '\n'.join(lines)

    lines.append(f'\nDays with signal data this week: {len(days_with_data)}')
    lines.append(f'  Dates: {", ".join(days_with_data)}')

    # Per-day velocity counts using daily_report functions
    from scrapers.daily_report import load_today, compute_velocity, build_rows

    extreme_tickers = set()
    hot_tickers     = set()
    slow_burn_tickers = set()
    all_rows_this_week = []

    weekly_extreme = weekly_hot = weekly_slow_burn = weekly_rising = weekly_normal = 0

    for date in days_with_data:
        ticker_data = load_today(conn, date)
        if not ticker_data:
            continue
        velocities, _ = compute_velocity(conn, date, ticker_data)
        rows = build_rows(ticker_data, velocities)

        for r in rows:
            if r['vel_tag'] == 'EXTREME':
                weekly_extreme += 1
                extreme_tickers.add(r['ticker'])
            elif r['vel_tag'] == 'HOT':
                weekly_hot += 1
                hot_tickers.add(r['ticker'])
            elif r['slow_burn']:
                weekly_slow_burn += 1
                slow_burn_tickers.add(r['ticker'])
            elif r['vel_tag'] == 'RISING':
                weekly_rising += 1
            else:
                weekly_normal += 1
            all_rows_this_week.append(r)

    lines.append(f'\nVelocity distribution (ticker-day occurrences):')
    lines.append(f'  EXTREME (>5×, avoided): {weekly_extreme}')
    lines.append(f'  HOT     (3-5×):         {weekly_hot}')
    lines.append(f'  RISING  (1.5-3×):       {weekly_rising}')
    lines.append(f'  NORMAL  (0.5-1.5×):     {weekly_normal}')
    lines.append(f'  SLOW_BURN (<0.5×):      {weekly_slow_burn}')

    if extreme_tickers:
        lines.append(f'\n  EXTREME tickers avoided: {", ".join(sorted(extreme_tickers))}')

    # ── Top 10 highest-scored tickers this week (by max score) ────────────────
    if all_rows_this_week:
        from collections import defaultdict
        ticker_max_score = defaultdict(float)
        for r in all_rows_this_week:
            ticker_max_score[r['ticker']] = max(ticker_max_score[r['ticker']],
                                                 r['live_score'])
        top10 = sorted(ticker_max_score.items(), key=lambda x: -x[1])[:10]
        lines.append('\nTop 10 tickers by peak score this week:')
        for ticker, score in top10:
            # Find what vel_tag that ticker had at its peak
            peak_row = max((r for r in all_rows_this_week if r['ticker'] == ticker),
                           key=lambda r: r['live_score'])
            tag = 'SLOW_BURN' if peak_row['slow_burn'] else peak_row['vel_tag']
            lines.append(f'  {ticker:<6}  score={score:.1f}  vel={peak_row["velocity"]:.1f}x  tag={tag}')

    # ── HOT_SCORE candidates (would have triggered a buy) ─────────────────────
    would_buy_hot  = [(r['ticker'], r['live_score'], r['velocity'])
                      for r in all_rows_this_week
                      if r['live_score'] > 70 and r['vel_tag'] == 'HOT']
    would_buy_slow = [(r['ticker'], r['live_score'], r['velocity'])
                      for r in all_rows_this_week
                      if r['live_score'] > 60 and r['slow_burn']]

    lines.append(f'\nBuy signals generated this week:')
    lines.append(f'  HOT_SCORE (score>70, 3-5×): {len(would_buy_hot)} occurrences')
    if would_buy_hot:
        seen = set()
        for t, s, v in sorted(would_buy_hot, key=lambda x: -x[1])[:5]:
            if t not in seen:
                lines.append(f'    {t:<6}  score={s:.1f}  vel={v:.1f}x')
                seen.add(t)

    lines.append(f'  SLOW_BURN (score>60, <0.5×): {len(would_buy_slow)} occurrences')
    if would_buy_slow:
        seen = set()
        for t, s, v in sorted(would_buy_slow, key=lambda x: -x[1])[:5]:
            if t not in seen:
                lines.append(f'    {t:<6}  score={s:.1f}  vel={v:.2f}x')
                seen.add(t)

    # ── Price follow-through for flagged tickers ──────────────────────────────
    # Use the prices table (historical) for any tickers that have post-signal data
    flagged = list({r['ticker'] for r in all_rows_this_week
                    if r['vel_tag'] in ('HOT', 'EXTREME') or r['slow_burn']})[:20]
    price_moves = []
    for ticker in flagged:
        row = conn.execute("""
            SELECT date, close FROM prices
             WHERE ticker = ? AND date >= ? AND date <= ?
             ORDER BY date
             LIMIT 1
        """, (ticker, since, today)).fetchone()
        if not row:
            continue
        start_price = row[1]
        end_row = conn.execute("""
            SELECT close FROM prices
             WHERE ticker = ? AND date > ? AND date <= date(?, '+7 days')
             ORDER BY date DESC LIMIT 1
        """, (ticker, row[0], row[0])).fetchone()
        if not end_row:
            continue
        move = (end_row[0] - start_price) / start_price * 100
        price_moves.append((ticker, round(move, 1)))

    if price_moves:
        price_moves.sort(key=lambda x: -abs(x[1]))
        lines.append('\nPrice moves for flagged tickers (from prices table — may be historical):')
        for ticker, move in price_moves[:10]:
            sign = '+' if move >= 0 else ''
            lines.append(f'  {ticker:<6}  {sign}{move:.1f}% over 7d')
    else:
        lines.append('\nPrice follow-through: no price data available for this week '
                     '(prices table covers historical backtesting data)')

    return '\n'.join(lines)


def collect_data_health(conn: sqlite3.Connection, since_ts: int, today: str,
                        since: str) -> str:
    lines = ['=== DATA HEALTH ===']

    # ── Pipeline runs ─────────────────────────────────────────────────────────
    runs = conn.execute("""
        SELECT script,
               strftime('%Y-%m-%d', datetime(started_at, 'unixepoch')) AS day,
               status,
               COUNT(*) AS n
          FROM scrape_log
         WHERE started_at >= ?
         GROUP BY script, day, status
         ORDER BY day DESC, script, status
    """, (since_ts,)).fetchall()

    if runs:
        from collections import defaultdict
        summary = defaultdict(lambda: {'success': 0, 'failure': 0})
        for script, day, status, n in runs:
            summary[script][status] = summary[script].get(status, 0) + n

        lines.append('\nPipeline run summary (last 7 days):')
        for script, counts in sorted(summary.items(), key=lambda x: x[0] or ''):
            ok  = counts.get('success', 0)
            err = counts.get('failure', 0)
            flag = '  ⚠' if err > 0 else ''
            lines.append(f'  {(script or "(untagged)"):<22}  {ok} success  {err} failure{flag}')
    else:
        lines.append('\nNo pipeline runs recorded this week.')

    # ── Recent failures ───────────────────────────────────────────────────────
    failures = conn.execute("""
        SELECT strftime('%Y-%m-%d %H:%M', datetime(started_at, 'unixepoch')) AS dt,
               script, errors
          FROM scrape_log
         WHERE status='failure' AND started_at >= ?
         ORDER BY started_at DESC
         LIMIT 10
    """, (since_ts,)).fetchall()

    if failures:
        lines.append('\nFailure details:')
        for dt, script, errors in failures:
            lines.append(f'  [{dt}] {script}: {(errors or "")[:120]}')
    else:
        lines.append('\nNo failures this week.')

    # ── Daily mentions coverage ───────────────────────────────────────────────
    from datetime import date as date_cls, timedelta as td
    today_date = datetime.strptime(today, '%Y-%m-%d').date()
    since_date = datetime.strptime(since, '%Y-%m-%d').date()
    expected_days = [(since_date + td(days=i)).strftime('%Y-%m-%d')
                     for i in range((today_date - since_date).days + 1)]

    covered = {r[0] for r in conn.execute(
        "SELECT DISTINCT date FROM daily_mentions WHERE date >= ? AND date <= ?",
        (since, today)
    ).fetchall()}

    missing = [d for d in expected_days if d not in covered]
    lines.append(f'\nDaily mentions coverage: {len(covered)}/{len(expected_days)} days')
    if missing:
        lines.append(f'  Missing: {", ".join(missing)}')

    # ── Row counts ────────────────────────────────────────────────────────────
    total_posts = conn.execute('SELECT COUNT(*) FROM posts').fetchone()[0]
    total_tickers = conn.execute(
        'SELECT COUNT(DISTINCT ticker) FROM post_tickers'
    ).fetchone()[0]
    lines.append(f'\nDatabase totals:')
    lines.append(f'  Posts: {total_posts:,}  |  Tracked tickers: {total_tickers:,}')

    return '\n'.join(lines)


# ── Report assembly ───────────────────────────────────────────────────────────

def build_digest_data(conn: sqlite3.Connection, today: str) -> str:
    since = (datetime.strptime(today, '%Y-%m-%d') - timedelta(days=7)).strftime('%Y-%m-%d')
    since_ts = int(datetime.strptime(since, '%Y-%m-%d').replace(
        tzinfo=timezone.utc).timestamp())

    header = '\n'.join([
        '=== WSB SIGNAL LAB — WEEKLY DIGEST ===',
        f'Period : {since} to {today}',
        f'Generated: {datetime.now().strftime("%Y-%m-%d %H:%M")}',
    ])

    trading = collect_paper_trading(conn, since, today)
    signals = collect_signal_quality(conn, since, today)
    health  = collect_data_health(conn, since_ts, today, since)

    return '\n\n'.join([header, trading, signals, health])


# ── Claude API call ───────────────────────────────────────────────────────────

def call_claude(data_text: str) -> str:
    import anthropic

    api_key = os.getenv('ANTHROPIC_API_KEY')
    if not api_key:
        raise RuntimeError('ANTHROPIC_API_KEY not set in .env')

    client = anthropic.Anthropic(api_key=api_key)

    log.info(f'Calling {MODEL} — input ~{len(data_text)//4} tokens est.')

    with client.messages.stream(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=[{
            "type": "text",
            "text": SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"},
        }],
        messages=[{"role": "user", "content": data_text}],
    ) as stream:
        message = stream.get_final_message()

    text = next(
        (block.text for block in message.content if block.type == "text"),
        "(no text in response)"
    )

    usage = message.usage
    log.info(
        f'Claude done — '
        f'input={usage.input_tokens} '
        f'cache_read={getattr(usage, "cache_read_input_tokens", 0)} '
        f'cache_write={getattr(usage, "cache_creation_input_tokens", 0)} '
        f'output={usage.output_tokens}'
    )
    return text


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description='WSB weekly digest')
    parser.add_argument('--date',    default=None,        help='Date YYYY-MM-DD (default: today)')
    parser.add_argument('--dry-run', action='store_true', help='Collect data, skip API call')
    args = parser.parse_args()

    today = args.date or datetime.now().strftime('%Y-%m-%d')

    conn = sqlite3.connect(DB_PATH)
    conn.execute('PRAGMA journal_mode=WAL')

    log.info(f'=== Weekly digest starting | date={today} ===')

    # ── 1. Collect data ───────────────────────────────────────────────────────
    data_text = build_digest_data(conn, today)
    conn.close()

    log.info(f'Data collected ({len(data_text):,} chars)')
    print('\n' + data_text + '\n')

    if args.dry_run:
        log.info('[DRY RUN] Skipping Claude API call and notifications')
        return

    # ── 2. Call Claude ────────────────────────────────────────────────────────
    try:
        analysis = call_claude(data_text)
    except Exception as e:
        log.error(f'Claude API call failed: {e}')
        sys.exit(1)

    # ── 3. Save response ──────────────────────────────────────────────────────
    out_path = os.path.join(LOG_DIR, f'weekly_digest_{today}.txt')
    with open(out_path, 'w') as f:
        f.write(data_text)
        f.write('\n\n' + '=' * 68 + '\n')
        f.write('CLAUDE ANALYSIS\n')
        f.write('=' * 68 + '\n\n')
        f.write(analysis)
        f.write('\n')

    log.info(f'Digest saved → {out_path}')
    print('\n' + '=' * 68)
    print('CLAUDE ANALYSIS')
    print('=' * 68)
    print(analysis)

    # ── 4. Pushover notification ──────────────────────────────────────────────
    from scrapers.notify import send_pushover
    ok = send_pushover(
        f'Murmur Weekly Digest ready — check logs/weekly_digest_{today}.txt',
        title='Murmur Weekly Digest',
    )
    log.info(f'Pushover: {"sent" if ok else "failed"}')

    log.info('=== Weekly digest complete ===')


if __name__ == '__main__':
    main()
