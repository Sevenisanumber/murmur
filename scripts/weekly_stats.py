#!/usr/bin/env python3
"""
Weekly stats summary. Called by cron every Sunday at 8:00 AM.

Queries the database and writes a human-readable summary to
logs/weekly_stats.log. Useful for a quick sanity check that
the pipeline is producing sensible data without opening the dashboard.
"""

import os
import sys
import sqlite3
import time
import logging
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

DB_PATH  = os.path.join(ROOT, 'data', 'wsb.db')
LOG_PATH = os.path.join(ROOT, 'logs', 'weekly_stats.log')

os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_PATH),
    ],
)
log = logging.getLogger(__name__)


def main():
    conn = sqlite3.connect(DB_PATH)
    now  = datetime.now(tz=timezone.utc).strftime('%Y-%m-%d %H:%M UTC')

    log.info(f'=== Weekly Stats — {now} ===')

    # ── database size ─────────────────────────────────────────────────────────
    total_posts    = conn.execute('SELECT COUNT(*) FROM posts').fetchone()[0]
    total_tickers  = conn.execute('SELECT COUNT(DISTINCT ticker) FROM post_tickers').fetchone()[0]
    total_prices   = conn.execute('SELECT COUNT(*) FROM prices').fetchone()[0]
    total_returns  = conn.execute(
        'SELECT COUNT(*) FROM post_tickers WHERE forward_return_1d IS NOT NULL'
    ).fetchone()[0]
    missing_returns = conn.execute(
        'SELECT COUNT(*) FROM post_tickers WHERE forward_return_1d IS NULL'
    ).fetchone()[0]

    log.info(f'Database:')
    log.info(f'  posts:              {total_posts:>10,}')
    log.info(f'  tickers tracked:    {total_tickers:>10,}')
    log.info(f'  price rows:         {total_prices:>10,}')
    log.info(f'  returns calculated: {total_returns:>10,}')
    log.info(f'  returns missing:    {missing_returns:>10,}')

    # ── date range ────────────────────────────────────────────────────────────
    bounds = conn.execute(
        'SELECT MIN(created_utc), MAX(created_utc) FROM posts WHERE created_utc > 0'
    ).fetchone()
    if bounds[0]:
        earliest = datetime.fromtimestamp(bounds[0], tz=timezone.utc).strftime('%Y-%m-%d')
        latest   = datetime.fromtimestamp(bounds[1], tz=timezone.utc).strftime('%Y-%m-%d')
        log.info(f'  dataset range:      {earliest} → {latest}')

    # ── top 10 tickers all-time ───────────────────────────────────────────────
    log.info('Top 10 tickers (all-time mentions):')
    rows = conn.execute(
        'SELECT ticker, COUNT(*) n FROM post_tickers GROUP BY ticker ORDER BY n DESC LIMIT 10'
    ).fetchall()
    for ticker, n in rows:
        log.info(f'  {ticker:<6} {n:>6,} mentions')

    # ── last 7 days of signals ────────────────────────────────────────────────
    cutoff_7d = (bounds[1] or 0) - 7 * 86400
    log.info('Top tickers last 7 days of dataset (≥2 mentions):')
    rows = conn.execute(
        '''SELECT pt.ticker,
                  COUNT(*) mentions,
                  ROUND(AVG(pt.forward_return_7d) * 100, 2) avg_7d
             FROM post_tickers pt
             JOIN posts p ON p.post_id = pt.post_id
            WHERE p.created_utc >= ?
              AND pt.forward_return_7d IS NOT NULL
            GROUP BY pt.ticker
           HAVING mentions >= 2
            ORDER BY mentions DESC
            LIMIT 10''',
        (cutoff_7d,)
    ).fetchall()
    for ticker, mentions, avg_7d in rows:
        sign = '+' if (avg_7d or 0) >= 0 else ''
        log.info(f'  {ticker:<6} {mentions:>3} mentions  7d avg: {sign}{avg_7d}%')

    # ── pipeline health: last 7 days of real-time runs ────────────────────────
    cutoff_real = int(time.time()) - 7 * 86400
    runs = conn.execute(
        '''SELECT script, status, COUNT(*) n
             FROM scrape_log
            WHERE started_at >= ?
            GROUP BY script, status
            ORDER BY script, status''',
        (cutoff_real,)
    ).fetchall()
    if runs:
        log.info('Pipeline runs last 7 days:')
        for script, status, n in runs:
            label = script or '(untagged)'
            log.info(f'  {label:<20} {status:<10} {n} run(s)')
    else:
        log.info('Pipeline runs last 7 days: none recorded')

    # ── scrape_log failures ───────────────────────────────────────────────────
    failures = conn.execute(
        "SELECT script, errors, started_at FROM scrape_log WHERE status='failure' AND started_at >= ? ORDER BY started_at DESC LIMIT 5",
        (cutoff_real,)
    ).fetchall()
    if failures:
        log.warning('Recent failures:')
        for script, errors, started_at in failures:
            dt = datetime.fromtimestamp(started_at, tz=timezone.utc).strftime('%Y-%m-%d %H:%M')
            log.warning(f'  [{dt}] {script}: {errors}')

    conn.close()
    log.info('=== Weekly stats complete ===')


if __name__ == '__main__':
    main()
