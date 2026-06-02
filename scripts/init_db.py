#!/usr/bin/env python3
"""Initialize the SQLite database with the full Phase 1 schema."""

import logging
import sqlite3
import os
import sys

log = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'wsb.db')


def init_db(db_path=DB_PATH):
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA foreign_keys=ON')

    conn.executescript("""
        CREATE TABLE IF NOT EXISTS posts (
            post_id         TEXT PRIMARY KEY,
            author          TEXT,
            title           TEXT,
            body            TEXT,
            score           INTEGER,
            upvote_ratio    REAL,
            num_comments    INTEGER,
            created_utc     INTEGER,   -- unix timestamp
            url             TEXT,
            flair           TEXT,
            is_self         INTEGER,   -- 1 = text post, 0 = link post
            scraped_at      INTEGER,   -- unix timestamp of when we ingested it
            source          TEXT,      -- 'kaggle' or 'live'
            is_bullish      INTEGER    -- 1=bullish 0=bearish NULL=neutral/unclear
        );

        CREATE INDEX IF NOT EXISTS idx_posts_created ON posts(created_utc);
        CREATE INDEX IF NOT EXISTS idx_posts_author  ON posts(author);

        CREATE TABLE IF NOT EXISTS authors (
            author_id           INTEGER PRIMARY KEY AUTOINCREMENT,
            username            TEXT UNIQUE NOT NULL,
            first_seen_at       INTEGER,
            last_seen_at        INTEGER,
            total_posts_scraped INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS tickers (
            ticker        TEXT PRIMARY KEY,
            company_name  TEXT,
            exchange      TEXT,
            sector        TEXT,
            is_ambiguous  INTEGER DEFAULT 0   -- 1 if ticker is also a common English word
        );

        CREATE TABLE IF NOT EXISTS post_tickers (
            post_id            TEXT NOT NULL REFERENCES posts(post_id),
            ticker             TEXT NOT NULL REFERENCES tickers(ticker),
            mention_count      INTEGER DEFAULT 1,
            extraction_method  TEXT,           -- e.g. 'regex'
            surrounding_text   TEXT,
            forward_return_1d  REAL,
            forward_return_3d  REAL,
            forward_return_7d  REAL,
            forward_return_30d REAL,
            PRIMARY KEY (post_id, ticker)
        );

        CREATE TABLE IF NOT EXISTS scrape_log (
            run_id        INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at    INTEGER NOT NULL,
            finished_at   INTEGER,
            posts_fetched INTEGER DEFAULT 0,
            errors        TEXT,
            status        TEXT,   -- 'running', 'success', 'failure'
            script        TEXT    -- which script/step produced this row
        );

        CREATE TABLE IF NOT EXISTS prices (
            ticker  TEXT NOT NULL,
            date    TEXT NOT NULL,   -- YYYY-MM-DD
            open    REAL,
            high    REAL,
            low     REAL,
            close   REAL,
            volume  INTEGER,
            source  TEXT,
            PRIMARY KEY (ticker, date)
        );

        CREATE INDEX IF NOT EXISTS idx_prices_ticker ON prices(ticker);

        CREATE TABLE IF NOT EXISTS daily_mentions (
            ticker        TEXT NOT NULL,
            date          TEXT NOT NULL,   -- YYYY-MM-DD
            mention_count INTEGER NOT NULL DEFAULT 0,
            subreddit     TEXT NOT NULL,
            PRIMARY KEY (ticker, date, subreddit)
        );

        CREATE INDEX IF NOT EXISTS idx_dm_date    ON daily_mentions(date);
        CREATE INDEX IF NOT EXISTS idx_dm_ticker  ON daily_mentions(ticker);

        CREATE TABLE IF NOT EXISTS short_interest (
            ticker        TEXT NOT NULL,
            report_date   TEXT NOT NULL,   -- YYYY-MM-DD
            short_interest INTEGER,
            days_to_cover REAL,
            float_percent REAL,
            fetched_at    INTEGER,          -- unix timestamp
            PRIMARY KEY (ticker, report_date)
        );
    """)

    migrate_is_bullish(conn)

    conn.commit()
    conn.close()
    print(f'Database initialized at {db_path}')


def migrate_is_bullish(conn) -> None:
    """Add is_bullish column to posts if absent. Safe to call on any existing DB."""
    try:
        conn.execute("ALTER TABLE posts ADD COLUMN is_bullish INTEGER")
        conn.commit()
        print("Migration: added 'is_bullish' column to posts")
    except sqlite3.OperationalError as e:
        if 'duplicate column name' in str(e).lower():
            print("Migration: 'is_bullish' column already exists — skipping")
        else:
            raise


if __name__ == '__main__':
    path = sys.argv[1] if len(sys.argv) > 1 else DB_PATH
    init_db(path)
