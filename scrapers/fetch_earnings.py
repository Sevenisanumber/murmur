#!/usr/bin/env python3
"""
Fetch upcoming earnings dates for today's active tickers from yfinance.

Tickers are sourced from daily_mentions for the target date.  Results are
upserted into the earnings_calendar table.  Any ticker with earnings within
EARNINGS_WINDOW_DAYS is logged as EARNINGS_NEAR.

Usage:
    python scrapers/fetch_earnings.py
    python scrapers/fetch_earnings.py --date 2026-05-31
    python scrapers/fetch_earnings.py --dry-run
"""

import argparse
import logging
import os
import sqlite3
import sys
import time
import warnings
from datetime import datetime, timedelta

warnings.filterwarnings('ignore')
import yfinance as yf
yf.set_tz_cache_location('/tmp/yf_cache')

ROOT     = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH  = os.path.join(ROOT, 'data', 'wsb.db')
LOG_PATH = os.path.join(ROOT, 'logs', 'fetch_earnings.log')

os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[logging.StreamHandler(), logging.FileHandler(LOG_PATH)],
)
log = logging.getLogger(__name__)

EARNINGS_WINDOW_DAYS = 5
TICKER_SLEEP         = 0.5   # seconds between yfinance calls (polite pacing)
COMMIT_EVERY         = 50    # tickers between DB commits


# ── Schema ─────────────────────────────────────────────────────────────────────

def ensure_earnings_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS earnings_calendar (
            ticker        TEXT PRIMARY KEY,
            earnings_date TEXT,          -- next upcoming date YYYY-MM-DD, NULL if unknown
            fetched_date  TEXT NOT NULL  -- YYYY-MM-DD when this row was last updated
        )
    """)
    conn.execute(
        'CREATE INDEX IF NOT EXISTS idx_ec_date ON earnings_calendar(earnings_date)'
    )
    conn.commit()


# ── Data loading ───────────────────────────────────────────────────────────────

def load_active_tickers(conn: sqlite3.Connection, date: str) -> list[str]:
    """Distinct tickers from daily_mentions for date; falls back to most recent."""
    rows = conn.execute(
        'SELECT DISTINCT ticker FROM daily_mentions WHERE date = ? ORDER BY ticker',
        (date,),
    ).fetchall()
    if rows:
        return [r[0] for r in rows]
    last = conn.execute('SELECT MAX(date) FROM daily_mentions').fetchone()[0]
    if not last:
        return []
    log.warning(f'No daily_mentions for {date} — falling back to {last}')
    rows = conn.execute(
        'SELECT DISTINCT ticker FROM daily_mentions WHERE date = ? ORDER BY ticker',
        (last,),
    ).fetchall()
    return [r[0] for r in rows]


# ── yfinance calendar ─────────────────────────────────────────────────────────

def get_next_earnings_date(ticker: str) -> str | None:
    """
    Return the next earnings date as YYYY-MM-DD via yfinance, or None.
    Handles all known yfinance 0.2.x calendar formats defensively:
      - DataFrame with dates as column headers
      - DataFrame with 'Earnings Date' as a row in the index
      - Dict with 'Earnings Date' key
    """
    try:
        import pandas as pd
        cal = yf.Ticker(ticker).calendar
        if cal is None:
            return None
        if hasattr(cal, 'empty') and cal.empty:
            return None

        dates = []

        if isinstance(cal, pd.DataFrame):
            # Format A: column headers are the earnings date(s)
            for col in cal.columns:
                try:
                    dates.append(pd.Timestamp(col))
                except Exception:
                    pass
            # Format B: 'Earnings Date' is a labelled row in the index
            if 'Earnings Date' in cal.index:
                for val in cal.loc['Earnings Date']:
                    try:
                        dates.append(pd.Timestamp(val))
                    except Exception:
                        pass
        elif isinstance(cal, dict):
            # Format C: dict with list of dates
            for val in cal.get('Earnings Date', []):
                try:
                    dates.append(pd.Timestamp(val))
                except Exception:
                    pass

        today = pd.Timestamp.now().normalize()
        future = [d for d in dates if pd.notna(d) and d >= today]
        return min(future).strftime('%Y-%m-%d') if future else None
    except Exception:
        return None


def is_earnings_near(earnings_date_str: str | None, today_str: str) -> bool:
    if not earnings_date_str:
        return False
    try:
        delta = (
            datetime.strptime(earnings_date_str, '%Y-%m-%d')
            - datetime.strptime(today_str, '%Y-%m-%d')
        ).days
        return 0 <= delta <= EARNINGS_WINDOW_DAYS
    except ValueError:
        return False


# ── Main ───────────────────────────────────────────────────────────────────────

def run(date: str | None = None, dry_run: bool = False) -> list[tuple]:
    """
    Fetch earnings dates for today's active tickers.
    Returns [(ticker, earnings_date), ...] for EARNINGS_NEAR tickers.
    """
    if date is None:
        date = datetime.now().strftime('%Y-%m-%d')

    conn = sqlite3.connect(DB_PATH)
    conn.execute('PRAGMA journal_mode=WAL')
    ensure_earnings_table(conn)

    tickers = load_active_tickers(conn, date)
    total   = len(tickers)

    log.info(f'=== fetch_earnings starting | date={date} tickers={total} dry_run={dry_run} ===')

    if not tickers:
        log.warning('No active tickers — run fetch_daily_mentions first')
        conn.close()
        return []

    if dry_run:
        log.info(f'Dry run: would check {total} tickers, no yfinance calls')
        conn.close()
        return []

    n_found   = 0
    n_no_data = 0
    near: list[tuple] = []

    for i, ticker in enumerate(tickers, 1):
        log.info(f'[{i}/{total}] {ticker}')
        earnings_date = get_next_earnings_date(ticker)

        conn.execute(
            """INSERT OR REPLACE INTO earnings_calendar
               (ticker, earnings_date, fetched_date)
               VALUES (?, ?, ?)""",
            (ticker, earnings_date, date),
        )

        if earnings_date:
            n_found += 1
            if is_earnings_near(earnings_date, date):
                near.append((ticker, earnings_date))
                log.info(f'  EARNINGS_NEAR: {ticker} reports on {earnings_date}')
        else:
            n_no_data += 1

        if i % COMMIT_EVERY == 0 or i == total:
            conn.commit()
            log.info(f'  Committed at ticker {i}/{total}')

        if i < total:
            time.sleep(TICKER_SLEEP)

    conn.commit()
    log.info('=== fetch_earnings complete ===')
    log.info(f'  Tickers checked:              {total:,}')
    log.info(f'  Earnings dates found:         {n_found:,}')
    log.info(f'  No earnings data:             {n_no_data:,}')
    log.info(f'  EARNINGS_NEAR (≤{EARNINGS_WINDOW_DAYS}d):    {len(near)}')
    for ticker, ed in near:
        log.info(f'    {ticker}: {ed}')

    conn.close()
    return near


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Fetch upcoming earnings for active tickers')
    parser.add_argument('--date',    default=None, help='Date YYYY-MM-DD (default: today)')
    parser.add_argument('--dry-run', action='store_true', help='Show ticker count, no fetching')
    args = parser.parse_args()
    run(date=args.date, dry_run=args.dry_run)
