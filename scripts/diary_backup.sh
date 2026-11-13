#!/usr/bin/env bash
# Weekly backup into diary_backups/: every account (JSON + SQLite copy), the accounts
# file (users-DATE.db), the club ammo database and the shared reticle library;
# all of it emailed to you as one zip.
# Needs DIARY_SMTP_USER and DIARY_SMTP_PASSWORD (Gmail app password) in the repo's .env.
# Cron (Sundays 03:17):  17 3 * * 0  /root/Shooting_Diary_Suite/scripts/diary_backup.sh >> /var/log/diary_backup.log 2>&1
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="${PYTHON:-$REPO_DIR/venv/bin/python}"
BACKUP_DIR="${BACKUP_DIR:-$REPO_DIR/diary_backups}"    # git-ignored
KEEP="${KEEP:-12}"                                       # copies kept on the server

echo "== $(date -Is) diary backup"
cd "$REPO_DIR"
"$PYTHON" -m diary backup "$BACKUP_DIR" --keep "$KEEP"
# Club ammo (separate database): a dated copy next to the diary backups; shooters' names older than
# CLUB_NAMES_YEARS (default 3) are forgotten first, as the privacy page says
"$PYTHON" -m club backup "$BACKUP_DIR" --keep "$KEEP"
# Shared reticle library of the ballistic calculator: a dated copy as well
"$PYTHON" -m ballistics backup "$BACKUP_DIR" --keep "$KEEP"
# Last: everything above in one zip by email (account .db copies left out if it would be too big)
"$PYTHON" -m diary mail-backup "$BACKUP_DIR"
