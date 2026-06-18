#!/usr/bin/env python3
"""
Classify WSB posts using Mistral via Ollama.

Labels:
  thesis        — investment thesis / DD / reasoned analysis about why a stock moves
  hype          — cheerleading, FOMO, moon emojis, no real analysis
  loss_porn     — sharing portfolio losses or post-mortem on a bad trade
  news_reaction — reaction to a specific news event, earnings report, or market event
  options_yolo  — sharing a specific options position, YOLO bet, or trade screenshot
  meme          — memes, jokes, off-topic humor, non-investment content

Usage:
  # Start Ollama first:  ollama serve
  python3 scrapers/classify_posts.py --test            # 100-post test batch
  python3 scrapers/classify_posts.py                   # full dataset
  python3 scrapers/classify_posts.py --year 2020       # only 2020 posts
  python3 scrapers/classify_posts.py --batch-size 25
"""

import argparse
import json
import logging
import os
import re
import sqlite3
import time
import urllib.error
import urllib.request

ROOT       = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH    = os.path.join(ROOT, 'data', 'wsb.db')
LOCK_PATH  = '/tmp/murmur_classify.lock'
LOG_PATH   = os.path.join(ROOT, 'logs', 'classify_posts.log')
OLLAMA_URL = 'http://localhost:11434/api/generate'
MODEL      = 'mistral'

VALID_LABELS = {'thesis', 'hype', 'loss_porn', 'news_reaction', 'options_yolo', 'meme'}

# Common Mistral responses that aren't exact label names but map cleanly to one.
# Anything not here and not in VALID_LABELS gets stored as "other".
LABEL_ALIASES = {
    # thesis
    'dd': 'thesis', 'analysis': 'thesis', 'due_diligence': 'thesis',
    'due diligence': 'thesis', 'research': 'thesis', 'bullish': 'thesis',
    'bearish': 'thesis', 'invest': 'thesis', 'investment': 'thesis',
    # hype
    'pump': 'hype', 'moon': 'hype', 'rocket': 'hype', 'fomo': 'hype',
    'shilling': 'hype', 'shill': 'hype',
    # loss_porn
    'loss': 'loss_porn', 'losses': 'loss_porn', 'rekt': 'loss_porn',
    'loss porn': 'loss_porn', 'gain_porn': 'loss_porn', 'gain porn': 'loss_porn',
    # news_reaction
    'news': 'news_reaction', 'earnings': 'news_reaction',
    'announcement': 'news_reaction', 'breaking': 'news_reaction',
    # options_yolo
    'yolo': 'options_yolo', 'options': 'options_yolo', 'trade': 'options_yolo',
    'calls': 'options_yolo', 'puts': 'options_yolo', 'position': 'options_yolo',
    'positions': 'options_yolo', 'bet': 'options_yolo',
    # meme
    'discussion': 'meme', 'joke': 'meme', 'funny': 'meme',
    'humor': 'meme', 'satire': 'meme', 'memes': 'meme', 'off-topic': 'meme',
    'offtopic': 'meme', 'other': 'meme',
}

STATUS_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           'logs', 'classify_status.json')

PROMPT_TEMPLATE = """\
Classify this r/wallstreetbets post. Respond with ONLY a JSON object on a single line — no explanation, no markdown.

Categories:
  thesis        — investment thesis, DD, or reasoned analysis about why a stock will move
  hype          — cheerleading, FOMO, rocket emojis, no real analysis
  loss_porn     — sharing portfolio losses, loss screenshots, or bad-trade post-mortem
  news_reaction — reacting to a specific news event, earnings, or market event
  options_yolo  — sharing a specific options position, YOLO trade, or trade screenshot
  meme          — memes, jokes, off-topic humor, non-investment content

JSON fields:
  "category"   — exactly one category name from the list above
  "is_bullish" — 1 if the post is bullish on the ticker(s) mentioned, 0 if bearish, null if neutral or unclear

Example: {{"category": "thesis", "is_bullish": 1}}

Post title: {title}
Post body: {body}

JSON:"""

os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[logging.StreamHandler(), logging.FileHandler(LOG_PATH)],
)
log = logging.getLogger(__name__)


def check_ollama():
    try:
        req = urllib.request.Request('http://localhost:11434/api/tags')
        with urllib.request.urlopen(req, timeout=5):
            pass
        return True
    except Exception:
        return False


def add_classification_column(conn):
    cols = {row[1] for row in conn.execute("PRAGMA table_info(posts)")}
    if 'classification' not in cols:
        conn.execute("ALTER TABLE posts ADD COLUMN classification TEXT")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_posts_classification ON posts(classification)"
        )
        log.info("Added 'classification' column to posts table")
    if 'is_bullish' not in cols:
        conn.execute("ALTER TABLE posts ADD COLUMN is_bullish INTEGER")
        log.info("Added 'is_bullish' column to posts table")
    conn.commit()


def fetch_unclassified(conn, limit=None, year=None, priority=False):
    score_filter = "" if priority else "AND p.score >= 10"
    sql = f"""
        SELECT p.post_id, p.title, p.body
        FROM posts p
        WHERE p.classification IS NULL
          {score_filter}
          AND EXISTS (SELECT 1 FROM post_tickers pt WHERE pt.post_id = p.post_id)
    """
    params = []
    if year is not None:
        sql += " AND strftime('%Y', datetime(p.created_utc, 'unixepoch')) = ?"
        params.append(str(year))
    if priority:
        sql += " ORDER BY p.score DESC"
    if limit:
        sql += f" LIMIT {limit}"
    return conn.execute(sql, params).fetchall()


def count_unclassified(conn, priority=False) -> int:
    """Count posts that still need classification.

    priority=True matches the --priority-unclassified universe (no score floor).
    priority=False matches the default universe (score >= 10).
    """
    score_filter = "" if priority else "AND score >= 10"
    return conn.execute(
        f"""SELECT COUNT(*) FROM posts
            WHERE classification IS NULL
              {score_filter}
              AND EXISTS (SELECT 1 FROM post_tickers pt WHERE pt.post_id = posts.post_id)"""
    ).fetchone()[0]


def call_ollama(prompt, retries=3):
    payload = json.dumps({
        'model': MODEL,
        'prompt': prompt,
        'stream': False,
        'options': {'temperature': 0.0, 'num_predict': 40},
    }).encode()

    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                OLLAMA_URL,
                data=payload,
                headers={'Content-Type': 'application/json'},
                method='POST',
            )
            with urllib.request.urlopen(req, timeout=180) as resp:
                data = json.loads(resp.read())
            return data.get('response', '').strip()
        except urllib.error.URLError as e:
            log.warning("Ollama request failed (attempt %d/%d): %s", attempt + 1, retries, e)
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
    return None


def resolve_label(raw):
    """Map a raw Mistral response to a valid label, alias, or 'other'."""
    if not raw:
        return 'other'
    cleaned = raw.lower().strip().rstrip('.,;:')
    first_word = cleaned.split()[0].rstrip('.,;:') if cleaned.split() else ''

    # Exact match on full cleaned string or first word
    for candidate in (cleaned, first_word):
        if candidate in VALID_LABELS:
            return candidate
        if candidate in LABEL_ALIASES:
            return LABEL_ALIASES[candidate]

    # Substring scan: first valid label or alias found in the response wins
    for valid in VALID_LABELS:
        if valid in cleaned:
            return valid
    for alias, mapped in LABEL_ALIASES.items():
        if alias in cleaned:
            return mapped

    return 'other'


def parse_is_bullish(value):
    """Convert the is_bullish field from JSON to 1, 0, or None."""
    if value == 1 or value is True:
        return 1
    if value == 0 or value is False:
        return 0
    return None  # null / missing / anything else → neutral


def classify_post(title, body):
    """
    Returns (label, is_bullish) where is_bullish is 1, 0, or None.
    Returns (None, None) on API failure.
    """
    title = (title or '').strip()
    body = (body or '').strip()[:600]
    prompt = PROMPT_TEMPLATE.format(title=title, body=body)

    raw = call_ollama(prompt)
    if raw is None:
        return None, None  # API failure — caller skips this post

    # Try to parse the JSON response
    label = None
    is_bullish = None
    json_match = re.search(r'\{[^}]+\}', raw)
    if json_match:
        try:
            parsed = json.loads(json_match.group())
            label = resolve_label(parsed.get('category', ''))
            is_bullish = parse_is_bullish(parsed.get('is_bullish'))
        except (json.JSONDecodeError, ValueError):
            pass

    # Fall back: treat the whole response as a plain-text label
    if label is None:
        label = resolve_label(raw)

    if label == 'other':
        log.warning("Unmapped label %r → 'other'  title=%r", raw.strip(), title[:60])

    return label, is_bullish


def fmt_eta(seconds):
    """Format seconds as 'Xh Ym' or 'Ym' for display."""
    seconds = int(seconds)
    h, m = divmod(seconds // 60, 60)
    return f"{h}h {m:02d}m" if h else f"{m}m"


def write_status(started_at, done, total, label_counts, failed, rate_per_min):
    remaining = total - done
    if rate_per_min > 0:
        eta_seconds = (remaining / rate_per_min) * 60
        estimated_completion = time.strftime(
            '%Y-%m-%dT%H:%M:%S', time.localtime(time.time() + eta_seconds)
        )
    else:
        estimated_completion = None

    status = {
        'started_at': time.strftime('%Y-%m-%dT%H:%M:%S', time.localtime(started_at)),
        'posts_done': done,
        'posts_remaining': remaining,
        'estimated_completion': estimated_completion,
        'current_rate_per_min': round(rate_per_min, 2),
        'label_distribution': dict(sorted(label_counts.items())),
        'api_failures': failed,
    }
    tmp = STATUS_PATH + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(status, f, indent=2)
    os.replace(tmp, STATUS_PATH)  # atomic write


def run(test_mode=False, batch_size=50, year=None, limit=None, priority=False):
    if not check_ollama():
        log.error(
            "Ollama is not running. Start it with:\n"
            "  OLLAMA_MODELS=/mnt/ollama/models ollama serve"
        )
        return

    conn = sqlite3.connect(DB_PATH)
    conn.execute('PRAGMA journal_mode=WAL')

    add_classification_column(conn)

    effective_limit = 100 if test_mode else limit
    posts = fetch_unclassified(conn, limit=effective_limit, year=year, priority=priority)
    total = len(posts)
    year_str = f"year={year}" if year else "all years"
    mode_str = "priority" if priority else "default order"
    log.info("Found %d unclassified posts (test_mode=%s, batch_size=%d, %s, %s, limit=%s)",
             total, test_mode, batch_size, year_str, mode_str, effective_limit)

    if total == 0:
        log.info("Nothing to classify — exiting")
        conn.close()
        return

    classified = 0
    failed = 0
    label_counts = {k: 0 for k in VALID_LABELS}
    label_counts['other'] = 0
    pending = []  # (label, post_id) pairs not yet committed
    start = time.time()

    for done, (post_id, title, body) in enumerate(posts, start=1):
        label, is_bullish = classify_post(title, body)
        if label is not None:
            pending.append((label, is_bullish, post_id))
            classified += 1
            label_counts[label] = label_counts.get(label, 0) + 1
        else:
            failed += 1

        # Commit every batch_size posts
        if done % batch_size == 0 or done == total:
            conn.executemany(
                "UPDATE posts SET classification=?, is_bullish=? WHERE post_id=?",
                pending,
            )
            conn.commit()
            pending.clear()

        elapsed = time.time() - start
        rate_per_min = (done / elapsed) * 60 if elapsed > 0 else 0
        remaining = total - done
        eta_secs = (remaining / rate_per_min) * 60 if rate_per_min > 0 else 0

        # Log every 10 posts
        if done % 10 == 0 or done == total:
            dist = '  '.join(f"{k}:{v}" for k, v in sorted(label_counts.items()) if v > 0)
            log.info(
                "[%d/%d]  ok=%d fail=%d  %.1f/min  ETA=%s  |  %s",
                done, total, classified, failed,
                rate_per_min, fmt_eta(eta_secs), dist,
            )

        # Write status JSON every 100 posts
        if done % 100 == 0 or done == total:
            write_status(start, done, total, label_counts, failed, rate_per_min)

    elapsed = time.time() - start
    log.info(
        "Finished. classified=%d  failed=%d  total=%d  elapsed=%s",
        classified, failed, total, fmt_eta(elapsed),
    )
    log.info("Label distribution: %s", {k: v for k, v in sorted(label_counts.items()) if v > 0})

    remaining = count_unclassified(conn, priority=priority)
    log.info("Unclassified posts remaining: %d", remaining)

    conn.close()


def run_for_post_ids(post_ids: list[str], batch_size: int = 50) -> None:
    """Classify a specific set of post_ids. Skips posts already classified or without tickers."""
    if not check_ollama():
        log.error(
            "Ollama is not running. Start it with:\n"
            "  OLLAMA_MODELS=/mnt/ollama/models ollama serve"
        )
        return

    conn = sqlite3.connect(DB_PATH)
    conn.execute('PRAGMA journal_mode=WAL')
    add_classification_column(conn)

    placeholders = ','.join('?' * len(post_ids))
    posts = conn.execute(
        f"""SELECT p.post_id, p.title, p.body FROM posts p
            WHERE p.post_id IN ({placeholders})
              AND p.classification IS NULL
              AND EXISTS (SELECT 1 FROM post_tickers pt WHERE pt.post_id = p.post_id)""",
        post_ids,
    ).fetchall()

    if not posts:
        log.info(f'[POST-IDS] No unclassified posts with tickers among {len(post_ids)} IDs — nothing to do')
        conn.close()
        return

    log.info(f'[POST-IDS] Classifying {len(posts)} of {len(post_ids)} posts')

    classified = 0
    failed = 0
    label_counts: dict[str, int] = {}
    pending = []

    for done, (post_id, title, body) in enumerate(posts, start=1):
        label, is_bullish = classify_post(title, body)
        if label is not None:
            pending.append((label, is_bullish, post_id))
            classified += 1
            label_counts[label] = label_counts.get(label, 0) + 1
        else:
            failed += 1

        if done % batch_size == 0 or done == len(posts):
            conn.executemany(
                "UPDATE posts SET classification=?, is_bullish=? WHERE post_id=?",
                pending,
            )
            conn.commit()
            pending.clear()

    log.info(
        f'[POST-IDS] Done. classified={classified} failed={failed} | '
        + ' '.join(f'{k}:{v}' for k, v in sorted(label_counts.items()) if v > 0)
    )
    conn.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Classify WSB posts via Mistral/Ollama')
    parser.add_argument('--test', action='store_true', help='Run on first 100 unclassified posts only')
    parser.add_argument('--batch-size', type=int, default=50, metavar='N',
                        help='DB commit interval (default 50)')
    parser.add_argument('--year', type=int, metavar='YYYY',
                        help='Only classify posts from this year (e.g. --year 2020)')
    parser.add_argument('--priority-unclassified', action='store_true',
                        help='Process posts ordered by score DESC (most-upvoted first)')
    parser.add_argument('--limit', type=int, default=None, metavar='N',
                        help='Cap the run at N posts (default: all unclassified)')
    parser.add_argument('--post-ids', type=str, default=None, metavar='IDS',
                        help='Comma-separated post_ids to classify (skips all other filters)')
    args = parser.parse_args()

    # Acquire lock file — prevent overlapping classification runs
    try:
        with open(LOCK_PATH, 'x') as f:
            f.write(str(os.getpid()))
    except FileExistsError:
        log.warning(f'Lock file exists ({LOCK_PATH}) — another classify_posts.py is already running, exiting')
        sys.exit(0)

    try:
        if args.post_ids:
            run_for_post_ids(
                post_ids=args.post_ids.split(','),
                batch_size=args.batch_size,
            )
        else:
            run(
                test_mode=args.test,
                batch_size=args.batch_size,
                year=args.year,
                limit=args.limit,
                priority=args.priority_unclassified,
            )
    finally:
        try:
            os.unlink(LOCK_PATH)
        except OSError:
            pass
