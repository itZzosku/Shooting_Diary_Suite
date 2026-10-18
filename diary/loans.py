"""Guns lent to friends (e.g. for a match) with your ammo: shots per gun and what the ammo cost.

A loan is one trip: date, event, and lines {who, gunId, gun, cal, rounds, price}. Its shots count toward the
gun's round count but never toward your own shooting record (the licence PDF/CSV) – you didn't shoot them.
Prices are per round in EUR, kept per account (kv 'ammo_prices'); the defaults are the public /ammo
calculator's list.
"""
import json
import time
from collections import OrderedDict

from flask import abort, jsonify, request

from . import DATE_RE, ID_RE, bp, db, kv_get, kv_set, login_required

# Per-round prices in EUR – also the defaults of the public ammo calculator (main.py /ammo)
DEFAULT_AMMO_PRICES = OrderedDict([
    (".223 Rem", 0.60),
    ("9 mm", 0.30),
    ("12 gauge", 0.40),
    ("7.62x54R", 1.00),
    ("6.5 CM", 2.00),
])
PRICES_KEY = "ammo_prices"
SCHEMA = """
CREATE TABLE IF NOT EXISTS gun_loans (
    id TEXT PRIMARY KEY, date TEXT NOT NULL, data TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS gun_loans_date ON gun_loans(date);
"""


def init_loans(conn):
    conn.executescript(SCHEMA)


def prices(conn=None):
    stored = kv_get(PRICES_KEY, conn)
    return json.loads(stored) if stored else dict(DEFAULT_AMMO_PRICES)


def loan_rows(conn):
    return [dict(json.loads(r["data"]), id=r["id"]) for r in conn.execute("SELECT id, data FROM gun_loans ORDER BY date, id")]


def upsert_loan(conn, lid, body):
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    conn.execute("INSERT INTO gun_loans(id,date,data,updated_at) VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                 "date=excluded.date, data=excluded.data, updated_at=excluded.updated_at",
                 (lid, body["date"], json.dumps(body, ensure_ascii=False), now))


def _clean(body):
    if not DATE_RE.match(str(body.get("date", ""))):
        raise ValueError("bad_date")
    lines = body.get("lines", [])
    if not isinstance(lines, list) or len(lines) > 100:
        raise ValueError("bad_lines")
    out_lines = []
    for l in lines:
        if not isinstance(l, dict):
            raise ValueError("bad_lines")
        try:
            rounds = int(l.get("rounds") or 0)
            price = float(str(l.get("price") or 0).replace(",", "."))
        except (TypeError, ValueError):
            raise ValueError("bad_lines")
        if not (0 <= rounds <= 100000 and 0 <= price <= 1000):
            raise ValueError("bad_lines")
        out_lines.append({"who": " ".join(str(l.get("who") or "").split())[:60],
                          "gunId": str(l.get("gunId") or "")[:64] or None,
                          "gun": str(l.get("gun") or "")[:120], "cal": str(l.get("cal") or "")[:40],
                          "rounds": rounds, "price": round(price, 4)})
    paid = [" ".join(str(p).split())[:60] for p in (body.get("paid") or []) if str(p).strip()][:50]
    return {"date": body["date"], "event": " ".join(str(body.get("event") or "").split())[:120],
            "note": str(body.get("note") or "")[:2000], "lines": out_lines, "paid": paid}


def export_rows(conn):
    return {"loans": loan_rows(conn), "ammo_prices": prices(conn)}


def import_rows(conn, data, replace=False):
    if replace and "loans" in data:
        conn.execute("DELETE FROM gun_loans")
    n = 0
    for l in data.get("loans", []):
        l = dict(l)
        lid = l.pop("id")
        upsert_loan(conn, lid, _clean(l))
        n += 1
    if isinstance(data.get("ammo_prices"), dict):
        kv_set(PRICES_KEY, json.dumps(data["ammo_prices"], ensure_ascii=False), conn)
    return n


def unpaid_total(conn):
    """EUR still owed to you across all loans (friends not marked paid)."""
    total = 0.0
    for l in loan_rows(conn):
        paid = set(l.get("paid") or [])
        total += sum(x["rounds"] * x["price"] for x in l.get("lines", []) if x.get("who") not in paid)
    return round(total, 2)


# ------------------------------------------------------------------ routes
@bp.get("/api/loans")
@login_required
def api_loans():
    return jsonify(loan_rows(db()))


@bp.put("/api/loans/<lid>")
@login_required
def api_put_loan(lid):
    if not ID_RE.match(lid) or not request.is_json:
        abort(415 if not request.is_json else 400)
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        abort(400)
    try:
        upsert_loan(db(), lid, _clean(body))
    except ValueError as e:
        return jsonify(error=str(e)), 400
    db().commit()
    return jsonify(ok=True)


@bp.delete("/api/loans/<lid>")
@login_required
def api_delete_loan(lid):
    if request.headers.get("X-Requested-With") != "fetch":
        abort(403)
    db().execute("DELETE FROM gun_loans WHERE id=?", (lid,))
    db().commit()
    return jsonify(ok=True)


@bp.get("/api/ammo-prices")
@login_required
def api_prices():
    return jsonify(prices(db()))


@bp.put("/api/ammo-prices")
@login_required
def api_put_prices():
    body = request.get_json(silent=True) if request.is_json else None
    if not isinstance(body, dict) or len(body) > 100:
        abort(400 if request.is_json else 415)
    out = {}
    for k, v in body.items():
        name = " ".join(str(k).split())[:40]
        try:
            price = float(str(v).replace(",", "."))
        except (TypeError, ValueError):
            return jsonify(error="bad_price"), 400
        if name and 0 <= price <= 1000:
            out[name] = round(price, 4)
    kv_set(PRICES_KEY, json.dumps(out, ensure_ascii=False))
    return jsonify(ok=True, prices=out)
