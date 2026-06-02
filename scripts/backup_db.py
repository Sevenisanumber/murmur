#!/usr/bin/env python3
"""
Weekly database backup — WSB Signal Lab

Copies data/wsb.db to /mnt/media/backups/murmur/wsb_YYYY-MM-DD.db,
retains the 4 most recent backups, and logs the result.
Sends a Pushover alert only on failure.

Cron: 0 17 * * 5  (Friday 5pm CDT, after weekly digest at 4:30pm)
"""

import logging
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / '.env')

DB_PATH     = ROOT / 'data' / 'wsb.db'
BACKUP_DIR  = Path('/mnt/media/backups/murmur')
KEEP        = 4
LOG_PATH    = ROOT / 'logs' / 'backup.log'

os.makedirs(ROOT / 'logs', exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_PATH),
    ],
)
log = logging.getLogger(__name__)


def main() -> None:
    today = datetime.now().strftime('%Y-%m-%d')
    dest  = BACKUP_DIR / f'wsb_{today}.db'

    log.info(f'=== DB backup starting | dest={dest} ===')

    try:
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)

        if not DB_PATH.exists():
            raise FileNotFoundError(f'Source DB not found: {DB_PATH}')

        shutil.copy2(DB_PATH, dest)
        size_mb = dest.stat().st_size / 1024 / 1024
        log.info(f'Backup written: {dest} ({size_mb:.1f} MB)')

        # Prune old backups — keep the KEEP most recent wsb_*.db files
        backups = sorted(BACKUP_DIR.glob('wsb_*.db'))
        for old in backups[:-KEEP]:
            old.unlink()
            log.info(f'Pruned old backup: {old.name}')

        remaining = sorted(BACKUP_DIR.glob('wsb_*.db'))
        log.info(f'Backups retained ({len(remaining)}): {[f.name for f in remaining]}')

    except Exception as e:
        log.error(f'Backup FAILED: {e}')
        from scrapers.notify import send_pushover
        send_pushover(f'DB backup failed: {e}', title='Murmur Backup FAILED')
        sys.exit(1)

    log.info('=== DB backup complete ===')


if __name__ == '__main__':
    main()
