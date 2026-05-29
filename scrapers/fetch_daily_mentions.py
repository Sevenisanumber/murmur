#!/usr/bin/env python3
"""
Fetch today's ticker mention counts from YoloStocks live CSV feeds and store
them in the daily_mentions table.

Live CSV format (narrow):
    ticker, count_past_24h, total_count, last_updated

The date is extracted from the last_updated timestamp; count_past_24h is used
as the daily mention count.

Safe to re-run: uses INSERT OR REPLACE so today's counts stay current.
Runs as part of the daily pipeline (scripts/run_daily.py).
"""

import csv
import io
import os
import sqlite3
import logging
import urllib.request
from datetime import datetime

ROOT     = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH  = os.path.join(ROOT, 'data', 'wsb.db')
LOG_PATH = os.path.join(ROOT, 'logs', 'fetch_daily_mentions.log')

os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[logging.StreamHandler(), logging.FileHandler(LOG_PATH)],
)
log = logging.getLogger(__name__)

SOURCES = {
    'wallstreetbets': 'https://yolostocks.live/downloads/wallstreetbets.csv',
    'stocks':         'https://yolostocks.live/downloads/stocks.csv',
    'investing':      'https://yolostocks.live/downloads/investing.csv',
    'pennystocks':    'https://yolostocks.live/downloads/pennystocks.csv',
    'options':        'https://yolostocks.live/downloads/options.csv',
}

TIMEOUT = 30


def _download(url: str) -> str | None:
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'WSBSignalLab/1.0'})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return resp.read().decode('utf-8', errors='replace')
    except Exception as e:
        log.error(f'Download failed for {url}: {e}')
        return None


def _parse_date(last_updated: str) -> str | None:
    """Extract YYYY-MM-DD from 'YYYY-MM-DD HH:MM:SS' or similar."""
    if not last_updated:
        return None
    try:
        return last_updated.strip().split()[0]
    except IndexError:
        return None


def fetch_one(conn: sqlite3.Connection, subreddit: str, url: str) -> int:
    log.info(f'Fetching {subreddit} from {url}')
    text = _download(url)
    if not text:
        return 0

    reader = csv.DictReader(io.StringIO(text))
    batch  = []
    date   = None

    for row in reader:
        ticker    = row.get('ticker', '').strip().upper()
        count_raw = row.get('count_past_24h', '').strip()
        ts        = row.get('last_updated', '').strip()

        if not ticker:
            continue

        try:
            count = int(count_raw)
        except ValueError:
            count = 0
        if count <= 0:
            continue

        row_date = _parse_date(ts)
        if row_date and date is None:
            date = row_date

        batch.append((ticker, row_date or datetime.now().strftime('%Y-%m-%d'), count, subreddit))

    if not batch:
        log.info(f'  No data rows for {subreddit}')
        return 0

    # INSERT OR REPLACE so today's snapshot stays fresh on repeated runs
    conn.executemany(
        """INSERT OR REPLACE INTO daily_mentions (ticker, date, mention_count, subreddit)
           VALUES (?,?,?,?)""",
        batch,
    )
    conn.commit()
    log.info(f'  {len(batch):,} rows upserted for {subreddit} (date={date})')
    return len(batch)


def fetch_daily_mentions(db_path: str = DB_PATH) -> int:
    if not os.path.exists(db_path):
        log.error(f'DB not found: {db_path}. Run scripts/init_db.py first.')
        import sys; sys.exit(1)

    conn  = sqlite3.connect(db_path)
    conn.execute('PRAGMA journal_mode=WAL')
    total = 0

    for subreddit, url in SOURCES.items():
        try:
            total += fetch_one(conn, subreddit, url)
        except Exception as e:
            log.error(f'Error processing {subreddit}: {e}')

    conn.close()
    log.info(f'fetch_daily_mentions complete: {total:,} total rows upserted')
    return total


if __name__ == '__main__':
    total = fetch_daily_mentions()
    print(f'\nDone. {total:,} rows upserted into daily_mentions.')
