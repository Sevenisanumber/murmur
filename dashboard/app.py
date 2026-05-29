#!/usr/bin/env python3
"""WSB Signal Lab dashboard — Flask app serving a single-page read-only view."""

import os
import sqlite3
import time
from datetime import datetime, timezone
from flask import Flask, render_template

ROOT    = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(ROOT, 'data', 'wsb.db')

app = Flask(__name__)


# ── template filters ───────────────────────────────────────────────────────────

@app.template_filter('comma')
def comma_filter(n):
    try:
        return f'{int(n):,}'
    except (TypeError, ValueError):
        return '—'


@app.template_filter('price')
def price_filter(n):
    try:
        return f'${float(n):.2f}'
    except (TypeError, ValueError):
        return '—'


@app.template_filter('pct')
def pct_filter(n):
    """Format a float as a signed percentage string, or None to signal '—'."""
    try:
        f = float(n)
        return f'{f:+.2f}'
    except (TypeError, ValueError):
        return None


@app.template_filter('ts')
def ts_filter(ts):
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    except (TypeError, ValueError):
        return '—'


# ── helpers ────────────────────────────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# ── routes ─────────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    conn = get_db()

    # Dataset bounds
    bounds    = conn.execute(
        'SELECT MIN(created_utc), MAX(created_utc) FROM posts WHERE created_utc > 0'
    ).fetchone()
    latest_ts = bounds[1] or 0
    latest_dt = datetime.fromtimestamp(latest_ts, tz=timezone.utc)
    latest_date = latest_dt.strftime('%Y-%m-%d')
    cutoff_24h  = latest_ts - 86_400
    cutoff_30d  = latest_ts - 30 * 86_400

    # ── pulse: top 10 tickers from last 24h of dataset ────────────────────────
    pulse_raw = conn.execute('''
        SELECT pt.ticker,
               COUNT(*)               AS mentions,
               ROUND(AVG(p.score), 0) AS avg_score
          FROM post_tickers pt
          JOIN posts p ON p.post_id = pt.post_id
         WHERE p.created_utc >= :cutoff
         GROUP BY pt.ticker
         ORDER BY mentions DESC
         LIMIT 10
    ''', {'cutoff': cutoff_24h}).fetchall()

    pulse = []
    for row in pulse_raw:
        ticker = row['ticker']
        pr = conn.execute(
            'SELECT close FROM prices WHERE ticker=? AND date<=? ORDER BY date DESC LIMIT 1',
            (ticker, latest_date)
        ).fetchone()
        prev = conn.execute(
            'SELECT close FROM prices WHERE ticker=? AND date<? ORDER BY date DESC LIMIT 1',
            (ticker, latest_date)
        ).fetchone()
        close = pr['close']   if pr   else None
        prev_c = prev['close'] if prev else None
        chg = round((close - prev_c) / prev_c * 100, 2) if (close and prev_c) else None
        pulse.append({
            'ticker':    ticker,
            'mentions':  row['mentions'],
            'avg_score': int(row['avg_score']) if row['avg_score'] else 0,
            'price':     close,
            'change':    chg,
        })

    # ── signal history: last 30 days, ≥2 mentions, limit 200 rows ─────────────
    history = conn.execute('''
        SELECT DATE(p.created_utc, 'unixepoch')          AS post_date,
               pt.ticker,
               COUNT(*)                                   AS mentions,
               ROUND(AVG(pt.forward_return_1d)  * 100, 2) AS avg_1d,
               ROUND(AVG(pt.forward_return_7d)  * 100, 2) AS avg_7d,
               ROUND(AVG(pt.forward_return_30d) * 100, 2) AS avg_30d
          FROM post_tickers pt
          JOIN posts p ON p.post_id = pt.post_id
         WHERE p.created_utc >= :cutoff
           AND pt.forward_return_1d IS NOT NULL
         GROUP BY post_date, pt.ticker
        HAVING mentions >= 2
         ORDER BY post_date DESC, mentions DESC
         LIMIT 200
    ''', {'cutoff': cutoff_30d}).fetchall()

    # ── today's top tickers from daily_mentions ───────────────────────────────
    dm_date_row = conn.execute('SELECT MAX(date) FROM daily_mentions').fetchone()
    dm_date     = dm_date_row[0] if dm_date_row and dm_date_row[0] else None

    daily_top = []
    if dm_date:
        daily_top_raw = conn.execute('''
            SELECT ticker,
                   SUM(mention_count)                                    AS total,
                   GROUP_CONCAT(subreddit || ':' || mention_count, ', ') AS sources
              FROM daily_mentions
             WHERE date = ?
             GROUP BY ticker
             ORDER BY total DESC
             LIMIT 20
        ''', (dm_date,)).fetchall()
        daily_top = [dict(r) for r in daily_top_raw]

    # ── data health ───────────────────────────────────────────────────────────
    scrape_runs = conn.execute('''
        SELECT run_id, started_at, finished_at, posts_fetched, errors, status, script
          FROM scrape_log
         ORDER BY started_at DESC
         LIMIT 5
    ''').fetchall()

    total_posts   = conn.execute('SELECT COUNT(*) FROM posts').fetchone()[0]
    total_tickers = conn.execute('SELECT COUNT(DISTINCT ticker) FROM post_tickers').fetchone()[0]
    # Use real wall-clock time so the health indicator reflects actual recent runs,
    # not the dataset's historical date range.
    recent_errors = conn.execute(
        "SELECT COUNT(*) FROM scrape_log WHERE status='failure' AND started_at >= ?",
        (int(time.time()) - 86_400,)
    ).fetchone()[0]

    conn.close()

    return render_template(
        'index.html',
        latest_date   = latest_date,
        pulse         = pulse,
        history       = history,
        daily_top     = daily_top,
        dm_date       = dm_date,
        scrape_runs   = scrape_runs,
        total_posts   = total_posts,
        total_tickers = total_tickers,
        recent_errors = recent_errors,
        generated_at  = datetime.now().strftime('%Y-%m-%d %H:%M'),
    )


if __name__ == '__main__':
    port = int(os.getenv('DASHBOARD_PORT', 5001))
    app.run(host='0.0.0.0', port=port, debug=False)
