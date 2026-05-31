#!/usr/bin/env python3
"""
Backfill daily OHLCV prices for 2022-01-01 through 2025-12-31 using yfinance.

Tickers are pulled from daily_mentions (the tickers we actually care about).
Any ticker that already has price rows anywhere in the 2022-2025 range is
skipped — safe to interrupt and re-run.

After prices are loaded, forward returns (1d/3d/7d/30d) are computed for
every (ticker, date) pair in daily_mentions for 2022-2025 that doesn't
already have an entry in forward_returns.  Results are inserted into a new
forward_returns table (created if absent) using INSERT OR IGNORE so the
phase is fully idempotent.

Intended to run overnight on the Pi:
  nohup venv/bin/python scrapers/fetch_prices_2022_2025.py > logs/fetch_2022_2025.log 2>&1 &

Requires: yfinance==0.2.58
"""

import bisect
import logging
import math
import os
import sqlite3
import sys
import time
import warnings
from collections import defaultdict
from datetime import datetime, timedelta, timezone

warnings.filterwarnings('ignore')
import yfinance as yf
yf.set_tz_cache_location('/tmp/yf_cache')

ROOT     = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH  = os.path.join(ROOT, 'data', 'wsb.db')
LOG_PATH = os.path.join(ROOT, 'logs', 'fetch_2022_2025.log')

os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[logging.StreamHandler(), logging.FileHandler(LOG_PATH)],
)
log = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────

FETCH_START  = '2022-01-01'
FETCH_END    = '2026-01-01'   # yfinance end is exclusive → captures all of 2025

TICKER_SLEEP = 1.0            # seconds between tickers (Pi-friendly pacing)
COMMIT_EVERY = 50             # tickers between DB commits

# Slightly wider window for price loading so we can look up base/forward prices
# at the edges of 2022 (Dec-2021 base) and 2025 (Jan-2026 forward)
PRICE_LOAD_START = '2021-12-01'
PRICE_LOAD_END   = '2026-02-01'

RETURNS_BATCH = 5000


# ── Price fetch ────────────────────────────────────────────────────────────────

def tickers_needing_prices(conn: sqlite3.Connection) -> tuple[list[str], int]:
    """
    Return (tickers_to_fetch, n_already_have_data).
    Tickers are sourced from daily_mentions; any ticker with at least one
    price row in the 2022-2025 range is considered complete and skipped.
    """
    all_tickers = {r[0] for r in conn.execute(
        'SELECT DISTINCT ticker FROM daily_mentions'
    )}
    have_data = {r[0] for r in conn.execute(
        """SELECT DISTINCT ticker FROM prices
           WHERE date >= '2022-01-01' AND date <= '2025-12-31'"""
    )}
    need = sorted(all_tickers - have_data)
    n_skip = len(all_tickers) - len(need)

    log.info(f'  {len(all_tickers):,} distinct tickers in daily_mentions')
    log.info(f'  {n_skip:,} already have 2022-2025 price rows — skipping')
    log.info(f'  {len(need):,} tickers to fetch')
    return need, n_skip


def fetch_ticker(ticker: str) -> list[tuple]:
    """
    Fetch OHLCV rows for the target window via yf.Ticker.history().
    Returns a list of (date_str, open, high, low, close, volume) tuples,
    or [] on any failure or empty result.
    """
    try:
        df = yf.Ticker(ticker).history(
            start=FETCH_START,
            end=FETCH_END,
            auto_adjust=True,
        )
    except Exception as e:
        log.warning(f'  yfinance error for {ticker}: {e}')
        return []

    if df is None or df.empty:
        return []

    rows = []
    for idx, row in df.iterrows():
        try:
            date_str = idx.strftime('%Y-%m-%d')
            vol = int(row['Volume']) if not math.isnan(float(row.get('Volume', 0) or 0)) else 0
            rows.append((
                date_str,
                round(float(row['Open']),  6),
                round(float(row['High']),  6),
                round(float(row['Low']),   6),
                round(float(row['Close']), 6),
                vol,
            ))
        except Exception:
            pass
    return rows


def flush_prices(conn: sqlite3.Connection, pending: list[tuple]) -> int:
    """INSERT OR IGNORE a batch of (ticker, date, o, h, l, c, v) rows."""
    if not pending:
        return 0
    conn.executemany(
        """INSERT OR IGNORE INTO prices
           (ticker, date, open, high, low, close, volume, source)
           VALUES (?, ?, ?, ?, ?, ?, ?, 'yfinance')""",
        pending,
    )
    conn.commit()
    return len(pending)


# ── Forward returns ────────────────────────────────────────────────────────────

def _price_on_or_after(sorted_dates: list, price_map: dict, target: str) -> float | None:
    idx = bisect.bisect_left(sorted_dates, target)
    if idx < len(sorted_dates):
        return price_map[sorted_dates[idx]]
    return None


def ensure_forward_returns_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS forward_returns (
            ticker     TEXT NOT NULL,
            date       TEXT NOT NULL,
            return_1d  REAL,
            return_3d  REAL,
            return_7d  REAL,
            return_30d REAL,
            PRIMARY KEY (ticker, date)
        )
    """)
    conn.execute(
        'CREATE INDEX IF NOT EXISTS idx_fr_date ON forward_returns(date)'
    )
    conn.commit()


def calc_returns_from_mentions(conn: sqlite3.Connection) -> int:
    """
    Compute 1d/3d/7d/30d forward returns for every (ticker, date) pair in
    daily_mentions for 2022-2025 that doesn't yet have a row in forward_returns.
    Uses the same bisect+price-map pattern as calc_returns.py.
    """
    ensure_forward_returns_table(conn)

    log.info('  Loading 2022-2025 prices into memory (windowed)...')
    raw: dict[str, list] = defaultdict(list)
    for ticker, date, close in conn.execute(
        """SELECT ticker, date, close FROM prices
           WHERE date >= ? AND date <= ? AND close IS NOT NULL""",
        (PRICE_LOAD_START, PRICE_LOAD_END),
    ):
        raw[ticker].append((date, close))

    ticker_prices: dict[str, tuple[list, dict]] = {}
    for ticker, rows in raw.items():
        rows.sort()
        dates  = [r[0] for r in rows]
        prices = {r[0]: r[1] for r in rows}
        ticker_prices[ticker] = (dates, prices)
    log.info(f'  Prices loaded for {len(ticker_prices):,} tickers')

    # Distinct (ticker, date) pairs from daily_mentions that are not yet in
    # forward_returns — LEFT JOIN is the idempotency check
    pending = conn.execute(
        """SELECT DISTINCT dm.ticker, dm.date
             FROM daily_mentions dm
        LEFT JOIN forward_returns fr
               ON fr.ticker = dm.ticker AND fr.date = dm.date
            WHERE dm.date >= '2022-01-01'
              AND dm.date <= '2025-12-31'
              AND fr.ticker IS NULL
            ORDER BY dm.ticker, dm.date"""
    ).fetchall()
    log.info(f'  {len(pending):,} (ticker, date) pairs need forward returns')

    inserts   = []
    n_written = 0
    n_no_data = 0

    for ticker, date in pending:
        tp = ticker_prices.get(ticker)
        if not tp:
            n_no_data += 1
            continue

        dates_list, prices = tp
        base = _price_on_or_after(dates_list, prices, date)
        if base is None or base == 0:
            n_no_data += 1
            continue

        def fwd(n: int) -> float | None:
            target = (datetime.strptime(date, '%Y-%m-%d') + timedelta(days=n)).strftime('%Y-%m-%d')
            p = _price_on_or_after(dates_list, prices, target)
            return None if p is None else round((p / base) - 1.0, 6)

        inserts.append((ticker, date, fwd(1), fwd(3), fwd(7), fwd(30)))

        if len(inserts) >= RETURNS_BATCH:
            _flush_forward_returns(conn, inserts)
            n_written += len(inserts)
            inserts = []
            log.info(f'  {n_written:,} return rows flushed...')

    if inserts:
        _flush_forward_returns(conn, inserts)
        n_written += len(inserts)

    log.info(f'  Returns done: {n_written:,} rows written, {n_no_data:,} skipped (no price coverage)')
    return n_written


def _flush_forward_returns(conn: sqlite3.Connection, inserts: list) -> None:
    conn.executemany(
        """INSERT OR IGNORE INTO forward_returns
           (ticker, date, return_1d, return_3d, return_7d, return_30d)
           VALUES (?, ?, ?, ?, ?, ?)""",
        inserts,
    )
    conn.commit()


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.execute('PRAGMA journal_mode=WAL')

    log.info('=== fetch_prices_2022_2025 starting ===')
    log.info(f'Target range: {FETCH_START} → 2025-12-31')

    # ── 1. Determine what to fetch ─────────────────────────────────────────────
    tickers, n_skipped = tickers_needing_prices(conn)
    total = len(tickers)

    # ── 2. Fetch prices one ticker at a time ───────────────────────────────────
    n_fetched = 0
    n_failed  = 0
    pending: list[tuple] = []

    for i, ticker in enumerate(tickers, start=1):
        log.info(f'Fetching {i} of {total}: {ticker}')
        rows = fetch_ticker(ticker)

        if rows:
            for r in rows:
                pending.append((ticker,) + r)
            n_fetched += 1
        else:
            log.info(f'  {ticker}: no data returned (delisted or invalid)')
            n_failed += 1

        if i % COMMIT_EVERY == 0 or i == total:
            inserted = flush_prices(conn, pending)
            pending.clear()
            log.info(f'  Committed {inserted:,} price rows (ticker {i}/{total})')

        if i < total:
            time.sleep(TICKER_SLEEP)

    log.info('=== Price fetch complete ===')
    log.info(f'  Fetched:  {n_fetched:,}')
    log.info(f'  Skipped:  {n_skipped:,}  (already had 2022-2025 data)')
    log.info(f'  Failed:   {n_failed:,}  (no data from yfinance)')

    # ── 3. Forward returns from daily_mentions → forward_returns table ────────
    log.info('=== Computing 2022-2025 forward returns from daily_mentions ===')
    n_returns = calc_returns_from_mentions(conn)

    log.info('=== All done ===')
    log.info(f'  Price rows added for {n_fetched:,} tickers')
    log.info(f'  Forward return rows written: {n_returns:,}')

    conn.close()


if __name__ == '__main__':
    main()
