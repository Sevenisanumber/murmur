#!/usr/bin/env python3
"""
Fetch short interest data for active WSB tickers via Yahoo Finance (yfinance).

Yahoo Finance sources its short interest figures from FINRA semi-monthly filings,
so the data matches the FINRA reports published around the 1st and 15th.
Fields: sharesShort → short_interest, shortRatio → days_to_cover,
        shortPercentOfFloat × 100 → float_percent.

Runs twice monthly (cron: 0 7 1,15 * *).  Targets tickers active in
daily_mentions (past 60 days) plus any open paper_trades positions.
Skips tickers with data from the past 10 days unless --force is used.

Usage:
    python scrapers/fetch_short_interest.py
    python scrapers/fetch_short_interest.py --dry-run
    python scrapers/fetch_short_interest.py --ticker GME
    python scrapers/fetch_short_interest.py --force
"""

import argparse
import logging
import os
import sqlite3
import time
import warnings
from datetime import datetime, timedelta

warnings.filterwarnings('ignore')

ROOT     = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH  = os.path.join(ROOT, 'data', 'wsb.db')
LOG_PATH = os.path.join(ROOT, 'logs', 'fetch_short_interest.log')

os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[logging.StreamHandler(), logging.FileHandler(LOG_PATH)],
)
log = logging.getLogger(__name__)

SLEEP_BETWEEN = 1.0   # seconds between yfinance .info calls (rate-limit courtesy)
FRESH_DAYS    = 10    # skip tickers fetched within this window (override with --force)


# ── DB helpers ────────────────────────────────────────────────────────────────

def init_table(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS short_interest (
            ticker        TEXT NOT NULL,
            report_date   TEXT NOT NULL,
            short_interest INTEGER,
            days_to_cover REAL,
            float_percent REAL,
            fetched_at    TEXT,
            PRIMARY KEY (ticker, report_date)
        );
        CREATE INDEX IF NOT EXISTS idx_si_ticker ON short_interest(ticker);
        CREATE INDEX IF NOT EXISTS idx_si_date   ON short_interest(report_date);
    """)
    conn.commit()


def get_active_tickers(conn: sqlite3.Connection) -> list[str]:
    cutoff = (datetime.now() - timedelta(days=60)).strftime('%Y-%m-%d')
    recent = {r[0] for r in conn.execute(
        "SELECT DISTINCT ticker FROM daily_mentions WHERE date >= ?", (cutoff,)
    )}
    open_pos = {r[0] for r in conn.execute(
        "SELECT DISTINCT ticker FROM paper_trades WHERE status = 'open'"
    )}
    tickers = sorted(recent | open_pos)
    log.info(
        f'{len(recent)} from daily_mentions (60d) + {len(open_pos)} open positions '
        f'= {len(tickers)} unique tickers'
    )
    return tickers


def get_recently_fetched(conn: sqlite3.Connection) -> set[str]:
    cutoff = (datetime.now() - timedelta(days=FRESH_DAYS)).strftime('%Y-%m-%d %H:%M:%S')
    rows = conn.execute(
        "SELECT DISTINCT ticker FROM short_interest WHERE fetched_at >= ?", (cutoff,)
    ).fetchall()
    return {r[0] for r in rows}


# ── yfinance fetch ────────────────────────────────────────────────────────────

def _report_date_from_ts(ts) -> str:
    """Convert Unix timestamp to YYYY-MM-DD; fall back to most recent 1st/15th."""
    if ts:
        try:
            return datetime.fromtimestamp(int(ts)).strftime('%Y-%m-%d')
        except (ValueError, OSError):
            pass
    today = datetime.now()
    if today.day >= 15:
        return today.replace(day=15).strftime('%Y-%m-%d')
    return today.replace(day=1).strftime('%Y-%m-%d')


def fetch_one(ticker: str) -> dict | None:
    """Fetch short interest fields for a single ticker via yfinance .info."""
    try:
        import yfinance as yf
        info = yf.Ticker(ticker).info
        short_int = info.get('sharesShort')
        if not short_int:
            return None
        dtc = info.get('shortRatio')
        fp  = info.get('shortPercentOfFloat')
        return {
            'short_interest': int(short_int),
            'days_to_cover':  round(float(dtc), 2) if dtc is not None else None,
            'float_percent':  round(float(fp) * 100, 2) if fp is not None else None,
            'report_date':    _report_date_from_ts(info.get('dateShortInterest')),
        }
    except Exception as e:
        log.debug(f'{ticker}: {e}')
        return None


# ── Main fetch loop ───────────────────────────────────────────────────────────

def fetch_short_interest(
    db_path: str = DB_PATH,
    dry_run: bool = False,
    force: bool = False,
    single_ticker: str | None = None,
) -> int:
    conn = sqlite3.connect(db_path)
    conn.execute('PRAGMA journal_mode=WAL')
    init_table(conn)

    if single_ticker:
        tickers = [single_ticker.upper()]
    else:
        tickers = get_active_tickers(conn)

    if not force and not single_ticker:
        fresh = get_recently_fetched(conn)
        stale = [t for t in tickers if t not in fresh]
        if len(fresh) > 0:
            log.info(f'Skipping {len(fresh)} tickers with data < {FRESH_DAYS}d old '
                     f'(--force to override)')
        tickers = stale

    if not tickers:
        log.info('All tickers up-to-date — nothing to fetch')
        conn.close()
        return 0

    log.info(f'Fetching short interest for {len(tickers)} tickers...')

    fetched_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    stored = skipped = 0

    for i, ticker in enumerate(tickers, 1):
        data = fetch_one(ticker)

        if data is None:
            skipped += 1
        elif dry_run:
            log.info(
                f'[{i}/{len(tickers)}] {ticker:<6} | si={data["short_interest"]:>12,} | '
                f'dtc={str(data["days_to_cover"]):>6} | fp={str(data["float_percent"]):>6}% | '
                f'{data["report_date"]}'
            )
        else:
            conn.execute(
                """INSERT OR REPLACE INTO short_interest
                   (ticker, report_date, short_interest, days_to_cover, float_percent, fetched_at)
                   VALUES (?,?,?,?,?,?)""",
                (ticker, data['report_date'], data['short_interest'],
                 data['days_to_cover'], data['float_percent'], fetched_at),
            )
            conn.commit()
            stored += 1
            if i % 25 == 0 or i == len(tickers):
                log.info(f'[{i}/{len(tickers)}] stored={stored} no-data={skipped}')

        if i < len(tickers):
            time.sleep(SLEEP_BETWEEN)

    conn.close()
    log.info(f'fetch_short_interest complete: {stored} stored, {skipped} no-data')
    return stored


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description='Fetch short interest data via yfinance')
    parser.add_argument('--dry-run', action='store_true', help='Show data without storing')
    parser.add_argument('--force',   action='store_true', help='Refetch even if recent data exists')
    parser.add_argument('--ticker',  default=None,        help='Fetch a single ticker only')
    parser.add_argument('--db',      default=DB_PATH,     help='SQLite DB path')
    args = parser.parse_args()

    n = fetch_short_interest(
        db_path=args.db,
        dry_run=args.dry_run,
        force=args.force,
        single_ticker=args.ticker,
    )
    if not args.dry_run:
        print(f'\nDone. {n} records stored.')


if __name__ == '__main__':
    main()
