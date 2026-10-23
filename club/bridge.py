"""Everything club ammo needs from the Shooting Diary – the only file in club/ that imports diary.

Moving club/ to its own repository means rewriting just this file (e.g. as calls to the diary's API):
  data_dir()                        where clubs.db goes
  current_user()                    the logged-in diary account or None: dict with id, username, is_admin
  get_user(uid) / user_by_name(n)   diary accounts, for roles and box holders
  display_name(user)                the name to fill in on the shooter form
  add_rounds(user, ...)             "Also add to my diary": the rounds onto that day's visit
  remove_rounds(uid, tag)           the shooter took the entry back
"""
import json
import secrets
import sqlite3

import diary
from diary import users


def data_dir():
    return diary.DATA_DIR


def login_url():
    return "/diary/login"


def current_user():
    return diary.current_user()


def get_user(uid):
    return users.get(uid)


def user_by_name(name):
    return users.by_name(name)


def _conn(user):
    return diary.connect(users.db_path(user))


def display_name(user):
    """Name from the diary profile, else the username."""
    if not user:
        return ""
    try:
        conn = _conn(user)
        try:
            p = json.loads(diary.kv_get("profile", conn) or "{}") or {}
        finally:
            conn.close()
        return (p.get("name") or "").strip() or user["username"]
    except sqlite3.Error:
        return user["username"]


def add_rounds(user, date, rounds, calibre, gun_type, info, place, tag):
    """Onto that day's latest visit, or a new visit. The line carries `tag` so it can be taken back."""
    conn = _conn(user)
    try:
        line = {"type": gun_type or "", "cal": calibre, "mode": "", "rounds": rounds, "info": info, "club": tag}
        r = conn.execute("SELECT id, data FROM sessions WHERE date=? ORDER BY updated_at DESC LIMIT 1", (date,)).fetchone()
        if r:
            s, sid = json.loads(r["data"]), r["id"]
        else:
            s, sid = {"date": date, "start": "", "end": "", "place": "", "org": place, "lines": []}, "c" + secrets.token_hex(6)
        s.setdefault("lines", []).append(line)
        diary.upsert_session(conn, sid, s)
        conn.commit()
        return True
    finally:
        conn.close()


def remove_rounds(uid, tag):
    u = users.get(uid)
    if not u:
        return
    conn = _conn(u)
    try:
        for r in conn.execute("SELECT id, data FROM sessions").fetchall():
            s = json.loads(r["data"])
            lines = s.get("lines", [])
            keep = [ln for ln in lines if ln.get("club") != tag]
            if len(keep) == len(lines):
                continue
            if not keep and r["id"].startswith("c") and not (s.get("place") or s.get("start")):
                conn.execute("DELETE FROM sessions WHERE id=?", (r["id"],))     # the visit only had this
            else:
                s["lines"] = keep
                diary.upsert_session(conn, r["id"], s)
        conn.commit()
    finally:
        conn.close()
