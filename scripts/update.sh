#!/usr/bin/env bash
# Update the site from GitHub and restart it — only if the new code passes its checks.
# Follows the branch that is checked out (main, or e.g. club-ammo while testing it); BRANCH=… overrides.
#
#   ./scripts/update.sh            pull, check, restart
#   ./scripts/update.sh --check    show what would come in, change nothing
#   ./scripts/update.sh --restart  just restart (no pull)
#
# Steps: refuse if there are local edits → snapshot diary.db → git pull → pip install if requirements.txt
# changed → compile + tests (pytest, if installed in the venv) → on failure roll back to the old commit and
# keep the old version running → restart → check the site answers → reset the demo account if there is one.
#
# Restart: set RESTART_CMD (e.g. in the environment or below), or SERVICE for a systemd unit.
#   RESTART_CMD="systemctl restart shooting-diary.service" ./scripts/update.sh
# Without either, the script uses systemd unit shooting-diary.service (or the old name flask-telemetry.service
# if that's the one installed) and otherwise tells you to restart by hand.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="${PYTHON:-$REPO_DIR/venv/bin/python}"
if [ -z "${SERVICE:-}" ]; then
  SERVICE=shooting-diary.service
  if command -v systemctl >/dev/null && ! systemctl cat "$SERVICE" >/dev/null 2>&1 \
     && systemctl cat flask-telemetry.service >/dev/null 2>&1; then
    SERVICE=flask-telemetry.service       # old unit name, until it's renamed
  fi
fi
RESTART_CMD="${RESTART_CMD:-}"
CURRENT="$(git -C "$REPO_DIR" rev-parse --abbrev-ref HEAD)"
BRANCH="${BRANCH:-$CURRENT}"            # the checked-out branch
DATA_DIR="${DIARY_DATA_DIR:-$REPO_DIR/diary_data}"
SNAP_DIR="${SNAP_DIR:-$REPO_DIR/diary_backups/pre-update}"   # git-ignored (diary_backups/)
SNAP_KEEP="${SNAP_KEEP:-5}"
PORT="${PORT:-5000}"
HEALTH_URL="${HEALTH_URL:-http://$(hostname -I 2>/dev/null | awk '{print $1}'):$PORT/diary/login}"

say()  { printf '\n== %s\n' "$*"; }
fail() { printf '\n!! %s\n' "$*" >&2; exit 1; }

cd "$REPO_DIR"
MODE="${1:-}"

restart() {
  if [ -n "$RESTART_CMD" ]; then
    say "Restarting: $RESTART_CMD"; eval "$RESTART_CMD"
  elif command -v systemctl >/dev/null && systemctl cat "$SERVICE" >/dev/null 2>&1; then
    say "Restarting systemd unit $SERVICE"; systemctl restart "$SERVICE"
  else
    say "Restart the site yourself now (no RESTART_CMD set and no systemd unit '$SERVICE')."
    return 0
  fi
  # Give it a moment, then check it answers
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    sleep 1
    if curl -fsS -o /dev/null "$HEALTH_URL" 2>/dev/null; then say "Site answers at $HEALTH_URL"; return 0; fi
  done
  fail "No answer from $HEALTH_URL after the restart – check the service log."
}

if [ "$MODE" = "--restart" ]; then restart; exit 0; fi

# 1. Local edits would be overwritten or block the pull
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  git status --short --untracked-files=no
  fail "Local changes in tracked files. Commit or undo them first (git checkout -- <file>), or reset with: git reset --hard origin/$BRANCH"
fi
[ "$CURRENT" != "HEAD" ] || fail "No branch checked out (detached HEAD) – git checkout main"
[ "$CURRENT" = "$BRANCH" ] || fail "On $CURRENT, not $BRANCH (git checkout $BRANCH)."
[ "$BRANCH" = "main" ] || echo "Note: following branch $BRANCH, not main (back with: git checkout main && $0)"

say "Fetching origin/$BRANCH"
git fetch --quiet origin "+refs/heads/$BRANCH:refs/remotes/origin/$BRANCH"
OLD="$(git rev-parse HEAD)"
NEW="$(git rev-parse "origin/$BRANCH")"
if [ "$OLD" = "$NEW" ]; then
  say "Already up to date ($(git log -1 --format='%h %s'))."
  exit 0
fi
git merge-base --is-ancestor "$OLD" "$NEW" || fail "Local $BRANCH has commits that aren't on GitHub – sort that out by hand."

echo "Coming in:"
git log --format='  %h %s' "$OLD..$NEW"
if [ "$MODE" = "--check" ]; then exit 0; fi

# 2. Snapshot every database (accounts + each account's data) before new code and its migrations touch them
if ls "$DATA_DIR"/*.db >/dev/null 2>&1; then
  SNAP="$SNAP_DIR/$(date +%Y%m%d-%H%M%S)-${OLD:0:7}"
  mkdir -p "$SNAP"
  "$PYTHON" - "$DATA_DIR" "$SNAP" <<'PY'
import glob, os, sqlite3, sys
data, out = sys.argv[1], sys.argv[2]
for src_path in glob.glob(os.path.join(data, "*.db")) + glob.glob(os.path.join(data, "users", "*.db")):
    rel = os.path.relpath(src_path, data)
    dst_path = os.path.join(out, rel)
    os.makedirs(os.path.dirname(dst_path), exist_ok=True)
    src, dst = sqlite3.connect(src_path), sqlite3.connect(dst_path)
    with dst:
        src.backup(dst)
    src.close(); dst.close()
PY
  say "Database snapshot: $SNAP"
  ls -1dt "$SNAP_DIR"/*/ 2>/dev/null | tail -n +"$((SNAP_KEEP + 1))" | xargs -r rm -r --
fi

rollback() {
  printf '\n!! %s – rolling back to %s\n' "$1" "${OLD:0:7}" >&2
  git reset --quiet --hard "$OLD"
  if [ "$REQ_CHANGED" = 1 ]; then "$PYTHON" -m pip install --quiet -r requirements.txt || true; fi
  fail "Update aborted. The old version is still the one on disk; nothing was restarted."
}

# 3. Pull
say "Updating ${OLD:0:7} → ${NEW:0:7}"
git merge --quiet --ff-only "$NEW"

REQ_CHANGED=0
if ! git diff --quiet "$OLD" "$NEW" -- requirements.txt; then
  REQ_CHANGED=1
  say "requirements.txt changed – installing"
  "$PYTHON" -m pip install --quiet -r requirements.txt || rollback "pip install failed"
fi

# 4. Checks: everything compiles, the app imports, tests pass (tests use a throwaway data folder)
say "Checking"
COMPILE="diary main.py"; [ -d club ] && COMPILE="$COMPILE club"
"$PYTHON" -m compileall -q $COMPILE >/dev/null || rollback "Python files don't compile"
DIARY_DATA_DIR="$(mktemp -d)" "$PYTHON" -c "import main" \
  || rollback "The app doesn't start"
if "$PYTHON" -c "import pytest" 2>/dev/null; then
  "$PYTHON" -m pytest -q tests || rollback "Tests failed"
else
  echo "  (pytest not installed in the venv – skipping tests; install with: $PYTHON -m pip install pytest)"
fi

# 5. Restart
restart

# 6. Demo account (if there is one): fresh sample data made by the new code. Skip with DEMO_RESET=0.
if [ "${DEMO_RESET:-1}" != "0" ] && "$PYTHON" -c "from diary.demo import demo_user; import sys; sys.exit(0 if demo_user() else 1)" 2>/dev/null; then
  say "Resetting the demo account"
  "$PYTHON" -m diary demo-reset || echo "  (demo reset failed – the site itself is updated; run: $PYTHON -m diary demo-reset)"
fi
say "Updated to $(git log -1 --format='%h %s')"
