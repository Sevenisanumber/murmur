#!/usr/bin/env python3
"""
Fetch daily OHLCV price data from Alpaca for all tickers in post_tickers.

Date range is derived from the posts table (earliest post - 5 days to
latest post + 35 days) so forward returns can be calculated for all posts.

Rate-limited to ~170 req/min (well under the 200/min free-tier limit).
Skips tickers that already have ≥100 price rows in the target range.
"""

import sqlite3
import os
import sys
import time
import logging
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env'))

ROOT     = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH  = os.path.join(ROOT, 'data', 'wsb.db')
LOG_PATH = os.path.join(ROOT, 'logs', 'fetch_prices.log')

os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[logging.StreamHandler(), logging.FileHandler(LOG_PATH)],
)
log = logging.getLogger(__name__)

RATE_LIMIT_DELAY = 0.36   # ~166 req/min, safely under 200/min
MAX_RETRIES      = 3


def fetch_prices(db_path: str = DB_PATH) -> int:
    import alpaca_trade_api as tradeapi

    api_key    = os.getenv('ALPACA_API_KEY')
    secret_key = os.getenv('ALPACA_SECRET_KEY')
    base_url   = os.getenv('ALPACA_BASE_URL', 'https://paper-api.alpaca.markets')

    if not api_key or not secret_key:
        log.error('Missing ALPACA_API_KEY or ALPACA_SECRET_KEY in .env')
        sys.exit(1)

    api  = tradeapi.REST(api_key, secret_key, base_url, api_version='v2')
    conn = sqlite3.connect(db_path)
    conn.execute('PRAGMA journal_mode=WAL')

    # Date range: cover all posts + 35-day forward window
    row = conn.execute(
        'SELECT MIN(created_utc), MAX(created_utc) FROM posts WHERE created_utc > 0'
    ).fetchone()
    if not row or not row[0]:
        log.error('No posts found.')
        sys.exit(1)

    start_dt  = datetime.fromtimestamp(row[0], tz=timezone.utc) - timedelta(days=5)
    end_dt    = datetime.fromtimestamp(row[1], tz=timezone.utc) + timedelta(days=35)
    end_dt    = min(end_dt, datetime.now(tz=timezone.utc) - timedelta(days=1))
    start_str = start_dt.strftime('%Y-%m-%d')
    end_str   = end_dt.strftime('%Y-%m-%d')
    log.info(f'Fetching prices for range {start_str} → {end_str}')

    tickers = [r[0] for r in conn.execute(
        'SELECT DISTINCT ticker FROM post_tickers ORDER BY ticker'
    )]
    log.info(f'{len(tickers):,} unique tickers to process')

    fetched_total = 0
    skipped       = 0
    failed        = []

    for i, ticker in enumerate(tickers):
        # Skip if we already have substantial data for this ticker in range
        existing = conn.execute(
            'SELECT COUNT(*) FROM prices WHERE ticker=? AND date>=? AND date<=?',
            (ticker, start_str, end_str),
        ).fetchone()[0]
        if existing >= 100:
            skipped += 1
            time.sleep(0.01)  # yield even when skipping
            continue

        success = False
        for attempt in range(MAX_RETRIES):
            try:
                bars = api.get_bars(ticker, '1Day', start=start_str, end=end_str).df
                if bars.empty:
                    log.debug(f'{ticker}: no data from Alpaca')
                    success = True
                    break

                rows = [
                    (ticker, str(ts)[:10],
                     float(r['open']), float(r['high']),
                     float(r['low']),  float(r['close']),
                     int(r['volume']), 'alpaca')
                    for ts, r in bars.iterrows()
                ]
                conn.executemany(
                    """INSERT OR IGNORE INTO prices
                       (ticker, date, open, high, low, close, volume, source)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    rows,
                )
                conn.commit()
                fetched_total += len(rows)
                success = True
                break

            except Exception as e:
                err_str = str(e).lower()
                if '429' in err_str or 'too many requests' in err_str:
                    wait = 60 * (attempt + 1)
                    log.warning(f'{ticker}: rate limited, waiting {wait}s...')
                    time.sleep(wait)
                else:
                    if attempt < MAX_RETRIES - 1:
                        time.sleep(2 ** attempt)
                    else:
                        log.warning(f'{ticker}: failed after {MAX_RETRIES} attempts — {e}')
                        failed.append(ticker)

        if (i + 1) % 100 == 0:
            log.info(
                f'  [{i+1}/{len(tickers)}] {fetched_total:,} price rows stored, '
                f'{skipped} skipped, {len(failed)} failed'
            )

        time.sleep(RATE_LIMIT_DELAY)

    log.info(
        f'Fetch complete: {fetched_total:,} rows stored | '
        f'{skipped} tickers skipped (had data) | {len(failed)} failed'
    )
    if failed:
        log.warning(f'Failed tickers (first 20): {failed[:20]}')

    conn.close()
    return fetched_total


if __name__ == '__main__':
    total = fetch_prices()
    print(f'\nDone. {total:,} price rows stored.')
