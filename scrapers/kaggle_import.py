#!/usr/bin/env python3
"""
Import Kaggle 'Reddit WallStreetBets Posts' CSV into the local SQLite database.

Usage:
    python scrapers/kaggle_import.py /path/to/reddit_wsb_posts.csv

The script handles column-name variations across the common WSB Kaggle datasets,
reads the CSV in chunks to stay memory-friendly on the Pi, and deduplicates by
post_id via INSERT OR IGNORE.

Dataset to download (free):
    https://www.kaggle.com/datasets/gpreda/reddit-wallstreetsbets-posts
    (search Kaggle for "Reddit WallStreetBets Posts")
"""

import csv
import sqlite3
import os
import sys
import time
import logging

# ── paths ─────────────────────────────────────────────────────────────────────
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(ROOT, 'data', 'wsb.db')
LOG_PATH = os.path.join(ROOT, 'logs', 'kaggle_import.log')

os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[logging.StreamHandler(), logging.FileHandler(LOG_PATH)],
)
log = logging.getLogger(__name__)

CHUNK_SIZE = 10_000

# ── column mapping ─────────────────────────────────────────────────────────────
# Maps possible CSV column names → our canonical schema field names.
# First match wins when multiple aliases exist in a given dataset.
COLUMN_ALIASES = {
    'post_id':      ['id', 'post_id', 'ID'],
    'author':       ['author', 'Author'],
    'title':        ['title', 'Title'],
    'body':         ['selftext', 'body', 'text', 'Body', 'Text', 'self_text'],
    'score':        ['score', 'Score'],
    'upvote_ratio': ['upvote_ratio', 'upvote ratio'],
    'num_comments': ['num_comments', 'comms_num', 'comments', 'Comments', 'num comments'],
    'created_utc':  ['created_utc', 'created', 'timestamp', 'Date', 'date', 'created_at'],
    'url':          ['url', 'Url', 'URL', 'permalink', 'full_link'],
    'flair':        ['link_flair_text', 'flair', 'Flair', 'category'],
    'is_self':      ['is_self', 'is self'],
}

REQUIRED_FIELDS = {'post_id', 'created_utc'}


def build_column_map(header: list[str]) -> dict[str, str]:
    """Return {canonical_name: actual_csv_column} based on the CSV header."""
    header_lower = {col.strip().lower(): col for col in header}
    mapping = {}
    for canonical, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            if alias.lower() in header_lower:
                mapping[canonical] = header_lower[alias.lower()]
                break
    missing = REQUIRED_FIELDS - set(mapping)
    if missing:
        raise ValueError(
            f"CSV is missing required columns: {missing}\n"
            f"CSV has: {header}"
        )
    return mapping


def normalize_timestamp(value: str) -> int | None:
    """Convert various timestamp formats to a Unix integer."""
    if not value or value.strip() in ('', 'nan', 'None', 'null'):
        return None
    value = value.strip()
    # Already a unix timestamp (integer or float string)
    try:
        ts = float(value)
        return int(ts)
    except ValueError:
        pass
    # ISO-style date strings
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d', '%m/%d/%Y'):
        try:
            import datetime
            return int(datetime.datetime.strptime(value, fmt).timestamp())
        except ValueError:
            continue
    return None


def normalize_bool(value: str) -> int | None:
    if value is None:
        return None
    v = str(value).strip().lower()
    if v in ('1', 'true', 'yes', 't'):
        return 1
    if v in ('0', 'false', 'no', 'f', ''):
        return 0
    return None


def safe_int(value) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def safe_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def import_csv(csv_path: str, db_path: str = DB_PATH):
    if not os.path.exists(csv_path):
        log.error(f'CSV not found: {csv_path}')
        sys.exit(1)

    if not os.path.exists(db_path):
        log.error(f'Database not found at {db_path}. Run scripts/init_db.py first.')
        sys.exit(1)

    conn = sqlite3.connect(db_path)
    conn.execute('PRAGMA journal_mode=WAL')

    started_at = int(time.time())
    run_id = conn.execute(
        "INSERT INTO scrape_log (started_at, status) VALUES (?, 'running')",
        (started_at,)
    ).lastrowid
    conn.commit()

    ingested = 0
    skipped = 0
    errors = []
    col_map = None

    log.info(f'Starting Kaggle import from {csv_path}')

    try:
        file_size = os.path.getsize(csv_path)
        with open(csv_path, 'r', encoding='utf-8', errors='replace') as f:
            reader = csv.DictReader(f)
            col_map = build_column_map(reader.fieldnames or [])
            log.info(f'Column mapping: {col_map}')

            chunk = []
            chunk_num = 0

            for row in reader:
                try:
                    post_id = row.get(col_map['post_id'], '').strip()
                    author = row.get(col_map.get('author', ''), '').strip() or 'unknown'
                    created_raw = row.get(col_map.get('created_utc', ''), '')
                    created_utc = normalize_timestamp(created_raw)

                    if not post_id or created_utc is None:
                        skipped += 1
                        continue

                    record = (
                        post_id,
                        author,
                        row.get(col_map.get('title', ''), '').strip() or None,
                        row.get(col_map.get('body', ''), '').strip() or None,
                        safe_int(row.get(col_map.get('score', ''), None)),
                        safe_float(row.get(col_map.get('upvote_ratio', ''), None)),
                        safe_int(row.get(col_map.get('num_comments', ''), None)),
                        created_utc,
                        row.get(col_map.get('url', ''), '').strip() or None,
                        row.get(col_map.get('flair', ''), '').strip() or None,
                        normalize_bool(row.get(col_map.get('is_self', ''), None)),
                        started_at,
                        'kaggle',
                    )
                    chunk.append(record)

                except Exception as e:
                    errors.append(str(e))
                    skipped += 1

                if len(chunk) >= CHUNK_SIZE:
                    _flush_chunk(conn, chunk)
                    ingested += len(chunk)
                    chunk_num += 1
                    chunk = []
                    if chunk_num % 10 == 0:
                        log.info(f'  {ingested:,} rows inserted, {skipped:,} skipped...')

            if chunk:
                _flush_chunk(conn, chunk)
                ingested += len(chunk)

        _flush_authors(conn)

        status = 'success'
        error_text = '; '.join(errors[:20]) if errors else None
        log.info(f'Import complete: {ingested:,} inserted, {skipped:,} skipped, {len(errors)} row errors')

    except Exception as e:
        status = 'failure'
        error_text = str(e)
        log.error(f'Import failed: {e}')
        raise

    finally:
        conn.execute(
            "UPDATE scrape_log SET finished_at=?, posts_fetched=?, errors=?, status=? WHERE run_id=?",
            (int(time.time()), ingested, error_text, status, run_id)
        )
        conn.commit()
        conn.close()

    return ingested


def _flush_chunk(conn: sqlite3.Connection, chunk: list):
    conn.executemany(
        """INSERT OR IGNORE INTO posts
           (post_id, author, title, body, score, upvote_ratio, num_comments,
            created_utc, url, flair, is_self, scraped_at, source)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        chunk,
    )
    conn.commit()


def _flush_authors(conn: sqlite3.Connection):
    """Populate the authors table from the posts table."""
    log.info('Building authors table from imported posts...')
    conn.executescript("""
        INSERT OR IGNORE INTO authors (username, first_seen_at, last_seen_at, total_posts_scraped)
        SELECT
            author,
            MIN(created_utc),
            MAX(created_utc),
            COUNT(*)
        FROM posts
        WHERE author NOT IN ('', '[deleted]', 'AutoModerator')
        GROUP BY author;
    """)
    conn.commit()
    count = conn.execute('SELECT COUNT(*) FROM authors').fetchone()[0]
    log.info(f'Authors table: {count:,} unique authors')


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    total = import_csv(sys.argv[1])
    print(f'\nDone. {total:,} posts imported into {DB_PATH}')
    print('Run: sqlite3 data/wsb.db "SELECT COUNT(*) FROM posts;"')
