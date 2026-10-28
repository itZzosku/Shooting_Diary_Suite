"""Ballistic calculator: profiles, bullets, matches and stages. The trajectory itself is computed in the browser
(static/ballistics/solver.js) so it works offline at the range; the server stores the records and resolves a
profile's links to the diary (gun, load, scope, scope zero) into the numbers the page needs.

Tables in each account's diary database:
  bal_bullets       bullet: diameter, length, weight, drag model, BC (or velocity bands)
  bal_curves        custom Mach → Cd drag curves (phase 3; table only)
  bal_profiles      gun + load + bullet + scope + zero, sight height, twist, zero atmosphere, muzzle velocity
  bal_observations  dope confirmed at the range, for truing (phase 3; table only)
  bal_matches       a match: name, date, optional range visit
  bal_stages        a stage: ordered target list, dial strategy, stage wind
Design: "Ballistic Calculator — Design Document".
"""
import json
import re
import secrets
import time

from flask import abort, jsonify, request

from . import ID_RE, DATE_RE, bp, db, kv_get, kv_set, login_required, static_page

SCHEMA = """
CREATE TABLE IF NOT EXISTS bal_bullets (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, maker TEXT,
    diameter_mm REAL NOT NULL, length_mm REAL, weight_gr REAL NOT NULL,
    drag_model TEXT NOT NULL DEFAULT 'G7', bc REAL, bc_bands TEXT, curve_id TEXT,
    notes TEXT DEFAULT '', archived INTEGER NOT NULL DEFAULT 0, updated TEXT);
CREATE TABLE IF NOT EXISTS bal_curves (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, points TEXT NOT NULL,
    source TEXT DEFAULT '', notes TEXT DEFAULT '', updated TEXT);
CREATE TABLE IF NOT EXISTS bal_profiles (
    id TEXT PRIMARY KEY, name TEXT NOT NULL,
    gun_id TEXT, load_id TEXT, bullet_id TEXT, scope_id TEXT, zero_id TEXT, reticle_id TEXT,
    sight_height_mm REAL NOT NULL DEFAULT 50, twist_mm REAL, twist_dir TEXT NOT NULL DEFAULT 'R',
    zero_range_m REAL NOT NULL DEFAULT 100, zero_temp_c REAL, zero_hpa REAL, zero_rh REAL,
    mv_ms REAL, mv_temp_c REAL, mv_source TEXT NOT NULL DEFAULT 'manual',
    temp_sens REAL, temp_sens_source TEXT NOT NULL DEFAULT 'manual',
    mv_scale REAL NOT NULL DEFAULT 1, drag_scale REAL NOT NULL DEFAULT 1,
    latitude REAL, notes TEXT DEFAULT '',
    ord INTEGER NOT NULL DEFAULT 0, archived INTEGER NOT NULL DEFAULT 0, updated TEXT);
CREATE TABLE IF NOT EXISTS bal_observations (
    id TEXT PRIMARY KEY, profile_id TEXT NOT NULL, date TEXT NOT NULL, visit_id TEXT,
    range_m REAL NOT NULL, elev REAL NOT NULL, unit TEXT NOT NULL,
    temp_c REAL, hpa REAL, rh REAL, angle_deg REAL, mv_ms REAL,
    use_for_truing INTEGER NOT NULL DEFAULT 1, note TEXT DEFAULT '', op_id TEXT, created TEXT);
CREATE INDEX IF NOT EXISTS bal_obs_profile ON bal_observations(profile_id, date);
CREATE TABLE IF NOT EXISTS bal_matches (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, date TEXT, visit_id TEXT,
    notes TEXT DEFAULT '', archived INTEGER NOT NULL DEFAULT 0, updated TEXT);
CREATE TABLE IF NOT EXISTS bal_stages (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, match_id TEXT, ord INTEGER NOT NULL DEFAULT 0, profile_id TEXT,
    dial_mode TEXT NOT NULL DEFAULT 'each', dial_target INTEGER, wind TEXT, targets TEXT NOT NULL,
    notes TEXT DEFAULT '', updated TEXT);
CREATE INDEX IF NOT EXISTS bal_stages_match ON bal_stages(match_id, ord);
"""
TABLES = ("bal_bullets", "bal_curves", "bal_profiles", "bal_observations", "bal_matches", "bal_stages")
DRAG_MODELS = ("G1", "G7")          # RA4 and custom curves come later (see the design doc's open questions)
DIAL_MODES = ("each", "first_hold_rest", "one")
SETTINGS_KEY = "bal_settings"
RANGES_KEY = "bal_ranges"            # saved shooting ranges: [{id, name, lat, lon, alt_m}]
DEFAULT_SETTINGS = {"card_start": 100, "card_step": 50, "card_end": 1000}


PROFILE_COLUMNS = {             # added after phase 1: chrono window and the small corrections
    "mv_strings": "INTEGER", "spin_drift": "INTEGER NOT NULL DEFAULT 0", "coriolis": "INTEGER NOT NULL DEFAULT 0",
    "aero_jump": "INTEGER NOT NULL DEFAULT 0", "azimuth_deg": "REAL"}


def init_ballistics(conn):
    conn.executescript(SCHEMA)
    have = {r[1] for r in conn.execute("PRAGMA table_info(bal_profiles)")}
    for col, typ in PROFILE_COLUMNS.items():
        if col not in have:
            conn.execute(f"ALTER TABLE bal_profiles ADD COLUMN {col} {typ}")


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def new_id(text, prefix):
    s = re.sub(r"[^A-Za-z0-9]+", "-", str(text)).strip("-").lower()
    return (prefix + s)[:56].rstrip("-") + "-" + secrets.token_hex(2)


# ------------------------------------------------------------------ validation
def _num(lo, hi, required=False):
    def check(v, name):
        if v is None or v == "":
            if required:
                raise ValueError(name + "_required")
            return None
        try:
            f = float(v)
        except (TypeError, ValueError):
            raise ValueError("bad_" + name)
        if not (lo <= f <= hi) or f != f:
            raise ValueError("bad_" + name)
        return f
    return check


def _int(lo, hi):
    def check(v, name):
        if v is None or v == "":
            return None
        try:
            i = int(v)
        except (TypeError, ValueError):
            raise ValueError("bad_" + name)
        if not lo <= i <= hi:
            raise ValueError("bad_" + name)
        return i
    return check


def _text(maxlen, required=False):
    def check(v, name):
        s = " ".join(str(v or "").split())[:maxlen] if name != "notes" else str(v or "")[:maxlen]
        if required and not s.strip():
            raise ValueError(name + "_required")
        return s
    return check


def _ref(v, name):
    if v is None or v == "":
        return None
    if not ID_RE.match(str(v)):
        raise ValueError("bad_" + name)
    return str(v)


def _choice(options, default):
    def check(v, name):
        if v is None or v == "":
            return default
        if v not in options:
            raise ValueError("bad_" + name)
        return v
    return check


def _flag(v, name):
    return 1 if v in (True, 1, "1", "true") else 0


def _date(v, name):
    if v is None or v == "":
        return None
    if not DATE_RE.match(str(v)):
        raise ValueError("bad_" + name)
    return str(v)


def _bands(v, name):
    """Multi-BC: [[v_min m/s, bc], ...], 1–8 bands, velocities ascending from 0."""
    if v in (None, "", []):
        return None
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except ValueError:
            raise ValueError("bad_" + name)
    if not isinstance(v, list) or not 1 <= len(v) <= 8:
        raise ValueError("bad_" + name)
    out = []
    for item in v:
        try:
            vel, bc = float(item[0]), float(item[1])
        except (TypeError, ValueError, IndexError):
            raise ValueError("bad_" + name)
        if not (0 <= vel <= 1500 and 0.05 <= bc <= 1.5) or (out and vel <= out[-1][0]):
            raise ValueError("bad_" + name)
        out.append([vel, bc])
    return json.dumps(out)


def _wind(v, name):
    if v in (None, "", {}):
        return None
    if not isinstance(v, dict):
        raise ValueError("bad_" + name)
    ms = _num(0, 30)(v.get("ms"), "wind_ms") or 0
    clock = _num(0, 12)(v.get("clock"), "wind_clock") or 0
    return {"ms": ms, "clock": clock}


def _targets(v, name):
    if not isinstance(v, list) or not 1 <= len(v) <= 30:
        raise ValueError("bad_" + name)
    out = []
    for i, t in enumerate(v):
        if not isinstance(t, dict):
            raise ValueError("bad_" + name)
        out.append({
            "label": _text(20)(t.get("label") or f"T{i + 1}", "label"),
            "range_m": _num(1, 3000, required=True)(t.get("range_m"), "range"),
            "w_cm": _num(0, 1000)(t.get("w_cm"), "width"),
            "h_cm": _num(0, 1000)(t.get("h_cm"), "height"),
            "angle_deg": _num(-60, 60)(t.get("angle_deg"), "angle"),
            "wind": _wind(t.get("wind"), "wind"),
        })
    return json.dumps(out)


def _json_wind(v, name):
    w = _wind(v, name)
    return json.dumps(w) if w else None


BULLET = {
    "name": _text(80, True), "maker": _text(40), "diameter_mm": _num(2, 20, True), "length_mm": _num(3, 80),
    "weight_gr": _num(10, 1000, True), "drag_model": _choice(DRAG_MODELS, "G7"), "bc": _num(0.05, 1.5),
    "bc_bands": _bands, "notes": _text(2000), "archived": _flag,
}
PROFILE = {
    "name": _text(80, True), "gun_id": _ref, "load_id": _ref, "bullet_id": _ref, "scope_id": _ref, "zero_id": _ref,
    "sight_height_mm": _num(10, 150, True), "twist_mm": _num(150, 800), "twist_dir": _choice(("R", "L"), "R"),
    "zero_range_m": _num(25, 600, True), "zero_temp_c": _num(-40, 60), "zero_hpa": _num(500, 1100),
    "zero_rh": _num(0, 100), "mv_ms": _num(100, 1500), "mv_temp_c": _num(-40, 60), "temp_sens": _num(-5, 5),
    "mv_scale": _num(0.8, 1.2), "drag_scale": _num(0.5, 1.5), "latitude": _num(-90, 90),
    "notes": _text(2000), "ord": _int(0, 100000), "archived": _flag,
    "mv_source": _choice(("manual", "chrono"), "manual"), "temp_sens_source": _choice(("manual", "chrono"), "manual"),
    "mv_strings": _int(1, 50), "spin_drift": _flag, "coriolis": _flag, "aero_jump": _flag,
    "azimuth_deg": _num(0, 360), "reticle_id": _ref,
}
MATCH = {"name": _text(80, True), "date": _date, "visit_id": _ref, "notes": _text(2000), "archived": _flag}
STAGE = {
    "name": _text(80, True), "match_id": _ref, "ord": _int(0, 1000), "profile_id": _ref,
    "dial_mode": _choice(DIAL_MODES, "each"), "dial_target": _int(0, 29), "wind": _json_wind,
    "targets": _targets, "notes": _text(2000),
}
DEFAULTS = {"bal_profiles": {"mv_scale": 1, "drag_scale": 1, "ord": 0}, "bal_stages": {"ord": 0}}


def clean(spec, body):
    return {k: check(body.get(k), k) for k, check in spec.items()}


def upsert(conn, table, rid, row):
    row = dict(row, updated=_now())
    for k, v in DEFAULTS.get(table, {}).items():
        if row.get(k) is None:
            row[k] = v
    cols = list(row)
    conn.execute(f"INSERT INTO {table}(id,{','.join(cols)}) VALUES(?,{','.join('?' * len(cols))}) "
                 f"ON CONFLICT(id) DO UPDATE SET {','.join(f'{c}=excluded.{c}' for c in cols)}",
                 [rid, *[row[c] for c in cols]])


# ------------------------------------------------------------------ reads
def _rows(conn, sql, args=()):
    return [dict(r) for r in conn.execute(sql, args)]


def bullet_rows(conn):
    rows = _rows(conn, "SELECT * FROM bal_bullets ORDER BY archived, name")
    for r in rows:
        r["bc_bands"] = json.loads(r["bc_bands"]) if r["bc_bands"] else None
    return rows


def stage_rows(conn):
    rows = _rows(conn, "SELECT * FROM bal_stages ORDER BY match_id, ord, name")
    for r in rows:
        r["targets"] = json.loads(r["targets"])
        r["wind"] = json.loads(r["wind"]) if r["wind"] else None
    return rows


def pickers(conn):
    """The diary records a profile can link to, in the shape the profile sheet needs."""
    guns = []
    for r in conn.execute("SELECT id, data FROM guns"):
        g = json.loads(r["data"])
        guns.append({"id": r["id"], "name": g.get("name") or "", "cal": g.get("cal") or "",
                     "archived": bool(g.get("archived")), "order": g.get("order", 99)})
    guns.sort(key=lambda g: (g["archived"], g["order"], g["name"]))
    try:
        from .chrono_loads import short_name
        loads = [{"id": r["id"], "name": r["name"], "short": short_name(dict(r)), "cal": r["cal"],
                  "bullet_gr": r["bullet_gr"], "archived": bool(r["archived"])}
                 for r in conn.execute("SELECT * FROM loads ORDER BY archived, name")]
    except Exception:                      # velocity tables not created yet
        loads = []
    from .zero import _load_labels, display_name
    labels = _load_labels(conn)
    scopes = []
    for s in conn.execute("SELECT * FROM scopes ORDER BY archived, ord, name"):
        s = dict(s)
        zeros = []
        for z in conn.execute("SELECT * FROM scope_zeros WHERE scope_id=? ORDER BY archived, ord, rowid", (s["id"],)):
            z = dict(z)
            zeros.append({"id": z["id"], "name": display_name(z, labels), "gun_id": z["gun_id"],
                          "load_id": z["load_id"], "distance_m": z["distance_m"], "elev": z["elev"],
                          "wind": z["wind"], "archived": bool(z["archived"])})
        scopes.append({k: s.get(k) for k in ("id", "name", "unit", "click", "zero_stop", "elev_turn", "wind_turn",
                                              "mounted_zero_id", "archived")} | {"zeros": zeros})
    return {"guns": guns, "loads": loads, "scopes": scopes}


DEFAULT_STRINGS = 3


def chrono_velocity(conn, gun_id, load_id, n_strings=DEFAULT_STRINGS):
    """Muzzle velocity from the newest chrono strings for this gun + load (excluded shots dropped), and the
    temperature sensitivity over every day of them – the same figures the Velocity page shows."""
    if not (gun_id and load_id):
        return None
    try:
        from . import chrono
        ds = chrono.days(conn, gun_id, load_id)          # newest first
    except Exception:                                    # velocity tables not created yet
        return None
    if not ds:
        return None
    picked = []
    for d in ds:
        picked += d["strings"]
        if len(picked) >= n_strings:
            break
    picked = picked[:max(n_strings, 1)]
    marks = ",".join("?" * len(picked))
    shots = chrono._shots_by_string(conn, f" WHERE st.id IN ({marks})", picked)
    stemp = {r["id"]: r["temp_c"] for r in conn.execute(f"SELECT id, temp_c FROM chrono_strings WHERE id IN ({marks})", picked)}
    v, temps = [], []
    for sid in picked:
        for x in shots.get(sid, []):
            if x["excluded"]:
                continue
            v.append(x["v_ms"])
            t = x["temp_c"] if x["temp_c"] is not None else stemp.get(sid)
            if t is not None:
                temps.append(t)
    if not v:
        return None
    from .chrono_stats import stats, temp_sensitivity
    st = stats(v)
    dates = sorted(d["date"] for d in ds if set(d["strings"]) & set(picked))
    return {"mv": st["avg"], "sd": st["sd"], "es": st["es"], "n": st["n"], "strings": len(picked),
            "from": dates[0], "to": dates[-1], "temp_c": round(sum(temps) / len(temps), 1) if temps else None,
            "temp_sens": temp_sensitivity([(d["temp_c"], d["stats"]["avg"]) for d in ds]),
            "days": len(ds)}


def resolve_inputs(profile, bullets, picks, conn=None):
    """One flat object of what the solver and the readout need, each value with where it came from."""
    by = lambda rows, rid: next((r for r in rows if r["id"] == rid), None)  # noqa: E731
    b = by(bullets, profile["bullet_id"]) if profile["bullet_id"] else None
    scope = by(picks["scopes"], profile["scope_id"]) if profile["scope_id"] else None
    zero = by(scope["zeros"], profile["zero_id"]) if scope and profile["zero_id"] else None
    gun = by(picks["guns"], profile["gun_id"]) if profile["gun_id"] else None
    load = by(picks["loads"], profile["load_id"]) if profile["load_id"] else None
    warn = []
    if not b:
        warn.append("no_bullet")
    if profile["mv_ms"] is None:
        warn.append("no_velocity")
    if b and load and load.get("bullet_gr") and abs(load["bullet_gr"] - b["weight_gr"]) > 0.5:
        warn.append("bullet_weight_differs")
    ch = None
    if conn is not None and (profile.get("mv_source") == "chrono" or profile.get("temp_sens_source") == "chrono"):
        ch = chrono_velocity(conn, profile["gun_id"], profile["load_id"], profile.get("mv_strings") or DEFAULT_STRINGS)
    mv, mv_t, mv_src = profile["mv_ms"], profile["mv_temp_c"], "manual"
    if profile.get("mv_source") == "chrono":
        if ch:
            mv, mv_t, mv_src = ch["mv"], ch["temp_c"], "chrono"
        else:
            warn.append("no_chrono_data")
    ts, ts_src = profile["temp_sens"], "manual"
    if profile.get("temp_sens_source") == "chrono":
        if ch and ch["temp_sens"] is not None:
            ts, ts_src = ch["temp_sens"], "chrono"
        else:
            warn.append("no_chrono_temp_sens")
    if mv is None and "no_velocity" not in warn:
        warn.append("no_velocity")
    elif mv is not None and "no_velocity" in warn:
        warn.remove("no_velocity")
    return {
        "bullet": b and {"model": b["drag_model"], "bc": b["bc"], "bands": b["bc_bands"],
                         "diameterMm": b["diameter_mm"], "lengthMm": b["length_mm"], "weightGr": b["weight_gr"],
                         "name": b["name"]},
        "mv": mv, "mvTempC": mv_t, "tempSens": ts, "mvSource": mv_src, "tempSensSource": ts_src, "chrono": ch,
        "mvScale": profile["mv_scale"], "dragScale": profile["drag_scale"],
        "spinDrift": bool(profile.get("spin_drift")), "coriolis": bool(profile.get("coriolis")),
        "aeroJump": bool(profile.get("aero_jump")), "azimuthDeg": profile.get("azimuth_deg"),
        "reticleId": profile.get("reticle_id"),
        "sightHeightMm": profile["sight_height_mm"], "zeroRangeM": profile["zero_range_m"],
        "zeroAtmo": {"tempC": profile["zero_temp_c"], "hpa": profile["zero_hpa"], "rh": profile["zero_rh"]},
        "twistMm": profile["twist_mm"], "twistDir": profile["twist_dir"], "latitude": profile["latitude"],
        "scope": scope and {"name": scope["name"], "unit": scope["unit"] or "MRAD", "click": scope["click"] or 0.1,
                            "zeroStop": scope["zero_stop"], "elevTurn": scope["elev_turn"],
                            "windTurn": scope["wind_turn"]},
        "zero": zero and {"name": zero["name"], "distanceM": zero["distance_m"], "elev": zero["elev"] or 0,
                          "wind": zero["wind"] or 0},
        "gun": gun and gun["name"], "load": load and load["short"],
        "warnings": warn,
    }


def settings(conn):
    stored = kv_get(SETTINGS_KEY, conn)
    return dict(DEFAULT_SETTINGS, **(json.loads(stored) if stored else {}))


def saved_ranges(conn):
    stored = kv_get(RANGES_KEY, conn)
    return json.loads(stored) if stored else []


def everything(conn):
    picks = pickers(conn)
    bullets = bullet_rows(conn)
    profiles = _rows(conn, "SELECT * FROM bal_profiles ORDER BY archived, ord, name")
    for p in profiles:
        p["inputs"] = resolve_inputs(p, bullets, picks, conn)
    from .reticles import all_reticles
    from . import current_user
    me = current_user()
    return {"profiles": profiles, "bullets": bullets,
            "reticles": all_reticles({p["reticle_id"] for p in profiles if p["reticle_id"]}),
            "is_admin": bool(me and me["is_admin"] and not me.get("demo")),
            "matches": _rows(conn, "SELECT * FROM bal_matches ORDER BY archived, date DESC, name"),
            "stages": stage_rows(conn), "settings": settings(conn), "ranges": saved_ranges(conn), "pickers": picks}


def summary(conn):
    """Home tile: number of profiles and the next or latest match."""
    n = conn.execute("SELECT COUNT(*) FROM bal_profiles WHERE NOT archived").fetchone()[0]
    m = conn.execute("SELECT name, date FROM bal_matches WHERE NOT archived ORDER BY date DESC LIMIT 1").fetchone()
    return {"profiles": n, "match": dict(m) if m else None}


# ------------------------------------------------------------------ backup
def export_rows(conn):
    out = {t: _rows(conn, f"SELECT * FROM {t} ORDER BY rowid") for t in TABLES}
    out["bal_kv"] = {k: kv_get(k, conn) for k in (SETTINGS_KEY, RANGES_KEY) if kv_get(k, conn) is not None}
    return out


def import_rows(conn, data, replace=False):
    if replace and any(t in data for t in TABLES):
        for t in TABLES:
            conn.execute(f"DELETE FROM {t}")
    for t in TABLES:
        have = {r[1] for r in conn.execute(f"PRAGMA table_info({t})")}
        for row in data.get(t, []):
            cols = [c for c in row if c in have]
            conn.execute(f"INSERT OR REPLACE INTO {t}({','.join(cols)}) VALUES({','.join('?' * len(cols))})",
                         [row[c] for c in cols])
    for k, v in (data.get("bal_kv") or {}).items():
        if k in (SETTINGS_KEY, RANGES_KEY) and isinstance(v, str):
            conn.execute("INSERT INTO kv(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, v))
    return {t: len(data.get(t, [])) for t in TABLES}


# ------------------------------------------------------------------ routes
@bp.get("/ballistics")
@login_required
def page_ballistics():
    return static_page("ballistics.html")


def _body():
    if not request.is_json:              # JSON only: a cross-site form can't send it (CSRF)
        abort(415)
    b = request.get_json(silent=True)
    if not isinstance(b, dict):
        abort(400)
    return b


def _id(v):
    if not ID_RE.match(v or ""):
        abort(400)
    return v


def _fetch_only():
    if request.headers.get("X-Requested-With") != "fetch":
        abort(403)


@bp.get("/api/ballistics")
@login_required
def api_bal_all():
    return jsonify(everything(db()))


def _put(table, spec, rid, extra=None):
    rid = _id(rid)
    try:
        row = clean(spec, _body())
        if extra:
            extra(row)
    except ValueError as e:
        return jsonify(error=str(e)), 400
    conn = db()
    upsert(conn, table, rid, row)
    conn.commit()
    return jsonify(ok=True, id=rid)


def _bullet_rules(row):
    if row["bc"] is None and not row["bc_bands"]:
        raise ValueError("bc_required")


def _stage_rules(row):
    if row["dial_mode"] == "one" and row["dial_target"] is None:
        row["dial_target"] = 0


@bp.put("/api/ballistics/bullets/<bid>")
@login_required
def api_bal_put_bullet(bid):
    return _put("bal_bullets", BULLET, bid, _bullet_rules)


@bp.delete("/api/ballistics/bullets/<bid>")
@login_required
def api_bal_delete_bullet(bid):
    _fetch_only()
    conn = db()
    n = conn.execute("SELECT COUNT(*) FROM bal_profiles WHERE bullet_id=?", (_id(bid),)).fetchone()[0]
    if n:
        return jsonify(error="in_use", profiles=n), 409
    conn.execute("DELETE FROM bal_bullets WHERE id=?", (bid,))
    conn.commit()
    return jsonify(ok=True)


@bp.put("/api/ballistics/profiles/<pid>")
@login_required
def api_bal_put_profile(pid):
    res = _put("bal_profiles", PROFILE, pid)
    if isinstance(res, tuple):              # validation error
        return res
    conn = db()
    p = dict(conn.execute("SELECT * FROM bal_profiles WHERE id=?", (pid,)).fetchone())
    p["inputs"] = resolve_inputs(p, bullet_rows(conn), pickers(conn), conn)
    return jsonify(ok=True, id=pid, profile=p)


@bp.delete("/api/ballistics/profiles/<pid>")
@login_required
def api_bal_delete_profile(pid):
    _fetch_only()
    conn = db()
    conn.execute("DELETE FROM bal_observations WHERE profile_id=?", (_id(pid),))
    conn.execute("UPDATE bal_stages SET profile_id=NULL WHERE profile_id=?", (pid,))
    conn.execute("DELETE FROM bal_profiles WHERE id=?", (pid,))
    conn.commit()
    return jsonify(ok=True)


@bp.put("/api/ballistics/matches/<mid>")
@login_required
def api_bal_put_match(mid):
    return _put("bal_matches", MATCH, mid)


@bp.delete("/api/ballistics/matches/<mid>")
@login_required
def api_bal_delete_match(mid):
    _fetch_only()
    conn = db()
    conn.execute("DELETE FROM bal_stages WHERE match_id=?", (_id(mid),))
    conn.execute("DELETE FROM bal_matches WHERE id=?", (mid,))
    conn.commit()
    return jsonify(ok=True)


@bp.put("/api/ballistics/stages/<sid>")
@login_required
def api_bal_put_stage(sid):
    return _put("bal_stages", STAGE, sid, _stage_rules)


@bp.delete("/api/ballistics/stages/<sid>")
@login_required
def api_bal_delete_stage(sid):
    _fetch_only()
    conn = db()
    conn.execute("DELETE FROM bal_stages WHERE id=?", (_id(sid),))
    conn.commit()
    return jsonify(ok=True)


@bp.put("/api/ballistics/settings")
@login_required
def api_bal_put_settings():
    b = _body()
    try:
        s = {"card_start": _num(0, 3000, True)(b.get("card_start"), "card_start"),
             "card_step": _num(1, 500, True)(b.get("card_step"), "card_step"),
             "card_end": _num(10, 3000, True)(b.get("card_end"), "card_end")}
        if s["card_end"] <= s["card_start"]:
            raise ValueError("bad_card_end")
    except ValueError as e:
        return jsonify(error=str(e)), 400
    kv_set(SETTINGS_KEY, json.dumps(s))
    return jsonify(ok=True, settings=s)


RANGE = {"name": _text(60, True), "lat": _num(-90, 90, True), "lon": _num(-180, 180, True), "alt_m": _num(-500, 9000)}


@bp.put("/api/ballistics/ranges")
@login_required
def api_bal_put_ranges():
    """The whole list of saved ranges (venue name, position, altitude), replaced at once."""
    b = request.get_json(silent=True) if request.is_json else abort(415)
    if not isinstance(b, list) or len(b) > 100:
        return jsonify(error="bad_ranges"), 400
    out = []
    try:
        for r in b:
            if not isinstance(r, dict):
                raise ValueError("bad_ranges")
            row = clean(RANGE, r)
            row["id"] = r.get("id") if ID_RE.match(str(r.get("id") or "")) else new_id(row["name"], "r-")
            out.append(row)
    except ValueError as e:
        return jsonify(error=str(e)), 400
    kv_set(RANGES_KEY, json.dumps(out, ensure_ascii=False))
    return jsonify(ok=True, ranges=out)


@bp.get("/api/ballistics/weather")
@login_required
def api_bal_weather():
    """Latest FMI observations near ?lat=&lon= (&alt= metres for station pressure)."""
    from . import weather
    try:
        lat = _num(-90, 90, True)(request.args.get("lat"), "lat")
        lon = _num(-180, 180, True)(request.args.get("lon"), "lon")
        alt = _num(-500, 9000)(request.args.get("alt"), "alt")
    except ValueError as e:
        return jsonify(error=str(e)), 400
    try:
        return jsonify(weather.current(lat, lon, alt))
    except ValueError as e:
        return jsonify(error=str(e) if str(e) == "no_stations" else "weather_unavailable"), 502
    except Exception:                       # network, timeout, bad XML
        return jsonify(error="weather_unavailable"), 502
