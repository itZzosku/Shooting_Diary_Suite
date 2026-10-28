"""Shared reticle library: one table for the whole server (DIARY_DATA_DIR/library.db), not per account.
An admin enters a reticle once from the maker's subtension drawing and every account's profiles can use it.

Each reticle is stored twice in one row: `spec`, the builder's input in the drawing's own terms (hash spacing,
hash length, line width, tree rows), and `elements`, the plain drawing primitives the reticle view draws,
compiled in the browser by static/ballistics/reticle.js. The server checks only shape and size.

Reticles are archived, never deleted: a profile that uses one keeps drawing it.
"""
import json
import math
import os
import sqlite3
import time

from flask import jsonify, request

from . import ID_RE, bp, current_user, login_required

SCHEMA = """
CREATE TABLE IF NOT EXISTS reticles (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, maker TEXT,
    unit TEXT NOT NULL DEFAULT 'MRAD', focal_plane TEXT NOT NULL DEFAULT 'FFP',
    design_mag REAL, min_mag REAL, max_mag REAL,
    spec TEXT NOT NULL, elements TEXT NOT NULL, template TEXT,
    notes TEXT DEFAULT '', archived INTEGER NOT NULL DEFAULT 0,
    rev INTEGER NOT NULL DEFAULT 1, updated TEXT, updated_by TEXT);
"""
MAX_ELEMENTS = 2000
MAX_SPEC_BYTES = 200_000
ELEMENT_FIELDS = {                       # type → numeric fields it must have
    "line": ("x1", "y1", "x2", "y2", "w"), "rect": ("x", "y", "width", "height"), "dot": ("x", "y", "r"),
    "circle": ("x", "y", "r", "w"), "poly": ("w",), "text": ("x", "y", "size")}


def path():
    from . import DATA_DIR
    return os.path.join(DATA_DIR, "library.db")


def connect():
    conn = sqlite3.connect(path())
    conn.row_factory = sqlite3.Row
    return conn


def init_library():
    conn = connect()
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()


def _row(r):
    d = dict(r)
    d["spec"] = json.loads(d["spec"])
    d["elements"] = json.loads(d["elements"])
    return d


def all_reticles(include_ids=()):
    """Active reticles, plus archived ones in include_ids (used by this account's profiles)."""
    conn = connect()
    try:
        rows = [_row(r) for r in conn.execute("SELECT * FROM reticles ORDER BY maker, name")]
    finally:
        conn.close()
    keep = set(include_ids)
    return [r for r in rows if not r["archived"] or r["id"] in keep]


# ------------------------------------------------------------------ validation
def _finite(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and abs(v) <= 1000


def check_elements(elements):
    if not isinstance(elements, list) or not elements or len(elements) > MAX_ELEMENTS:
        raise ValueError("bad_elements")
    for e in elements:
        if not isinstance(e, dict) or e.get("t") not in ELEMENT_FIELDS:
            raise ValueError("bad_elements")
        if not all(_finite(e.get(k)) for k in ELEMENT_FIELDS[e["t"]]):
            raise ValueError("bad_elements")
        if e["t"] == "poly":
            pts = e.get("points")
            if not isinstance(pts, list) or not 2 <= len(pts) <= 64 or not all(
                    isinstance(p, list) and len(p) == 2 and _finite(p[0]) and _finite(p[1]) for p in pts):
                raise ValueError("bad_elements")
        if e["t"] == "text" and not (isinstance(e.get("text"), str) and len(e["text"]) <= 12):
            raise ValueError("bad_elements")
    return elements


def clean(b):
    name = " ".join(str(b.get("name") or "").split())[:80]
    if not name:
        raise ValueError("name_required")
    unit = b.get("unit") or "MRAD"
    fp = b.get("focal_plane") or "FFP"
    if unit not in ("MRAD", "MOA"):
        raise ValueError("bad_unit")
    if fp not in ("FFP", "SFP"):
        raise ValueError("bad_focal_plane")

    def mag(k):
        v = b.get(k)
        if v in (None, ""):
            return None
        if not _finite(v) or not 1 <= float(v) <= 100:
            raise ValueError("bad_" + k)
        return float(v)
    out = {"name": name, "maker": " ".join(str(b.get("maker") or "").split())[:40], "unit": unit, "focal_plane": fp,
           "design_mag": mag("design_mag"), "min_mag": mag("min_mag"), "max_mag": mag("max_mag"),
           "template": str(b.get("template") or "")[:40] or None, "notes": str(b.get("notes") or "")[:2000],
           "archived": 1 if b.get("archived") else 0}
    if fp == "SFP" and out["design_mag"] is None:
        raise ValueError("design_mag_required")
    spec = b.get("spec")
    if not isinstance(spec, dict):
        raise ValueError("bad_spec")
    out["spec"] = json.dumps(spec, ensure_ascii=False)
    if len(out["spec"]) > MAX_SPEC_BYTES:
        raise ValueError("bad_spec")
    out["elements"] = json.dumps(check_elements(b.get("elements")))
    return out


def save(rid, body, username):
    row = clean(body)
    conn = connect()
    try:
        old = conn.execute("SELECT rev FROM reticles WHERE id=?", (rid,)).fetchone()
        row.update(rev=(old["rev"] + 1) if old else 1, updated=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                   updated_by=username)
        cols = list(row)
        conn.execute(f"INSERT INTO reticles(id,{','.join(cols)}) VALUES(?,{','.join('?' * len(cols))}) "
                     f"ON CONFLICT(id) DO UPDATE SET {','.join(f'{c}=excluded.{c}' for c in cols)}",
                     [rid, *[row[c] for c in cols]])
        conn.commit()
        return _row(conn.execute("SELECT * FROM reticles WHERE id=?", (rid,)).fetchone())
    finally:
        conn.close()


def backup(directory, keep):
    """Dated copy of library.db next to the account backups (newest `keep`)."""
    import glob
    if not os.path.exists(path()):
        return None
    dst_path = os.path.join(directory, f"library-{time.strftime('%Y-%m-%d')}.db")
    src, dst = sqlite3.connect(path()), sqlite3.connect(dst_path)
    with dst:
        src.backup(dst)
    src.close()
    dst.close()
    for p in sorted(glob.glob(os.path.join(directory, "library-[0-9]*.db")))[:-keep]:
        os.remove(p)
    return dst_path


# ------------------------------------------------------------------ routes
def _admin():
    u = current_user()
    return u if u and u["is_admin"] and not u.get("demo") else None


@bp.get("/api/ballistics/reticles")
@login_required
def api_reticles():
    return jsonify(all_reticles())


@bp.put("/api/ballistics/reticles/<rid>")
@login_required
def api_put_reticle(rid):
    if not ID_RE.match(rid):
        return jsonify(error="bad_id"), 400
    u = _admin()
    if not u:
        return jsonify(error="admin_only"), 403
    if not request.is_json or not isinstance(request.get_json(silent=True), dict):
        return jsonify(error="bad_body"), 415
    try:
        r = save(rid, request.get_json(), u["username"])
    except ValueError as e:
        return jsonify(error=str(e)), 400
    return jsonify(ok=True, reticle=r)


@bp.delete("/api/ballistics/reticles/<rid>")
@login_required
def api_archive_reticle(rid):
    """Archives: hidden from pickers, still drawn for profiles that use it."""
    if request.headers.get("X-Requested-With") != "fetch":
        return jsonify(error="fetch_only"), 403
    if not _admin():
        return jsonify(error="admin_only"), 403
    conn = connect()
    try:
        conn.execute("UPDATE reticles SET archived=1, rev=rev+1 WHERE id=?", (rid,))
        conn.commit()
    finally:
        conn.close()
    return jsonify(ok=True)
