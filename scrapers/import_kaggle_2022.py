#!/usr/bin/env python3
"""
Import wallstreetbets_2022.csv (Kaggle) into the posts table.

Column mapping:
  id        → post_id
  title     → title
  score     → score
  body      → body
  url       → url
  comms_num → num_comments
  created   → created_utc  (unix timestamp float)
  source    → 'kaggle_2022' (hardcoded)
  subreddit → 'wallstreetbets' (hardcoded)

Uses INSERT OR IGNORE to skip duplicate post_ids.
Reads in chunks of 5000 rows to keep memory usage flat on large files.
Commits every 5000 rows.

Usage:
  python scrapers/import_kaggle_2022.py
  python scrapers/import_kaggle_2022.py --csv /path/to/wallstreetbets_2022.csv
"""

import argparse
import csv
import logging
import os
import sqlite3
import time

ROOT    = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(ROOT, 'data', 'wsb.db')

DEFAULT_CSV = os.path.expanduser(
    '~/Documents/Claude/Projects/Trading Bot LLM/WSB Kaggle Data/wallstreetbets_2022.csv'
)

LOG_PATH = os.path.join(ROOT, 'logs', 'import_kaggle_2022.log')
os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[logging.StreamHandler(), logging.FileHandler(LOG_PATH)],
)
log = logging.getLogger(__name__)

CHUNK_SIZE    = 5000
LOG_INTERVAL  = 10_000
INSERT_SQL    = """
    INSERT OR IGNORE INTO posts
        (post_id, title, score, body, url, num_comments,
         created_utc, source, subreddit)
    VALUES (?, ?, ?, ?, ?, ?, ?, 'kaggle_2022', 'wallstreetbets')
"""


def _to_int(val, default=None):
    try:
        return int(float(val))
    except (TypeError, ValueError):
        return default


def run(csv_path: str = DEFAULT_CSV, db_path: str = DB_PATH) -> tuple[int, int]:
    """
    Import the CSV and return (rows_inserted, rows_skipped).
    """
    if not os.path.exists(csv_path):
        log.error(f'CSV not found: {csv_path}')
        raise FileNotFoundError(csv_path)

    conn = sqlite3.connect(db_path)
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA synchronous=NORMAL')

    log.info(f'Starting import: {csv_path}')
    start       = time.time()
    total_read  = 0
    inserted    = 0
    chunk: list = []

    with open(csv_path, newline='', encoding='utf-8', errors='replace') as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            total_read += 1
            chunk.append((
                row.get('id', '').strip() or None,
                row.get('title', '').strip() or None,
                _to_int(row.get('score')),
                row.get('body', '').strip() or None,
                row.get('url', '').strip() or None,
                _to_int(row.get('comms_num')),
                _to_int(row.get('created')),   # unix timestamp float → int
            ))

            if len(chunk) >= CHUNK_SIZE:
                cur = conn.executemany(INSERT_SQL, chunk)
                inserted += cur.rowcount
                conn.commit()
                chunk = []

            if total_read % LOG_INTERVAL == 0:
                skipped = total_read - inserted
                elapsed = time.time() - start
                log.info(
                    f'Progress: {total_read:,} rows read | '
                    f'{inserted:,} inserted | {skipped:,} skipped | '
                    f'{elapsed:.0f}s elapsed'
                )

    # flush remainder
    if chunk:
        cur = conn.executemany(INSERT_SQL, chunk)
        inserted += cur.rowcount
        conn.commit()

    conn.close()

    skipped = total_read - inserted
    elapsed = time.time() - start
    log.info(
        f'Done. {total_read:,} rows read | '
        f'{inserted:,} inserted | {skipped:,} skipped | '
        f'{elapsed:.1f}s total'
    )
    return inserted, skipped


def main() -> None:
    parser = argparse.ArgumentParser(description='Import wallstreetbets_2022.csv into posts table')
    parser.add_argument('--csv', default=DEFAULT_CSV, help='Path to CSV file')
    parser.add_argument('--db',  default=DB_PATH,     help='Path to SQLite DB')
    args = parser.parse_args()
    run(csv_path=args.csv, db_path=args.db)


if __name__ == '__main__':
    main()
