#!/usr/bin/env bash
# A fresh backup of everything, written as a tar to stdout – for another machine to pull over SSH
# (scripts/pull_backup.ps1 on Windows, scripts/pull_backup.sh on Linux). Nothing is kept on this server.
#
# Contents (all consistent copies, taken while the app runs): every account's JSON + .db, users.db (the
# accounts), clubs.db, library.db (reticles). Plus .env and secret_key with PULL_SECRETS=1 – put it in the
# forced command: command="PULL_SECRETS=1 /root/…/backup_stream.sh" (.env holds the Gmail app password).
#
# Set up a key that can do nothing but this – one line in /root/.ssh/authorized_keys:
#   restrict,command="/root/Shooting_Diary_Suite/scripts/backup_stream.sh" ssh-ed25519 AAAA… diary-backup-pull
# `restrict` turns off shells, port forwarding and the rest; whatever the other machine asks to run, this runs.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="${PYTHON:-$REPO_DIR/venv/bin/python}"
DATA_DIR="${DIARY_DATA_DIR:-$REPO_DIR/diary_data}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

cd "$REPO_DIR"
# stdout is the tar: everything the backups print goes to stderr
{
  "$PYTHON" -m diary backup "$TMP" --keep 1
  "$PYTHON" -m club backup "$TMP" --keep 1
  "$PYTHON" -m ballistics backup "$TMP" --keep 1
} >&2
if [ "${PULL_SECRETS:-0}" = 1 ]; then
  for f in "$REPO_DIR/.env" "$DATA_DIR/secret_key"; do [ -f "$f" ] && cp "$f" "$TMP/"; done
fi
date -Is > "$TMP/BACKUP-TAKEN"
mkdir -p "$REPO_DIR/diary_backups" && date -Is > "$REPO_DIR/diary_backups/.last-pull" || true   # when another machine last pulled
tar -C "$TMP" -cf - .
