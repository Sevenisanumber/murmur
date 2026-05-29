#!/usr/bin/env python3
"""
Populate the tickers table from Alpaca's list of active US equity assets.

Uses the same Alpaca credentials already in .env — no extra accounts needed.
Re-running is safe — uses INSERT OR REPLACE.
"""

import sqlite3
import os
import sys
import logging

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env'))

ROOT     = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH  = os.path.join(ROOT, 'data', 'wsb.db')
LOG_PATH = os.path.join(ROOT, 'logs', 'load_tickers.log')

os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[logging.StreamHandler(), logging.FileHandler(LOG_PATH)],
)
log = logging.getLogger(__name__)

# Tickers flagged ambiguous are loaded into the DB but skipped by the extractor.
# These are either common English words, WSB slang, or financial acronyms that
# produce too many false positives even though some are valid ticker symbols.
AMBIGUOUS = {
    # Plan-specified list
    'AI', 'ON', 'ARE', 'LOVE', 'CAN', 'GO', 'NOW', 'RUN', 'NEW', 'IT',
    'AT', 'BE', 'OR', 'AND', 'FOR', 'THE',
    # Common English words
    'ALL', 'ONE', 'TWO', 'BIG', 'OLD', 'HIT', 'OUT', 'HAS', 'GET', 'GOT',
    'NOT', 'BUT', 'ITS', 'HIS', 'WHO', 'HOW', 'WHY', 'WILL', 'THAT', 'THIS',
    'FROM', 'WITH', 'BEEN', 'HAVE', 'THEM', 'THEY', 'WHEN', 'WHAT', 'THAN',
    'THEN', 'SOME', 'ALSO', 'INTO', 'OVER', 'VERY', 'JUST', 'EVEN', 'BACK',
    'GOOD', 'WELL', 'LONG', 'DOWN', 'LAST', 'NEXT', 'MOST', 'MUCH', 'MANY',
    'HIGH', 'CALL', 'SAYS', 'WANT', 'NEED', 'LOOK', 'CAME', 'HERE', 'BOTH',
    # Financial jargon (acronyms not worth tracking as tickers)
    'IPO', 'ETF', 'EPS', 'CEO', 'CFO', 'CTO', 'COO', 'SEC', 'FED',
    'GDP', 'CPI', 'PPI', 'PCE', 'DD', 'TA', 'PE', 'PB',
    # WSB slang and options jargon
    'YOLO', 'FOMO', 'HODL', 'APES', 'ATH', 'ATL', 'BULL', 'BEAR',
    'PUTS', 'HOLD', 'MOON', 'LOSS', 'GAIN', 'TLDR', 'ITM', 'OTM',
    'WSB', 'DFV', 'EDIT',
    # Geographic / institutional abbreviations
    'UK', 'EU', 'US', 'USD', 'NYSE', 'SEC',
    # Common short words that beat the ALL-CAPS filter
    'YOU', 'YOUR', 'LINE', 'UP', 'SO', 'NO', 'MY', 'DO', 'IF',
    'TO', 'OF', 'IN', 'IS', 'BY', 'AS', 'AN', 'AM',
    # Single letters — real tickers (T, F, C, etc.) but far too noisy in prose
    *list('ABCDEFGHIJKLMNOPQRSTUVWXYZ'),
}


def load_tickers(db_path: str = DB_PATH) -> int:
    import alpaca_trade_api as tradeapi

    api_key    = os.getenv('ALPACA_API_KEY')
    secret_key = os.getenv('ALPACA_SECRET_KEY')
    base_url   = os.getenv('ALPACA_BASE_URL', 'https://paper-api.alpaca.markets')

    if not api_key or not secret_key:
        log.error('Missing ALPACA_API_KEY or ALPACA_SECRET_KEY in .env')
        sys.exit(1)

    if not os.path.exists(db_path):
        log.error(f'DB not found: {db_path}. Run scripts/init_db.py first.')
        sys.exit(1)

    api = tradeapi.REST(api_key, secret_key, base_url, api_version='v2')

    log.info('Fetching active US equity assets from Alpaca...')
    assets = api.list_assets(status='active', asset_class='us_equity')
    log.info(f'Received {len(assets):,} assets from Alpaca')

    rows = []
    for asset in assets:
        ticker = asset.symbol.strip()
        # Keep only pure-alpha tickers up to 5 chars (skip BRK/B, preferred shares, etc.)
        if not ticker or not ticker.isalpha() or len(ticker) > 5:
            continue
        is_ambiguous = 1 if ticker in AMBIGUOUS else 0
        rows.append((ticker, asset.name, asset.exchange, None, is_ambiguous))

    conn = sqlite3.connect(db_path)
    conn.execute('PRAGMA journal_mode=WAL')
    conn.executemany(
        """INSERT OR REPLACE INTO tickers
           (ticker, company_name, exchange, sector, is_ambiguous)
           VALUES (?,?,?,?,?)""",
        rows,
    )
    conn.commit()

    count  = conn.execute('SELECT COUNT(*) FROM tickers').fetchone()[0]
    ambig  = conn.execute('SELECT COUNT(*) FROM tickers WHERE is_ambiguous=1').fetchone()[0]
    active = count - ambig
    log.info(f'Tickers table: {count:,} total | {active:,} active | {ambig:,} ambiguous')
    conn.close()
    return len(rows)


if __name__ == '__main__':
    n = load_tickers()
    print(f'Done. {n:,} tickers loaded.')
