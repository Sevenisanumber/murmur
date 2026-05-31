#!/usr/bin/env python3
"""
Pushover notification helper — Murmur

Uses stdlib only (urllib). Reads credentials from the environment:
  PUSHOVER_USER_KEY   — your Pushover user key
  PUSHOVER_API_TOKEN  — your application API token

Credentials are loaded from .env by the calling script (paper_trader.py,
check_positions.py) before this module's functions are invoked.

Usage:
  from scrapers.notify import send_pushover
  send_pushover("Trade placed")

  # Send a test notification (loads .env itself):
  python3 scrapers/notify.py
"""

import json
import os
import urllib.parse
import urllib.request

_PUSHOVER_URL = 'https://api.pushover.net/1/messages.json'


def send_pushover(message: str, title: str = 'Murmur') -> bool:
    """POST a Pushover notification. Returns True on success, False on any failure."""
    user_key  = os.getenv('PUSHOVER_USER_KEY')
    api_token = os.getenv('PUSHOVER_API_TOKEN')
    if not user_key or not api_token:
        return False
    try:
        data = urllib.parse.urlencode({
            'token':   api_token,
            'user':    user_key,
            'title':   title,
            'message': message,
        }).encode()
        req = urllib.request.Request(_PUSHOVER_URL, data=data)
        with urllib.request.urlopen(req, timeout=8) as resp:
            body = json.loads(resp.read())
            return body.get('status') == 1
    except Exception:
        return False


def send_morning_briefing(report_text: str) -> bool:
    """
    Parse a daily report string and send a concise Pushover morning briefing.
    Returns True if notification was sent successfully, False otherwise.
    """
    import re

    _EMOJI = {
        'HOT':       '🔥',
        'EXTREME':   '⚠️',
        'RISING':    '📈',
        'SLOW_BURN': '🐢',
        'NORMAL':    '📊',
    }

    # ── Date (MM-DD) — match "Daily Watchlist YYYY-MM-DD" anywhere in header ──
    dm = re.search(r'Daily Watchlist\s+\d{4}-(\d{2})-(\d{2})', report_text)
    date_short = f'{dm.group(1)}-{dm.group(2)}' if dm else '??-??'

    # ── Active ticker count — from "Active tickers: N" in the header line ────
    # (the SUMMARY section has "Active tickers : N" with a space before the
    #  colon, so this regex only matches the header)
    tm = re.search(r'Active tickers:\s*(\d+)', report_text)
    n_tickers = tm.group(1) if tm else '?'

    # ── Top 3 signals from the TOP SIGNALS table ──────────────────────────────
    # Row format: "   1  TSLA         239   3.5x   89.2  HOT         OPTIONS_ACTIVE  WSB..."
    row_re = re.compile(
        r'^\s{2,}(\d+)\s{2}([A-Z][A-Z0-9.]{0,5})\s+'
        r'[\d,]+\s+[\d.]+x\s+([\d.]+)\s+([A-Z_]+)'
    )
    top_rows: list[tuple] = []
    in_table = False
    for line in report_text.splitlines():
        if '── TOP' in line and 'SIGNALS' in line:
            in_table = True
            continue
        if not in_table:
            continue
        if line.startswith('──') or 'VELOCITY ALERTS' in line:
            break
        m = row_re.match(line)
        if m and len(top_rows) < 3:
            ticker = m.group(2)
            score  = m.group(3)
            tag    = m.group(4)
            flags  = [f for f in ('OPTIONS_ACTIVE', 'SQUEEZE_WATCH') if f in line]
            top_rows.append((ticker, score, tag, flags))

    # ── HOT tickers from velocity alert section ───────────────────────────────
    hot_tickers: list[str] = []
    in_hot = False
    hot_re = re.compile(r'^\s{2}([A-Z][A-Z0-9.]{0,5})\s+[\d,]+')
    for line in report_text.splitlines():
        if 'HOT (3-5' in line and 'WATCH' in line:
            in_hot = True
            continue
        if not in_hot:
            continue
        if not line.strip() or line.strip().startswith('SLOW') or '(none)' in line:
            break
        m = hot_re.match(line)
        if m:
            hot_tickers.append(m.group(1))

    # ── Summary counts ────────────────────────────────────────────────────────
    def _num(pattern: str) -> str:
        m = re.search(pattern, report_text)
        return m.group(1) if m else '0'

    n_slow    = _num(r'Slow-burn \(<0\.5.?\):\s*(\d+)')
    n_squeeze = _num(r'Squeeze watch:\s*(\d+)')
    n_opts    = _num(r'Options active:\s*(\d+)')

    # ── Build message ─────────────────────────────────────────────────────────
    lines = [f'Murmur {date_short} | {n_tickers} tickers']

    shown: set[str] = set()
    for ticker, score, tag, flags in top_rows:
        emoji    = _EMOJI.get(tag, '📊')
        flag_str = (' ' + ' '.join(flags)) if flags else ''
        lines.append(f'{emoji} {ticker} {score} {tag}{flag_str}')
        shown.add(ticker)

    extra_hot = [t for t in hot_tickers if t not in shown]
    if extra_hot:
        lines.append(f'Also HOT: {" ".join(extra_hot)}')

    lines.append(f'Slow burns: {n_slow} | Squeeze: {n_squeeze} | Opts: {n_opts}')

    return send_pushover('\n'.join(lines), title='Murmur Morning')


if __name__ == '__main__':
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env'))

    ok = send_pushover(
        'Test notification from Murmur — notify.py is wired up correctly.',
        title='Murmur Test',
    )
    if ok:
        print('OK  Test notification sent.')
    else:
        print('FAIL  Nothing sent — check PUSHOVER_USER_KEY and PUSHOVER_API_TOKEN in .env')
        sys.exit(1)
