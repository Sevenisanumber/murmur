#!/usr/bin/env python3
"""
Extract stock ticker mentions from WSB post titles and bodies.

Rules applied in order:
  1. Find ALL-CAPS sequences 1–5 characters long (also matches $TICKER format)
  2. Must exist in the tickers table
  3. Must not be flagged is_ambiguous=1
  4. Store mention count, extraction method, and ~20-word surrounding context

Safe to re-run: uses INSERT OR IGNORE, skips posts already in post_tickers.
"""

import re
import sqlite3
import os
import sys
import logging

ROOT     = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH  = os.path.join(ROOT, 'data', 'wsb.db')
LOG_PATH = os.path.join(ROOT, 'logs', 'extract_tickers.log')

os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[logging.StreamHandler(), logging.FileHandler(LOG_PATH)],
)
log = logging.getLogger(__name__)

# Matches ALL-CAPS words 1–5 chars; \b handles both "TSLA" and "$TSLA"
TICKER_RE  = re.compile(r'\b([A-Z]{1,5})\b')
BATCH_SIZE = 2000
CTX_CHARS  = 150  # ~20 words of surrounding context each side


def extract_from_text(text: str, valid_tickers: set) -> dict[str, tuple[int, str]]:
    """Return {ticker: (mention_count, first_surrounding_text)} for valid tickers."""
    if not text:
        return {}
    found: dict[str, list] = {}
    for m in TICKER_RE.finditer(text):
        t = m.group(1)
        if t not in valid_tickers:
            continue
        if t not in found:
            start = max(0, m.start() - CTX_CHARS)
            end   = min(len(text), m.end() + CTX_CHARS)
            found[t] = [1, text[start:end].strip()]
        else:
            found[t][0] += 1
    return {t: (c, ctx) for t, (c, ctx) in found.items()}


def run_extraction(db_path: str = DB_PATH) -> tuple[int, int]:
    conn = sqlite3.connect(db_path)
    conn.execute('PRAGMA journal_mode=WAL')

    valid_tickers = set(
        r[0] for r in conn.execute('SELECT ticker FROM tickers WHERE is_ambiguous=0')
    )
    if not valid_tickers:
        log.error('No tickers in DB. Run scrapers/load_tickers.py first.')
        sys.exit(1)
    log.info(f'Loaded {len(valid_tickers):,} valid (non-ambiguous) tickers')

    # Skip posts already processed
    processed = set(
        r[0] for r in conn.execute('SELECT DISTINCT post_id FROM post_tickers')
    )
    log.info(f'{len(processed):,} posts already processed, skipping')

    total_posts    = 0
    total_mentions = 0
    batch          = []

    cursor = conn.execute(
        'SELECT post_id, title, body FROM posts ORDER BY created_utc DESC'
    )

    for post_id, title, body in cursor:
        if post_id in processed:
            continue

        text     = ' '.join(filter(None, [title, body]))
        mentions = extract_from_text(text, valid_tickers)

        for ticker, (count, ctx) in mentions.items():
            batch.append((post_id, ticker, count, 'regex', ctx))

        total_posts += 1

        if len(batch) >= BATCH_SIZE:
            _flush(conn, batch)
            total_mentions += len(batch)
            batch = []
            if total_posts % 10000 == 0:
                log.info(f'  {total_posts:,} posts | {total_mentions:,} mentions so far...')

    if batch:
        _flush(conn, batch)
        total_mentions += len(batch)

    log.info(f'Extraction complete: {total_posts:,} posts, {total_mentions:,} ticker mentions')
    conn.close()
    return total_posts, total_mentions


def run_extraction_for_posts(conn: sqlite3.Connection, post_ids: list[str]) -> int:
    """Extract tickers from a specific list of post_ids. Returns total mention count inserted."""
    valid_tickers = set(
        r[0] for r in conn.execute('SELECT ticker FROM tickers WHERE is_ambiguous=0')
    )
    if not valid_tickers:
        log.error('No tickers in DB. Run scrapers/load_tickers.py first.')
        return 0

    placeholders = ','.join('?' * len(post_ids))
    rows = conn.execute(
        f'SELECT post_id, title, body FROM posts WHERE post_id IN ({placeholders})',
        post_ids,
    ).fetchall()

    batch = []
    for post_id, title, body in rows:
        text = ' '.join(filter(None, [title, body]))
        for ticker, (count, ctx) in extract_from_text(text, valid_tickers).items():
            batch.append((post_id, ticker, count, 'regex', ctx))

    if batch:
        _flush(conn, batch)

    return len(batch)


def _flush(conn: sqlite3.Connection, batch: list):
    conn.executemany(
        """INSERT OR IGNORE INTO post_tickers
           (post_id, ticker, mention_count, extraction_method, surrounding_text)
           VALUES (?,?,?,?,?)""",
        batch,
    )
    conn.commit()


if __name__ == '__main__':
    posts, mentions = run_extraction()
    print(f'\nDone. {posts:,} posts processed, {mentions:,} ticker mentions stored.')

    import sqlite3 as _sq
    conn = _sq.connect(DB_PATH)
    top = conn.execute(
        """SELECT ticker, COUNT(*) n FROM post_tickers
           GROUP BY ticker ORDER BY n DESC LIMIT 10"""
    ).fetchall()
    conn.close()
    print('\nTop 10 tickers by mention count:')
    for ticker, n in top:
        print(f'  {ticker:<6} {n:,}')
