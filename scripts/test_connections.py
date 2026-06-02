#!/usr/bin/env python3
"""Test Alpaca API credentials and confirm data is reachable."""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env'))


def test_alpaca():
    import alpaca_trade_api as tradeapi

    api_key = os.getenv('ALPACA_API_KEY')
    secret_key = os.getenv('ALPACA_SECRET_KEY')
    base_url = os.getenv('ALPACA_BASE_URL', 'https://paper-api.alpaca.markets')

    if not all([api_key, secret_key]):
        print('[ALPACA] FAIL — missing ALPACA_API_KEY or ALPACA_SECRET_KEY in .env')
        return False

    try:
        api = tradeapi.REST(api_key, secret_key, base_url, api_version='v2')
        account = api.get_account()
        bars = api.get_bars('AAPL', '1Day', limit=3).df
        print(f'[ALPACA] OK — account status: {account.status}')
        print('  AAPL last 3 days:')
        for date, row in bars.iterrows():
            print(f'  - {str(date)[:10]} | close: ${row["close"]:.2f}')
        return True
    except Exception as e:
        print(f'[ALPACA] FAIL — {e}')
        return False


def test_db():
    """Verify the database exists and is initialized."""
    import sqlite3
    db_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'wsb.db')
    if not os.path.exists(db_path):
        print('[DB] NOT FOUND — run: python scripts/init_db.py')
        return False
    try:
        conn = sqlite3.connect(db_path)
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        conn.close()
        expected = {'posts', 'authors', 'tickers', 'post_tickers', 'scrape_log', 'prices'}
        missing = expected - set(tables)
        if missing:
            print(f'[DB] FAIL — missing tables: {missing}')
            return False
        post_count = sqlite3.connect(db_path).execute('SELECT COUNT(*) FROM posts').fetchone()[0]
        print(f'[DB] OK — all tables present | {post_count:,} posts')
        return True
    except Exception as e:
        print(f'[DB] FAIL — {e}')
        return False


if __name__ == '__main__':
    print('=== Murmur — Connection Test ===\n')
    db_ok = test_db()
    print()
    alpaca_ok = test_alpaca()
    print()
    if db_ok and alpaca_ok:
        print('All checks passed. Ready to proceed.')
        sys.exit(0)
    else:
        print('One or more checks failed. See above.')
        sys.exit(1)
