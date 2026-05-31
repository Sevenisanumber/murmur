#!/usr/bin/env python3
"""
Fetch historical OHLCV price data from Yahoo Finance for tickers that have
WSB posts before 2020-01-01 but no pre-2020 rows in the prices table.

Downloads in batches of BATCH_SIZE tickers using yf.download(), which is
significantly faster than individual Ticker.history() calls.

Date range: 2012-01-01 → 2020-09-23 (day before existing Alpaca data starts).
Source field is set to 'yfinance'. Safe to re-run: uses INSERT OR IGNORE.

Usage:
  python3 scrapers/fetch_prices_yfinance.py
  python3 scrapers/fetch_prices_yfinance.py --dry-run   # show counts, don't fetch
"""

import argparse
import logging
import math
import os
import sqlite3
import time
import warnings

warnings.filterwarnings('ignore')  # suppress yfinance noise
import yfinance as yf
yf.set_tz_cache_location("/tmp/yf_cache")  # avoid SQLite conflict with wsb.db

ROOT     = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH  = os.path.join(ROOT, 'data', 'wsb.db')
LOG_PATH = os.path.join(ROOT, 'logs', 'fetch_prices_yfinance.log')

os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[logging.StreamHandler(), logging.FileHandler(LOG_PATH)],
)
log = logging.getLogger(__name__)

FETCH_START    = '2012-01-01'
FETCH_END      = '2020-09-23'   # one day before existing Alpaca data begins
PRE2020_CUTOFF = 1577836800     # Unix ts for 2020-01-01 00:00 UTC
BATCH_SIZE     = 100
BATCH_SLEEP    = 1.0            # seconds between batches (polite to Yahoo)


def tickers_needing_data(conn: sqlite3.Connection) -> list[str]:
    """Tickers with pre-2020 posts that have no pre-2020 price rows yet."""
    pre2020 = {r[0] for r in conn.execute("""
        SELECT DISTINCT pt.ticker
        FROM post_tickers pt
        JOIN posts p ON p.post_id = pt.post_id
        WHERE p.created_utc > 0 AND p.created_utc < ?
    """, (PRE2020_CUTOFF,))}

    have_data = {r[0] for r in conn.execute(
        "SELECT DISTINCT ticker FROM prices WHERE date < '2020-01-01'"
    )}

    need = sorted(pre2020 - have_data)
    log.info(f"  {len(pre2020):,} tickers appear in pre-2020 posts")
    log.info(f"  {len(have_data):,} already have pre-2020 price rows")
    log.info(f"  {len(need):,} tickers to fetch")
    return need


RATE_LIMIT_RETRIES = 3
RATE_LIMIT_WAITS   = [30, 90, 180]   # seconds to sleep on successive rate-limit hits


def fetch_batch(tickers: list[str]) -> dict[str, list[tuple]]:
    """
    Download a batch via yf.download() and return
    {ticker: [(date_str, open, high, low, close, volume), ...]}.

    yfinance 1.4.x returns MultiIndex columns with level 0 = price type,
    level 1 = ticker (confirmed by local inspection).
    Retries up to RATE_LIMIT_RETRIES times on YFRateLimitError.
    """
    result: dict[str, list[tuple]] = {}
    for attempt in range(RATE_LIMIT_RETRIES):
        try:
            raw = yf.download(
                tickers,
                start=FETCH_START,
                end=FETCH_END,
                auto_adjust=True,
                progress=False,
                threads=True,
            )
            break   # success — exit retry loop
        except Exception as e:
            if 'ratelimit' in str(e).lower() or 'too many requests' in str(e).lower():
                wait = RATE_LIMIT_WAITS[min(attempt, len(RATE_LIMIT_WAITS) - 1)]
                log.warning(f"Rate limited on attempt {attempt+1}; sleeping {wait}s then retrying...")
                time.sleep(wait)
                if attempt == RATE_LIMIT_RETRIES - 1:
                    log.error(f"Batch failed after {RATE_LIMIT_RETRIES} attempts (rate limit)")
                    return result
            else:
                log.warning(f"Batch download error: {e}")
                return result
    else:
        return result

    if raw.empty:
        return result

    import pandas as pd

    # Multi-level: ('PriceType', 'Ticker'); cross-section by ticker on level 1
    if isinstance(raw.columns, pd.MultiIndex):
        available = set(raw.columns.get_level_values(1))
        for ticker in tickers:
            if ticker not in available:
                continue
            try:
                df = raw.xs(ticker, axis=1, level=1).dropna(subset=['Close'])
                rows = _df_to_rows(df)
                if rows:
                    result[ticker] = rows
            except Exception:
                pass
    else:
        # Flat columns — only happens for a single-ticker batch (edge case)
        if len(tickers) == 1:
            df = raw.dropna(subset=['Close'])
            rows = _df_to_rows(df)
            if rows:
                result[tickers[0]] = rows

    return result


def _df_to_rows(df) -> list[tuple]:
    """Convert a per-ticker DataFrame to (date_str, o, h, l, c, vol) tuples."""
    rows = []
    for idx, row in df.iterrows():
        try:
            date_str = idx.strftime('%Y-%m-%d')
            vol = int(row['Volume']) if not math.isnan(float(row['Volume'])) else 0
            rows.append((
                date_str,
                float(row['Open']),
                float(row['High']),
                float(row['Low']),
                float(row['Close']),
                vol,
            ))
        except Exception:
            pass
    return rows


def store_batch(conn: sqlite3.Connection,
                price_data: dict[str, list[tuple]]) -> int:
    """INSERT OR IGNORE all rows for the batch; return count inserted."""
    all_rows = []
    for ticker, rows in price_data.items():
        for date_str, o, h, l, c, v in rows:
            all_rows.append((ticker, date_str, o, h, l, c, v))

    if not all_rows:
        return 0

    conn.executemany(
        """INSERT OR IGNORE INTO prices
           (ticker, date, open, high, low, close, volume, source)
           VALUES (?, ?, ?, ?, ?, ?, ?, 'yfinance')""",
        all_rows,
    )
    conn.commit()
    return len(all_rows)


def main(dry_run: bool = False):
    conn = sqlite3.connect(DB_PATH)
    conn.execute('PRAGMA journal_mode=WAL')

    log.info("Scanning for tickers needing historical price data...")
    tickers = tickers_needing_data(conn)

    if not tickers:
        log.info("Nothing to fetch — all tickers already have pre-2020 data.")
        conn.close()
        return 0

    if dry_run:
        log.info(f"Dry run: would fetch {len(tickers):,} tickers "
                 f"in {math.ceil(len(tickers)/BATCH_SIZE)} batches.")
        conn.close()
        return 0

    batches      = math.ceil(len(tickers) / BATCH_SIZE)
    total_rows   = 0
    tickers_ok   = 0
    tickers_miss = 0

    log.info(f"Fetching {len(tickers):,} tickers | "
             f"{batches} batches of {BATCH_SIZE} | "
             f"{FETCH_START} → {FETCH_END}")

    for b in range(batches):
        batch    = tickers[b * BATCH_SIZE:(b + 1) * BATCH_SIZE]
        t0       = time.time()

        price_data = fetch_batch(batch)

        batch_rows   = store_batch(conn, price_data)
        batch_ok     = len(price_data)
        batch_miss   = len(batch) - batch_ok

        total_rows   += batch_rows
        tickers_ok   += batch_ok
        tickers_miss += batch_miss

        elapsed = time.time() - t0
        pct     = (b + 1) / batches * 100
        log.info(
            f"[{b+1:3d}/{batches}] {pct:5.1f}%  "
            f"+{batch_rows:6,} rows  ({batch_ok} tickers ok, {batch_miss} no data)  "
            f"{elapsed:.1f}s  running total: {total_rows:,}"
        )

        if b < batches - 1:
            time.sleep(BATCH_SLEEP)

    log.info("=" * 55)
    log.info(f"Complete: {total_rows:,} new price rows added")
    log.info(f"  Tickers with data:    {tickers_ok:,}")
    log.info(f"  Tickers with no data: {tickers_miss:,} "
             f"(delisted / ETF / invalid symbols)")

    # Summary query
    r = conn.execute(
        "SELECT COUNT(*), MIN(date), MAX(date) FROM prices WHERE source='yfinance'"
    ).fetchone()
    log.info(f"  yfinance rows in DB:  {r[0]:,}  ({r[1]} to {r[2]})")

    conn.close()
    return total_rows


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Fetch pre-2020 historical prices from Yahoo Finance'
    )
    parser.add_argument(
        '--dry-run', action='store_true',
        help='Show what would be fetched without downloading'
    )
    args = parser.parse_args()
    total = main(dry_run=args.dry_run)
    if not args.dry_run:
        print(f'\nDone. {total:,} price rows added.')
