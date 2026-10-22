"""Health check for monitoring (e.g. Uptime Kuma): GET /diary/health, no login.

200 + "status": "ok"        everything fine
200 + "status": "degraded"  the site works, but disk space is low or the newest backup is old
503 + "status": "down"      the databases can't be read

Only check results are shown – no account names, counts or paths.
  DIARY_BACKUP_DIR        where the weekly backups go (default: diary_backups next to the code)
  DIARY_BACKUP_MAX_DAYS   newest backup older than this → degraded (default 8; 0 = don't check)
  DIARY_DISK_MIN_MB       free space below this → degraded (default 1024)
"""
import glob
import os
import shutil
import sqlite3
import subprocess
import time

from flask import jsonify

from . import DATA_DIR, HERE, bp

STARTED = time.time()


def _version():
    try:
        return subprocess.run(["git", "-C", os.path.dirname(HERE), "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True, timeout=3).stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


VERSION = _version()


def _db_ok():
    from . import users
    try:
        with users.uconnect() as c:
            c.execute("SELECT COUNT(*) FROM users").fetchone()
        for u in users.all_users(include_disabled=False):
            conn = sqlite3.connect(f"file:{users.db_path(u)}?mode=ro", uri=True, timeout=3)
            try:
                conn.execute("SELECT 1 FROM sessions LIMIT 1").fetchall()
            finally:
                conn.close()
        return True
    except sqlite3.Error:
        return False


def _backup_age_days():
    d = os.getenv("DIARY_BACKUP_DIR") or os.path.join(os.path.dirname(HERE), "diary_backups")
    files = glob.glob(os.path.join(d, "diary-*-[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9].json"))
    return None if not files else round((time.time() - max(os.path.getmtime(f) for f in files)) / 86400, 1)


@bp.get("/health")
def health():
    checks = {"database": "ok" if _db_ok() else "fail"}
    try:
        free_mb = shutil.disk_usage(DATA_DIR).free // (1024 * 1024)
        checks["disk"] = "ok" if free_mb >= int(os.getenv("DIARY_DISK_MIN_MB", "1024")) else "low"
    except OSError:
        free_mb, checks["disk"] = None, "unknown"
    max_days = float(os.getenv("DIARY_BACKUP_MAX_DAYS", "8"))
    age = _backup_age_days()
    if max_days > 0:
        checks["backup"] = "none" if age is None else ("ok" if age <= max_days else "stale")
    status = "down" if checks["database"] != "ok" else ("ok" if all(v == "ok" for v in checks.values()) else "degraded")
    body = {"status": status, "checks": checks, "backup_age_days": age, "disk_free_mb": free_mb,
            "version": VERSION, "uptime_s": int(time.time() - STARTED)}
    resp = jsonify(body)
    resp.status_code = 503 if status == "down" else 200
    resp.headers["Cache-Control"] = "no-store"
    return resp
