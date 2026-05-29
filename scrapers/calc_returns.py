#!/usr/bin/env python3
"""
Calculate forward returns for all post_ticker rows that have price data.

For each (post, ticker) pair, computes percentage return from post date to:
  +1 calendar day  (next available trading day)
  +3 calendar days
  +7 calendar days
  +30 calendar days

Uses binary search on in-memory price arrays — fast even at 50k+ post scale.
Safe to re-run: skips rows where forward_return_1d is already populated.
"""

import sqlite3
import os
import sys
import logging
import bisect
from datetime import datetime, timedelta, timezone
from collections import defaultdict

ROOT     = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH  = os.path.join(ROOT, 'data', 'wsb.db')
LOG_PATH = os.path.join(ROOT, 'logs', 'calc_returns.log')

os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[logging.StreamHandler(), logging.FileHandler(LOG_PATH)],
)
log = logging.getLogger(__name__)

BATCH_SIZE = 5000


def ts_to_date(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%d')


def offset_date(date_str: str, days: int) -> str:
    dt = datetime.strptime(date_str, '%Y-%m-%d') + timedelta(days=days)
    return dt.strftime('%Y-%m-%d')


def price_on_or_after(sorted_dates: list, price_map: dict, target: str) -> float | None:
    """Closing price on first available trading day >= target date."""
    idx = bisect.bisect_left(sorted_dates, target)
    if idx < len(sorted_dates):
        return price_map[sorted_dates[idx]]
    return None


def calc_returns(db_path: str = DB_PATH) -> int:
    conn = sqlite3.connect(db_path)
    conn.execute('PRAGMA journal_mode=WAL')

    # Load all prices into memory indexed by ticker
    log.info('Loading prices into memory...')
    raw: dict[str, list] = defaultdict(list)
    for ticker, date, close in conn.execute(
        'SELECT ticker, date, close FROM prices ORDER BY ticker, date'
    ):
        raw[ticker].append((date, close))

    # Build sorted-dates + price-map per ticker
    ticker_prices: dict[str, tuple[list, dict]] = {}
    for ticker, rows in raw.items():
        rows.sort()
        dates  = [r[0] for r in rows]
        prices = {r[0]: r[1] for r in rows}
        ticker_prices[ticker] = (dates, prices)
    log.info(f'Prices loaded for {len(ticker_prices):,} tickers')

    # Find rows that still need forward returns
    pending = conn.execute(
        """SELECT pt.post_id, pt.ticker, p.created_utc
           FROM post_tickers pt
           JOIN posts p ON p.post_id = pt.post_id
           WHERE pt.forward_return_1d IS NULL
             AND p.created_utc > 0
           ORDER BY pt.ticker"""
    ).fetchall()
    log.info(f'{len(pending):,} post_ticker rows need forward returns')

    updates       = []
    total_updated = 0
    no_data       = 0

    for post_id, ticker, created_utc in pending:
        tp = ticker_prices.get(ticker)
        if not tp:
            no_data += 1
            continue

        dates, prices = tp
        post_date = ts_to_date(created_utc)

        base = price_on_or_after(dates, prices, post_date)
        if base is None or base == 0:
            no_data += 1
            continue

        def fwd(n: int) -> float | None:
            p = price_on_or_after(dates, prices, offset_date(post_date, n))
            if p is None:
                return None
            return round((p / base) - 1.0, 6)

        updates.append((fwd(1), fwd(3), fwd(7), fwd(30), post_id, ticker))

        if len(updates) >= BATCH_SIZE:
            _flush(conn, updates)
            total_updated += len(updates)
            updates = []
            log.info(f'  {total_updated:,} rows updated...')

    if updates:
        _flush(conn, updates)
        total_updated += len(updates)

    log.info(
        f'Returns calculated: {total_updated:,} rows updated, '
        f'{no_data:,} skipped (no price data)'
    )
    conn.close()
    return total_updated


def _flush(conn: sqlite3.Connection, updates: list):
    conn.executemany(
        """UPDATE post_tickers
           SET forward_return_1d=?, forward_return_3d=?, forward_return_7d=?, forward_return_30d=?
           WHERE post_id=? AND ticker=?""",
        updates,
    )
    conn.commit()


if __name__ == '__main__':
    total = calc_returns()
    print(f'\nDone. {total:,} forward returns calculated.')
    print()
    print('Session 5 go/no-go query:')
    print('  20 most mentioned tickers in last 30 days of dataset + avg 7d return\n')

    import sqlite3 as _sq
    conn = _sq.connect(DB_PATH)

    # Find the date range "last 30 days" of the dataset
    max_ts = conn.execute('SELECT MAX(created_utc) FROM posts WHERE created_utc > 0').fetchone()[0]
    cutoff = max_ts - (30 * 86400)

    rows = conn.execute(
        """SELECT
               pt.ticker,
               COUNT(*)                                    AS mentions,
               ROUND(AVG(pt.forward_return_7d) * 100, 2)  AS avg_7d_pct,
               ROUND(AVG(pt.forward_return_1d) * 100, 2)  AS avg_1d_pct
           FROM post_tickers pt
           JOIN posts p ON p.post_id = pt.post_id
           WHERE p.created_utc >= ?
             AND pt.forward_return_7d IS NOT NULL
           GROUP BY pt.ticker
           ORDER BY mentions DESC
           LIMIT 20""",
        (cutoff,),
    ).fetchall()
    conn.close()

    print(f'{"TICKER":<8} {"MENTIONS":>8} {"AVG 7D %":>10} {"AVG 1D %":>10}')
    print('-' * 40)
    for ticker, mentions, avg7d, avg1d in rows:
        print(f'{ticker:<8} {mentions:>8} {avg7d:>9.2f}% {avg1d:>9.2f}%')
