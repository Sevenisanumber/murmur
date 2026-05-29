#!/usr/bin/env python3
"""
Import YoloStocks archive CSVs into the daily_mentions table.

Archive layout:
    archive/{year}/{subreddit}_{year}.csv

CSV format (wide): ticker, overall_rank, M/D/YY, ..., total
Each date column value is the mention count for that ticker on that day.

Safe to re-run: uses INSERT OR IGNORE, skips rows already present.
"""

import csv
import os
import sqlite3
import logging
from datetime import datetime

ROOT     = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH  = os.path.join(ROOT, 'data', 'wsb.db')
LOG_PATH = os.path.join(ROOT, 'logs', 'import_archive.log')

os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[logging.StreamHandler(), logging.FileHandler(LOG_PATH)],
)
log = logging.getLogger(__name__)

SKIP_COLS = {'ticker', 'overall_rank', 'total'}


def parse_date_col(col: str) -> str | None:
    """Convert 'M/D/YY' column header to 'YYYY-MM-DD', or None if not a date."""
    try:
        return datetime.strptime(col.strip(), '%m/%d/%y').strftime('%Y-%m-%d')
    except ValueError:
        return None


def import_file(conn: sqlite3.Connection, fpath: str, subreddit: str) -> int:
    inserted = 0
    batch    = []

    with open(fpath, 'r', encoding='utf-8', errors='replace') as f:
        reader  = csv.DictReader(f)
        headers = reader.fieldnames or []

        # Pre-parse date columns once per file
        date_map = {}  # col_header -> YYYY-MM-DD
        for col in headers:
            if col in SKIP_COLS:
                continue
            d = parse_date_col(col)
            if d:
                date_map[col] = d

        if not date_map:
            log.warning(f'No parseable date columns in {fpath}')
            return 0

        for row in reader:
            ticker = row.get('ticker', '').strip().upper()
            if not ticker:
                continue

            for col, date in date_map.items():
                raw = row.get(col, '').strip()
                try:
                    count = int(raw)
                except ValueError:
                    count = 0
                if count <= 0:
                    continue
                batch.append((ticker, date, count, subreddit))

            if len(batch) >= 50_000:
                inserted += _flush(conn, batch)
                batch = []

    if batch:
        inserted += _flush(conn, batch)

    return inserted


def _flush(conn: sqlite3.Connection, batch: list) -> int:
    conn.executemany(
        """INSERT OR IGNORE INTO daily_mentions (ticker, date, mention_count, subreddit)
           VALUES (?,?,?,?)""",
        batch,
    )
    conn.commit()
    return len(batch)


def import_archive(db_path: str = DB_PATH) -> int:
    if not os.path.exists(db_path):
        log.error(f'DB not found: {db_path}. Run scripts/init_db.py first.')
        import sys; sys.exit(1)

    conn = sqlite3.connect(db_path)
    conn.execute('PRAGMA journal_mode=WAL')

    archive_dir = os.path.join(ROOT, 'archive')
    total = 0

    for year_name in sorted(os.listdir(archive_dir)):
        year_path = os.path.join(archive_dir, year_name)
        if not os.path.isdir(year_path):
            continue

        for fname in sorted(os.listdir(year_path)):
            if not fname.endswith('.csv'):
                continue
            # wallstreetbets_2021.csv -> subreddit = wallstreetbets
            subreddit = fname.rsplit('_', 1)[0]
            fpath     = os.path.join(year_path, fname)

            log.info(f'Importing {fname} (subreddit={subreddit})...')
            n = import_file(conn, fpath, subreddit)
            log.info(f'  {n:,} rows inserted')
            total += n

    conn.close()
    log.info(f'Archive import complete: {total:,} total rows inserted')
    return total


if __name__ == '__main__':
    total = import_archive()
    print(f'\nDone. {total:,} daily_mentions rows imported from archive.')
