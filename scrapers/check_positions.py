#!/usr/bin/env python3
"""
Intraday position monitor — WSB Signal Lab

Checks all open paper_trades positions and executes exits when conditions are met.
Designed to run every 30 minutes during market hours via cron; exits silently
if the market is closed (handles weekends, holidays, pre/post-market automatically).

Exit conditions (same thresholds as paper_trader.py):
  take_profit : unrealized gain >= +15%
  stop_loss   : unrealized loss >= -8%
  time_exit   : position held >= 7 calendar days

Logs every check and any exits to logs/paper_trades.log using the same
format as paper_trader.py so the combined log tells a coherent story.

Usage:
  python scrapers/check_positions.py           # normal run
  python scrapers/check_positions.py --dry-run # check without placing sell orders

Cron (ET, weekdays):
  */30 9-15 * * 1-5  cd /project && python3 scrapers/check_positions.py
  0 16   * * 1-5     cd /project && python3 scrapers/check_positions.py
"""

import argparse
import logging
import os
import sqlite3
import sys
from datetime import datetime, timezone

import pytz

ROOT    = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(ROOT, 'data', 'wsb.db')
LOG_DIR = os.path.join(ROOT, 'logs')

os.makedirs(LOG_DIR, exist_ok=True)

# Append to the same log file as paper_trader so the full history is in one place
LOG_PATH = os.path.join(LOG_DIR, 'paper_trades.log')
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_PATH),
    ],
)
log = logging.getLogger(__name__)

ET = pytz.timezone('America/New_York')
MARKET_OPEN  = (9, 30)   # 9:30 AM ET
MARKET_CLOSE = (16, 0)   # 4:00 PM ET


def local_market_check() -> bool:
    """
    Fast local check: is it currently a weekday between 9:30am and 4:00pm ET?
    Returns False on weekends and outside trading window — no API call needed.
    This is a pre-filter only; market_is_open() does the authoritative check.
    """
    now_et = datetime.now(tz=ET)
    if now_et.weekday() >= 5:   # Saturday=5, Sunday=6
        return False
    t = (now_et.hour, now_et.minute)
    return MARKET_OPEN <= t <= MARKET_CLOSE


def count_open_positions(conn: sqlite3.Connection) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM paper_trades WHERE status = 'open'"
    ).fetchone()[0]


def main() -> None:
    parser = argparse.ArgumentParser(description='WSB intraday position monitor')
    parser.add_argument('--dry-run', action='store_true',
                        help='Check positions without placing sell orders')
    args = parser.parse_args()

    # ── Fast local gate: skip entirely on weekends / outside trading window ───
    if not local_market_check():
        # Silent exit — cron fires at 9:00 too, this is expected
        sys.exit(0)

    # ── Load DB and check for open positions before touching Alpaca ───────────
    conn = sqlite3.connect(DB_PATH)
    conn.execute('PRAGMA journal_mode=WAL')

    # init_paper_trades_table is idempotent; ensures table exists on first run
    sys.path.insert(0, ROOT)
    from scrapers.paper_trader import (
        init_paper_trades_table,
        make_api,
        market_is_open,
        check_exits,
    )
    init_paper_trades_table(conn)

    n_open = count_open_positions(conn)
    if n_open == 0:
        # Nothing to monitor — exit without logging to keep the log clean
        conn.close()
        sys.exit(0)

    # ── Authoritative market-open check (handles holidays) ────────────────────
    api = make_api()
    if not market_is_open(api):
        # Market is closed despite local time check passing (holiday, early close)
        log.info('[MONITOR] Market closed — skipping position check')
        conn.close()
        sys.exit(0)

    # ── Run exit checks ───────────────────────────────────────────────────────
    today = datetime.now().strftime('%Y-%m-%d')
    log.info(f'[MONITOR] Checking {n_open} open position(s) | {today}'
             + (' [DRY RUN]' if args.dry_run else ''))

    closed = check_exits(api, conn, today=today, dry_run=args.dry_run)

    log.info(f'[MONITOR] Done — {closed} position(s) closed this run')
    conn.close()


if __name__ == '__main__':
    main()
