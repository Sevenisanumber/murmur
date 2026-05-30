#!/usr/bin/env python3
"""
One-time fix: add posts.subreddit column and backfill from leukipp CSV files.

Each leukipp CSV file corresponds to exactly one subreddit (confirmed: zero
cross-file duplicate post_ids). Streams each file to avoid loading it fully
into memory, then does batch UPDATEs of BATCH_SIZE rows at a time.

Also sets subreddit='wallstreetbets' on kaggle-source rows since that dataset
is WSB-only.

Usage:
    python3 scripts/fix_leukipp_subreddit.py
    python3 scripts/fix_leukipp_subreddit.py --dry-run   # show counts, no writes
"""

import argparse
import csv
import logging
import os
import sqlite3
import sys

ROOT    = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(ROOT, 'data', 'wsb.db')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[logging.StreamHandler()],
)
log = logging.getLogger(__name__)

BATCH_SIZE = 10_000

LEUKIPP_FILES = [
    ('leukipp_wsb.csv',        'wallstreetbets'),
    ('leukipp_gme.csv',        'gamestop'),
    ('leukipp_stocks.csv',     'stocks'),
    ('leukipp_options.csv',    'options'),
    ('leukipp_pennystocks.csv','pennystocks'),
    ('leukipp_investing.csv',  'investing'),
]


def add_subreddit_column(conn: sqlite3.Connection) -> None:
    existing = {row[1] for row in conn.execute('PRAGMA table_info(posts)')}
    if 'subreddit' in existing:
        log.info('Column posts.subreddit already exists')
    else:
        conn.execute('ALTER TABLE posts ADD COLUMN subreddit TEXT')
        conn.commit()
        log.info('Added column posts.subreddit TEXT')


def backfill_from_csv(
    conn: sqlite3.Connection,
    csv_path: str,
    subreddit: str,
    dry_run: bool,
) -> int:
    updated = 0
    batch: list[str] = []

    with open(csv_path, encoding='utf-8', errors='replace') as f:
        reader = csv.DictReader(f)
        for row in reader:
            pid = row.get('id', '').strip()
            if not pid:
                continue
            batch.append(pid)
            if len(batch) >= BATCH_SIZE:
                updated += _flush_update(conn, batch, subreddit, dry_run)
                batch = []

    if batch:
        updated += _flush_update(conn, batch, subreddit, dry_run)

    return updated


def _flush_update(
    conn: sqlite3.Connection,
    ids: list[str],
    subreddit: str,
    dry_run: bool,
) -> int:
    if dry_run:
        # Count how many would actually change
        ph = ','.join('?' * len(ids))
        n = conn.execute(
            f"SELECT COUNT(*) FROM posts WHERE post_id IN ({ph}) AND source='leukipp'",
            ids,
        ).fetchone()[0]
        return n

    ph = ','.join('?' * len(ids))
    cur = conn.execute(
        f"UPDATE posts SET subreddit=? WHERE post_id IN ({ph}) AND source='leukipp'",
        [subreddit] + ids,
    )
    conn.commit()
    return cur.rowcount


def fix_kaggle_subreddit(conn: sqlite3.Connection, dry_run: bool) -> int:
    """Set subreddit='wallstreetbets' for all kaggle-source posts (WSB-only dataset)."""
    if dry_run:
        try:
            n = conn.execute(
                "SELECT COUNT(*) FROM posts WHERE source='kaggle' AND subreddit IS NULL"
            ).fetchone()[0]
        except sqlite3.OperationalError:
            n = conn.execute(
                "SELECT COUNT(*) FROM posts WHERE source='kaggle'"
            ).fetchone()[0]
        return n
    cur = conn.execute(
        "UPDATE posts SET subreddit='wallstreetbets' WHERE source='kaggle'"
    )
    conn.commit()
    return cur.rowcount


def main() -> None:
    parser = argparse.ArgumentParser(description='Backfill posts.subreddit from leukipp CSVs')
    parser.add_argument('--dry-run', action='store_true',
                        help='Count affected rows without writing')
    parser.add_argument('--db', default=DB_PATH)
    args = parser.parse_args()

    if not os.path.exists(args.db):
        log.error(f'DB not found: {args.db}')
        sys.exit(1)

    conn = sqlite3.connect(args.db)
    conn.execute('PRAGMA journal_mode=WAL')

    # ── Before state ──────────────────────────────────────────────────────────
    total = conn.execute('SELECT COUNT(*) FROM posts').fetchone()[0]
    leukipp_total = conn.execute(
        "SELECT COUNT(*) FROM posts WHERE source='leukipp'"
    ).fetchone()[0]
    existing_cols = {row[1] for row in conn.execute('PRAGMA table_info(posts)')}
    has_col = 'subreddit' in existing_cols
    already_set = conn.execute(
        'SELECT COUNT(*) FROM posts WHERE subreddit IS NOT NULL'
    ).fetchone()[0] if has_col else 0
    log.info(f'Posts: {total:,} total | {leukipp_total:,} leukipp | {already_set:,} already have subreddit')

    if not args.dry_run:
        add_subreddit_column(conn)
    else:
        log.info('[DRY RUN] skipping ALTER TABLE')

    # ── Backfill leukipp files ────────────────────────────────────────────────
    data_dir = os.path.join(ROOT, 'data')
    total_updated = 0

    for fname, subreddit in LEUKIPP_FILES:
        csv_path = os.path.join(data_dir, fname)
        if not os.path.exists(csv_path):
            log.warning(f'CSV not found, skipping: {csv_path}')
            continue

        log.info(f'Processing {fname} → subreddit={subreddit}')
        n = backfill_from_csv(conn, csv_path, subreddit, dry_run=args.dry_run)
        log.info(f'  {"would update" if args.dry_run else "updated"} {n:,} rows')
        total_updated += n

    # ── Kaggle rows ───────────────────────────────────────────────────────────
    log.info("Setting subreddit='wallstreetbets' for kaggle-source posts")
    n_kaggle = fix_kaggle_subreddit(conn, dry_run=args.dry_run)
    log.info(f'  {"would update" if args.dry_run else "updated"} {n_kaggle:,} kaggle rows')

    # ── After state ───────────────────────────────────────────────────────────
    if not args.dry_run:
        after_set = conn.execute(
            'SELECT COUNT(*) FROM posts WHERE subreddit IS NOT NULL'
        ).fetchone()[0]
        after_null = total - after_set
        log.info(
            f'Done: {after_set:,} posts now have subreddit | {after_null:,} still NULL | '
            f'{total_updated:,} leukipp rows updated + {n_kaggle:,} kaggle rows updated'
        )
        # Show breakdown
        log.info('Subreddit distribution in posts:')
        for row in conn.execute(
            "SELECT subreddit, COUNT(*) n FROM posts GROUP BY subreddit ORDER BY n DESC"
        ):
            log.info(f'  {str(row[0]):<20} {row[1]:>10,}')
    else:
        log.info(
            f'[DRY RUN] would update {total_updated:,} leukipp rows + {n_kaggle:,} kaggle rows'
        )

    conn.close()


if __name__ == '__main__':
    main()
