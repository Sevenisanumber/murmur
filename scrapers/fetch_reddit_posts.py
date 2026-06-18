#!/usr/bin/env python3
"""
Fetch live Reddit posts from WSB-adjacent subreddits via the public .json endpoint.

No API key required — uses Reddit's unauthenticated JSON feed with a descriptive
User-Agent. Sleeps 2 seconds between subreddit requests to respect rate limits.

After inserting new posts, runs ticker extraction on those post_ids immediately,
then triggers classify_posts.py in the background (if not already running).

Usage:
  python scrapers/fetch_reddit_posts.py           # fetch and store new posts
  python scrapers/fetch_reddit_posts.py --dry-run # fetch only, no DB writes

Crontab (Pi — every 30 minutes during market hours, Mon–Fri):
  */30 8-16 * * 1-5 cd /home/plex/wsb-signal-lab && venv/bin/python scrapers/fetch_reddit_posts.py >> logs/reddit_live.log 2>&1
"""

import argparse
import json
import logging
import os
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT    = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from scrapers.extract_tickers import run_extraction_for_posts

DB_PATH = os.path.join(ROOT, 'data', 'wsb.db')
LOG_DIR = os.path.join(ROOT, 'logs')

os.makedirs(LOG_DIR, exist_ok=True)

LOG_PATH = os.path.join(LOG_DIR, 'reddit_live.log')
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_PATH),
    ],
)
log = logging.getLogger(__name__)

SUBREDDITS = [
    'wallstreetbets',
    'stocks',
    'investing',
    'pennystocks',
    'options',
]

REQUEST_HEADERS = {
    'User-Agent': 'Murmur/1.0 sentiment research bot (non-commercial)',
}
REQUEST_DELAY = 2   # seconds between subreddit requests to respect rate limits
CLASSIFY_LOCK = '/tmp/murmur_classify.lock'


def fetch_subreddit(subreddit: str) -> list[dict]:
    """Fetch up to 100 newest posts from a subreddit. Returns [] on any error."""
    url = f'https://old.reddit.com/r/{subreddit}/new.json?limit=100'
    req = urllib.request.Request(url, headers=REQUEST_HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
        return [child['data'] for child in data.get('data', {}).get('children', [])]
    except urllib.error.HTTPError as e:
        log.error(f'[{subreddit}] HTTP {e.code}: {e.reason}')
    except urllib.error.URLError as e:
        log.error(f'[{subreddit}] Network error: {e.reason}')
    except Exception as e:
        log.error(f'[{subreddit}] Unexpected error: {e}')
    return []


def insert_posts(conn: sqlite3.Connection, raw_posts: list[dict]) -> list[str]:
    """Insert new posts; return list of newly inserted post_ids (duplicates skipped)."""
    scraped_at = int(time.time())
    new_ids = []
    for p in raw_posts:
        cur = conn.execute(
            """INSERT OR IGNORE INTO posts
               (post_id, title, body, score, author, created_utc,
                num_comments, url, subreddit, flair, source, scraped_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                p.get('id'),
                p.get('title'),
                p.get('selftext') or '',
                p.get('score', 0),
                p.get('author'),
                int(p.get('created_utc', 0)),
                p.get('num_comments', 0),
                p.get('url'),
                p.get('subreddit'),
                p.get('link_flair_text'),
                'reddit_live',
                scraped_at,
            ),
        )
        if cur.rowcount:
            new_ids.append(p['id'])
    conn.commit()
    return new_ids


def trigger_classification(post_ids: list[str]) -> None:
    """Launch classify_posts.py --post-ids in the background if no lock file exists."""
    if os.path.exists(CLASSIFY_LOCK):
        log.info(
            f'[CLASSIFY] Lock file exists ({CLASSIFY_LOCK}) — '
            f'another classification run is in progress, skipping this cycle'
        )
        return

    python = os.path.join(ROOT, 'venv', 'bin', 'python')
    if not os.path.exists(python):
        python = sys.executable
    script = os.path.join(ROOT, 'scrapers', 'classify_posts.py')

    try:
        log_fh = open(os.path.join(LOG_DIR, 'classify_posts.log'), 'a')
        subprocess.Popen(
            [python, script, '--post-ids', ','.join(post_ids)],
            stdout=log_fh,
            stderr=subprocess.STDOUT,
        )
        log.info(f'[CLASSIFY] Launched classify_posts.py for {len(post_ids)} new posts')
    except Exception as e:
        log.error(f'[CLASSIFY] Failed to launch classify_posts.py: {e}')


def run(dry_run: bool = False, db_path: str = DB_PATH) -> int:
    """Fetch posts from all subreddits, insert new ones, extract tickers. Returns new post count."""
    log.info(f'=== fetch_reddit_posts starting | dry_run={dry_run} ===')

    conn = sqlite3.connect(db_path)
    conn.execute('PRAGMA journal_mode=WAL')

    total_fetched = 0
    total_new     = 0
    total_dupes   = 0
    all_new_ids: list[str] = []

    for i, subreddit in enumerate(SUBREDDITS):
        if i > 0:
            time.sleep(REQUEST_DELAY)

        raw = fetch_subreddit(subreddit)
        if not raw:
            continue

        fetched = len(raw)
        total_fetched += fetched

        if dry_run:
            log.info(f'[{subreddit}] fetched={fetched} [DRY RUN — no writes]')
            continue

        new_ids = insert_posts(conn, raw)
        new     = len(new_ids)
        dupes   = fetched - new
        total_new   += new
        total_dupes += dupes
        all_new_ids.extend(new_ids)
        log.info(f'[{subreddit}] fetched={fetched} new={new} dupes={dupes}')

    if not dry_run:
        if all_new_ids:
            mention_count = run_extraction_for_posts(conn, all_new_ids)
            log.info(
                f'[EXTRACT] {mention_count} ticker mentions extracted '
                f'from {len(all_new_ids)} new posts'
            )
            trigger_classification(all_new_ids)
        else:
            log.info('[EXTRACT] No new posts — skipping ticker extraction and classification')

    conn.close()
    log.info(
        f'=== fetch_reddit_posts done | '
        f'fetched={total_fetched} new={total_new} dupes={total_dupes} ==='
    )
    return total_new


def main() -> None:
    parser = argparse.ArgumentParser(description='Fetch live Reddit posts from WSB subreddits')
    parser.add_argument('--dry-run', action='store_true', help='Fetch only, no DB writes')
    args = parser.parse_args()
    run(dry_run=args.dry_run)


if __name__ == '__main__':
    main()
