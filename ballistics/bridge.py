"""Everything the ballistic calculator needs from the Shooting Diary – the only file in ballistics/ that imports diary.

  data_dir()                     where library.db (the shared reticle library) goes
  login_required, current_user   the diary login: the account, with is_admin
  db()                           the logged-in account's own database (profiles, bullets, matches live in it)
  kv_get / kv_set                small per-account settings in that database
  static_page(path)              serve a page with the diary's no-store headers
  guns / loads / scopes          the diary records a profile links to
  chrono_days, chrono_shots      velocities for a gun + load (Velocity page figures)
  stats, temp_sensitivity        the Velocity page's statistics
  register(...)                  per-account schema, backup export and import (diary.EXTENSIONS)
"""
import json
import os
from functools import wraps

from flask import jsonify, redirect, request, send_from_directory

import diary
from diary import users

ID_RE = diary.ID_RE
DATE_RE = diary.DATE_RE
current_user = diary.current_user


def login_required(view):
    """The diary login: API calls without it get 401 JSON (the page sends you to log in), pages a redirect."""
    @wraps(view)
    def wrapper(*args, **kwargs):
        if current_user():
            return view(*args, **kwargs)
        if "/api/" in request.path:
            return jsonify(error="login_required"), 401
        return redirect("/diary/login")
    return wrapper
db = diary.db
kv_get = diary.kv_get
kv_set = diary.kv_set


def data_dir():
    return diary.DATA_DIR


def static_page(folder, name):
    resp = send_from_directory(folder, name, max_age=0)
    resp.headers["Cache-Control"] = "no-store"
    return resp


def account_dbs():
    """Every account's database file (for schema updates at start-up)."""
    paths = [users.db_path(u) for u in users.all_users()]
    first = os.path.join(diary.DATA_DIR, "diary.db")
    return paths or ([first] if os.path.exists(first) else [])


def connect(path):
    return diary.connect(path)


def register(name, init_schema, export_rows, import_rows):
    """Hook ballistics into the diary's per-account database: new accounts get the tables, and the backup
    JSON (python -m diary backup / import, 'My data') carries its rows."""
    if not any(e["name"] == name for e in diary.EXTENSIONS):
        diary.EXTENSIONS.append({"name": name, "init": init_schema, "export": export_rows, "import": import_rows})


# ------------------------------------------------------------------ diary records a profile links to
def guns(conn):
    out = []
    for r in conn.execute("SELECT id, data FROM guns"):
        g = json.loads(r["data"])
        out.append({"id": r["id"], "name": g.get("name") or "", "cal": g.get("cal") or "",
                    "archived": bool(g.get("archived")), "order": g.get("order", 99)})
    out.sort(key=lambda g: (g["archived"], g["order"], g["name"]))
    return out


def loads(conn):
    try:
        from diary.chrono_loads import short_name
        return [{"id": r["id"], "name": r["name"], "short": short_name(dict(r)), "cal": r["cal"],
                 "bullet_gr": r["bullet_gr"], "archived": bool(r["archived"])}
                for r in conn.execute("SELECT * FROM loads ORDER BY archived, name")]
    except Exception:                      # velocity tables not created yet
        return []


def scopes(conn):
    from diary.zero import _load_labels, display_name
    labels = _load_labels(conn)
    out = []
    for s in conn.execute("SELECT * FROM scopes ORDER BY archived, ord, name"):
        s = dict(s)
        zeros = []
        for z in conn.execute("SELECT * FROM scope_zeros WHERE scope_id=? ORDER BY archived, ord, rowid", (s["id"],)):
            z = dict(z)
            zeros.append({"id": z["id"], "name": display_name(z, labels), "gun_id": z["gun_id"],
                          "load_id": z["load_id"], "distance_m": z["distance_m"], "elev": z["elev"],
                          "wind": z["wind"], "archived": bool(z["archived"])})
        out.append({k: s.get(k) for k in ("id", "name", "unit", "click", "zero_stop", "elev_turn", "wind_turn",
                                          "mounted_zero_id", "archived")} | {"zeros": zeros})
    return out


def chrono_days(conn, gun_id, load_id):
    """Per day for this gun + load, newest first: {date, strings, stats, temp_c} (the Velocity page's days)."""
    from diary import chrono
    return chrono.days(conn, gun_id, load_id)


def chrono_shots(conn, string_ids):
    """{string_id: [shot rows]} and {string_id: string temperature}."""
    from diary import chrono
    marks = ",".join("?" * len(string_ids))
    shots = chrono._shots_by_string(conn, f" WHERE st.id IN ({marks})", list(string_ids))
    temps = {r["id"]: r["temp_c"] for r in conn.execute(f"SELECT id, temp_c FROM chrono_strings WHERE id IN ({marks})",
                                                        list(string_ids))}
    return shots, temps


def stats(values):
    from diary.chrono_stats import stats as _stats
    return _stats(values)


def temp_sensitivity(day_points):
    from diary.chrono_stats import temp_sensitivity as _ts
    return _ts(day_points)
