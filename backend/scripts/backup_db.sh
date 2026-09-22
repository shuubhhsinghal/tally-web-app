#!/usr/bin/env bash
set -euo pipefail

# Snapshots the live operational SQLite database on a schedule (see the
# tally-backup.timer/.service systemd units on the VM, which invoke this
# every 6 hours). Kept in the repo so the backup logic itself is versioned,
# even though the systemd units that schedule it are VM-local config.

DB_PATH="/home/ubuntu/tally-web-app/backend/tally_sync.db"
BACKUP_DIR="/home/ubuntu/backups/tally_sync"
RETENTION_DAYS=14

mkdir -p "$BACKUP_DIR"
timestamp=$(date -u +%Y%m%d-%H%M%S)
dest="$BACKUP_DIR/tally_sync-$timestamp.db"

# Uses SQLite's own online backup API (via the stdlib sqlite3 module) rather
# than a plain file copy -- safe to run against the live database, including
# mid-write, since it takes a consistent snapshot instead of racing an
# in-flight transaction or copying a torn WAL file.
python3 - "$DB_PATH" "$dest" <<'PYEOF'
import sqlite3
import sys

src = sqlite3.connect(sys.argv[1])
dst = sqlite3.connect(sys.argv[2])
with dst:
    src.backup(dst)
dst.close()
src.close()
PYEOF

find "$BACKUP_DIR" -name 'tally_sync-*.db' -mtime "+$RETENTION_DAYS" -delete
