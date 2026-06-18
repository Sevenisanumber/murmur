#!/usr/bin/env python3
"""
Fetch live Reddit posts via the Arctic-Shift API.

Endpoint: https://arctic-shift.photon-reddit.com/api/posts/search
Per-subreddit fetch state (last-seen timestamp) is persisted in
logs/reddit_fetch_state.json so each run only pulls posts newer
than the previous run. First run defaults to a 2-hour lookback.

After inserting new posts, runs ticker extraction on those post_ids
immediately, then triggers classify_posts.py in the background
(skipped if the lock file /tmp/murmur_classify.lock already exists).

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
import urllib.parse
import urllib.request

ROOT    = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from scrapers.extract_tickers import run_extraction_for_posts

DB_PATH     = os.path.join(ROOT, 'data', 'wsb.db')
LOG_DIR     = os.path.join(ROOT, 'logs')
STATE_PATH  = os.path.join(LOG_DIR, 'reddit_fetch_state.json')

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

ARCTIC_SHIFT_URL = 'https://arctic-shift.photon-reddit.com/api/posts/search'
REQUEST_HEADERS  = {
    'User-Agent': 'Murmur/1.0 sentiment research bot (non-commercial)',
}
REQUEST_DELAY    = 1    # seconds between subreddit requests
CLASSIFY_LOCK    = '/tmp/murmur_classify.lock'
DEFAULT_LOOKBACK = 2 * 60 * 60  # 2 hours in seconds


# ── Fetch state ───────────────────────────────────────────────────────────────

def load_state() -> dict[str, int]:
    """Return {subreddit: last_fetched_utc}. Empty dict on first run."""
    try:
        with open(STATE_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(state: dict[str, int]) -> None:
    tmp = STATE_PATH + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, STATE_PATH)  # atomic write


# ── Arctic-Shift fetch ────────────────────────────────────────────────────────

def fetch_subreddit(subreddit: str, after: int) -> list[dict]:
    """
    Fetch up to 100 posts from Arctic-Shift newer than `after` (unix timestamp).
    Returns [] on any error.
    """
    params = urllib.parse.urlencode({
        'subreddit': subreddit,
        'limit':     100,
        'sort':      'desc',
        'after':     after,
    })
    url = f'{ARCTIC_SHIFT_URL}?{params}'
    req = urllib.request.Request(url, headers=REQUEST_HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
        return data.get('data', [])
    except urllib.error.HTTPError as e:
        log.error(f'[{subreddit}] HTTP {e.code}: {e.reason}')
    except urllib.error.URLError as e:
        log.error(f'[{subreddit}] Network error: {e.reason}')
    except Exception as e:
        log.error(f'[{subreddit}] Unexpected error: {e}')
    return []


# ── DB insert ─────────────────────────────────────────────────────────────────

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
                'arctic_shift',
                scraped_at,
            ),
        )
        if cur.rowcount:
            new_ids.append(p['id'])
    conn.commit()
    return new_ids


# ── Classification trigger ────────────────────────────────────────────────────

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


# ── Main run ──────────────────────────────────────────────────────────────────

def run(dry_run: bool = False, db_path: str = DB_PATH) -> int:
    """Fetch posts from all subreddits, insert new ones, extract tickers. Returns new post count."""
    log.info(f'=== fetch_reddit_posts starting | dry_run={dry_run} ===')

    state       = load_state()
    now         = int(time.time())
    default_after = now - DEFAULT_LOOKBACK

    conn = sqlite3.connect(db_path)
    conn.execute('PRAGMA journal_mode=WAL')

    total_fetched = 0
    total_new     = 0
    total_dupes   = 0
    all_new_ids: list[str] = []
    new_state: dict[str, int] = dict(state)  # carry forward existing entries

    for i, subreddit in enumerate(SUBREDDITS):
        if i > 0:
            time.sleep(REQUEST_DELAY)

        after = state.get(subreddit, default_after)
        raw   = fetch_subreddit(subreddit, after)
        if not raw:
            log.info(f'[{subreddit}] fetched=0 (no data or error)')
            continue

        fetched = len(raw)
        total_fetched += fetched

        # Advance the state cursor to the newest post seen this run
        newest_utc = max(int(p.get('created_utc', 0)) for p in raw)
        new_state[subreddit] = max(new_state.get(subreddit, 0), newest_utc)

        if dry_run:
            log.info(f'[{subreddit}] fetched={fetched} after={after} [DRY RUN — no writes]')
            continue

        new_ids = insert_posts(conn, raw)
        new     = len(new_ids)
        dupes   = fetched - new
        total_new   += new
        total_dupes += dupes
        all_new_ids.extend(new_ids)
        log.info(f'[{subreddit}] fetched={fetched} new={new} dupes={dupes} after={after}')

    if not dry_run:
        save_state(new_state)

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
    parser = argparse.ArgumentParser(description='Fetch live Reddit posts via Arctic-Shift API')
    parser.add_argument('--dry-run', action='store_true', help='Fetch only, no DB writes')
    args = parser.parse_args()
    run(dry_run=args.dry_run)


if __name__ == '__main__':
    main()
