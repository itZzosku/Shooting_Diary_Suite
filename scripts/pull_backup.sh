#!/usr/bin/env bash
# Linux version of pull_backup.ps1: pulls a fresh backup from the server into a dated folder, keeps KEEP_DAYS.
# Cron (daily 04:00):  0 4 * * *  /path/to/pull_backup.sh >> ~/diary-pull.log 2>&1
# Key setup: as in pull_backup.ps1 (ssh-keygen -t ed25519 -f ~/.ssh/diary_backup, restricted line on the server).
set -euo pipefail
SERVER="${SERVER:-root@10.30.2.2}"
KEY="${KEY:-$HOME/.ssh/diary_backup}"
DEST="${DEST:-$HOME/backups/shooting-diary}"
KEEP_DAYS="${KEEP_DAYS:-60}"

out="$DEST/$(date +%Y-%m-%d_%H%M)"
mkdir -p "$out"
trap 'rm -rf "$out"; echo "$(date -Is) FAILED" >&2' ERR
ssh -i "$KEY" -o BatchMode=yes -o ConnectTimeout=20 -o StrictHostKeyChecking=accept-new "$SERVER" backup | tar -xf - -C "$out"
ls "$out"/users-*.db "$out"/diary-*.json >/dev/null      # incomplete → ERR
trap - ERR
echo "$(date -Is) OK $out ($(du -sh "$out" | cut -f1))"
find "$DEST" -mindepth 1 -maxdepth 1 -type d -name '20*' -mtime +"$KEEP_DAYS" ! -path "$out" -exec rm -rf {} +
