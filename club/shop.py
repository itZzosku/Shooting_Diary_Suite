"""Club fees and merch: season fee (kausimaksu, once per person per year), one-time fee (kertamaksu, e.g. per
SRA match) and merch (patches …, optional stock). One "payments page" per club (its own secret link / QR code, e.g.
at the match desk): name, what you pay for, total – then one MobilePay payment per category, because every product
has its own MobilePay link (account + category) and message.

  sales      kind sale (someone paid / owes), refill / count (merch stock); voided rows stay in the log
"""
import re
import time

from flask import abort, jsonify, request

from . import (WITHDRAW_MIN, _body, _clean, _club_for, _err, _grp, _int, _limited, _now, _page, _price, _ref,
               _user_names, _valid_date, bridge, cbp, cdb, current_user, new_code, CODE_RE, SLUG_RE)

KINDS = ("season", "single", "merch")
SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    club_id INTEGER NOT NULL,
    kind TEXT NOT NULL,                       -- season | single | merch
    name TEXT NOT NULL,
    price REAL NOT NULL DEFAULT 0,
    pay_url TEXT NOT NULL DEFAULT '',         -- MobilePay link of its category
    pay_msg TEXT NOT NULL DEFAULT '',         -- MobilePay message before the reference, e.g. KAUSIMAKSU
    track INTEGER NOT NULL DEFAULT 0,         -- merch: keep stock
    low INTEGER NOT NULL DEFAULT 0,           -- merch: warn below this
    archived INTEGER NOT NULL DEFAULT 0,
    ord INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS sales (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    club_id INTEGER NOT NULL,
    grp TEXT NOT NULL,
    kind TEXT NOT NULL,                       -- sale | refill | count
    product_id INTEGER NOT NULL,
    qty INTEGER NOT NULL,                     -- sale: how many; refill / count: change to the stock
    price REAL,
    name TEXT NOT NULL DEFAULT '',
    uid INTEGER,
    date TEXT NOT NULL,
    at TEXT NOT NULL,
    paid INTEGER,
    ref TEXT,
    device TEXT,
    client_id TEXT UNIQUE,
    note TEXT NOT NULL DEFAULT '',
    by_uid INTEGER,
    voided_at TEXT,
    voided_by INTEGER
);
CREATE INDEX IF NOT EXISTS sales_club ON sales(club_id, date);
CREATE INDEX IF NOT EXISTS sales_grp ON sales(grp);
"""


def init_db(conn):
    conn.executescript(SCHEMA)
    if "pay_code" not in {r[1] for r in conn.execute("PRAGMA table_info(clubs)")}:
        conn.execute("ALTER TABLE clubs ADD COLUMN pay_code TEXT")          # secret of the payments page
    pcols = {r[1] for r in conn.execute("PRAGMA table_info(products)")}
    if "deal_qty" not in pcols:                                             # quantity price: e.g. 3 for 25 €
        conn.execute("ALTER TABLE products ADD COLUMN deal_qty INTEGER NOT NULL DEFAULT 0")
        conn.execute("ALTER TABLE products ADD COLUMN deal_price REAL NOT NULL DEFAULT 0")
    if "amount" not in {r[1] for r in conn.execute("PRAGMA table_info(sales)")}:
        conn.execute("ALTER TABLE sales ADD COLUMN amount REAL")            # the line's € (with the quantity price)


def line_price(p, qty):
    """€ for qty of a product: every full deal_qty costs deal_price, the rest the single price.
    10 € each, 3 for 25 €: 1 → 10, 2 → 20, 3 → 25, 4 → 35, 6 → 50."""
    dq, dp = int(p.get("deal_qty") or 0), float(p.get("deal_price") or 0)
    if dq > 1 and dp > 0:
        return round((qty // dq) * dp + (qty % dq) * p["price"], 2)
    return round(qty * p["price"], 2)


def _amount(r):
    return r["amount"] if r["amount"] is not None else round(r["qty"] * (r["price"] or 0), 2)


def pay_code(conn, club):
    """The club's payments-page secret, made on first use."""
    code = conn.execute("SELECT pay_code FROM clubs WHERE id=?", (club["id"],)).fetchone()[0]
    if not code:
        code = new_code(conn)
        while conn.execute("SELECT 1 FROM clubs WHERE pay_code=?", (code,)).fetchone():
            code = new_code(conn)
        conn.execute("UPDATE clubs SET pay_code=? WHERE id=?", (code, club["id"]))
        conn.commit()
    return code


def product_stock(conn, club_id):
    """{product_id: count} for merch with stock kept: refills and counts minus what was sold (not voided)."""
    out = {}
    for r in conn.execute("SELECT s.product_id, s.kind, SUM(s.qty) AS n FROM sales s JOIN products p ON p.id=s.product_id "
                          "WHERE s.club_id=? AND s.voided_at IS NULL AND p.track GROUP BY s.product_id, s.kind",
                          (club_id,)):
        out[r["product_id"]] = out.get(r["product_id"], 0) + (-r["n"] if r["kind"] == "sale" else r["n"])
    return out


def merch_warnings(conn, club_id):
    st = product_stock(conn, club_id)
    return [{"kind": "merch", "name": p["name"], "n": st.get(p["id"], 0), "low": p["low"]}
            for p in conn.execute("SELECT * FROM products WHERE club_id=? AND track AND low>0 AND NOT archived", (club_id,))
            if st.get(p["id"], 0) < p["low"]]


def _products(conn, club_id, public=False):
    rows = [dict(r) for r in conn.execute("SELECT * FROM products WHERE club_id=? ORDER BY archived, "
                                          "CASE kind WHEN 'season' THEN 0 WHEN 'single' THEN 1 ELSE 2 END, ord, name",
                                          (club_id,))]
    st = product_stock(conn, club_id)
    for p in rows:
        p["stock"] = st.get(p["id"], 0) if p["track"] else None
    if public:
        rows = [{k: p[k] for k in ("id", "kind", "name", "price", "stock", "deal_qty", "deal_price")} for p in rows
                if not p["archived"] and not (p["track"] and p["stock"] <= 0)]
    return rows


# ------------------------------------------------------------------ public: the payments page
def _club_by_code(code, conn):
    if not CODE_RE.match(code or ""):
        return None
    r = conn.execute("SELECT * FROM clubs WHERE pay_code=?", (code,)).fetchone()
    return dict(r) if r else None


@cbp.get("/<slug>/pay/<code>")
def pay_page(slug, code):
    if not SLUG_RE.match(slug) or not CODE_RE.match(code):
        abort(404)
    return _page("pay.html")


@cbp.get("/api/p/<code>")
def api_pay_info(code):
    conn = cdb()
    club = _club_by_code(code, conn)
    if not club:
        return _err("not_found", 404)
    u = current_user()
    return jsonify(club={"name": club["name"], "slug": club["slug"], "mobilepay": club["mobilepay"]},
                   products=_products(conn, club["id"], public=True),
                   me={"name": bridge.display_name(u), "user": u["username"]} if u else None,
                   withdraw_min=WITHDRAW_MIN)


def pay_message(tmpl, date, qty, ref):
    """The MobilePay message for one product, from its template: {pvm} → 24.10.2026, {vuosi} → 2026, {kpl} → 2,
    {koodi} → the entry's code (added at the end when the template doesn't place it). English names work too:
    {date}, {year}, {qty}, {code}.  "SRA KERTAMAKSU {pvm}" → "SRA KERTAMAKSU 24.10.2026 A7K3"."""
    y, m, d = date.split("-")
    out = str(tmpl or "")
    for keys, val in ((("{pvm}", "{date}"), f"{d}.{m}.{y}"), (("{vuosi}", "{year}"), y), (("{kpl}", "{qty}"), str(qty))):
        for k in keys:
            out = out.replace(k, val)
    if "{koodi}" in out or "{code}" in out:
        return out.replace("{koodi}", ref).replace("{code}", ref).strip()
    return (out + " " + ref).strip()


def _sale_out(conn, grp):
    rows = conn.execute("SELECT s.*, p.name AS pname, p.kind AS pkind, p.pay_url, p.pay_msg FROM sales s "
                        "JOIN products p ON p.id=s.product_id WHERE s.grp=? ORDER BY s.id", (grp,)).fetchall()
    pays = {}                                  # one MobilePay payment per link + message
    for r in rows:
        msg = pay_message(r["pay_msg"], r["date"], r["qty"], r["ref"])
        g = pays.setdefault((r["pay_url"], msg), {"pay_url": r["pay_url"], "msg": msg, "total": 0, "names": []})
        g["total"] = round(g["total"] + _amount(r), 2)
        g["names"].append(r["pname"] + (f" × {r['qty']}" if r["qty"] > 1 else ""))
    return {"grp": grp, "ref": rows[0]["ref"], "date": rows[0]["date"], "at": rows[0]["at"], "paid": bool(rows[0]["paid"]),
            "voided": bool(rows[0]["voided_at"]), "total": round(sum(_amount(r) for r in rows), 2),
            "lines": [{"name": r["pname"], "kind": r["pkind"], "qty": r["qty"], "price": r["price"], "amount": _amount(r)}
                      for r in rows],
            "pays": list(pays.values())}


@cbp.post("/api/p/<code>/pay")
def api_pay(code):
    body = _body()
    if body.get("website"):
        return jsonify(ok=True, ref="", total=0, pays=[])
    conn = cdb()
    club = _club_by_code(code, conn)
    if not club:
        return _err("not_found", 404)
    cid = _clean(body.get("client_id"), 40) or None
    if cid:
        prev = conn.execute("SELECT grp FROM sales WHERE client_id=?", (cid,)).fetchone()
        if prev:
            return jsonify(ok=True, **_sale_out(conn, prev["grp"]))
    if _limited():
        return _err("Too many entries from here – try again later.", 429)
    name = _clean(body.get("name"), 60)
    if len(name) < 2:
        return _err("Write your name.")
    d = str(body.get("date") or "")
    if not _valid_date(d):
        return _err("Check the date.")
    prods = {p["id"]: p for p in _products(conn, club["id"], public=True)}
    lines = body.get("lines")
    if not isinstance(lines, list) or not 1 <= len(lines) <= 10:
        return _err("Pick what you pay for.")
    rows = []
    try:
        for ln in lines:
            p = prods.get(int(ln.get("product") or 0))
            if not p:
                return _err("That isn't for sale any more – reload the page.")
            q = 1 if p["kind"] == "season" else _int(ln.get("qty") or 1, 1, 100)
            if p["stock"] is not None and q > p["stock"]:
                return _err(f"Only {p['stock']} left: {p['name']}")
            rows.append((p, q))
    except (TypeError, ValueError, AttributeError):
        return _err("Pick what you pay for.")
    u = current_user()
    grp, ref, at = _grp(), _ref(), _now()
    paid = 1 if body.get("paid", True) else 0
    device = _clean(body.get("device"), 40) or None
    for i, (p, q) in enumerate(rows):
        conn.execute("INSERT INTO sales(club_id,grp,kind,product_id,qty,price,amount,name,uid,date,at,paid,ref,device,"
                     "client_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (club["id"], grp, "sale", p["id"], q, p["price"], line_price(p, q), name, u["id"] if u else None, d,
                      at, paid, ref, device, cid if i == 0 else None))
    conn.commit()
    return jsonify(ok=True, **_sale_out(conn, grp))


@cbp.post("/api/p/<code>/mine")
def api_pay_mine(code):
    body, conn = _body(), cdb()
    club = _club_by_code(code, conn)
    device = _clean(body.get("device"), 40)
    if not club or not device:
        return jsonify(entries=[])
    since = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 30 * 86400))
    grps = [r["grp"] for r in conn.execute(
        "SELECT grp, MAX(at) AS at FROM sales WHERE club_id=? AND device=? AND kind='sale' AND at>=? "
        "GROUP BY grp ORDER BY at DESC LIMIT 20", (club["id"], device, since))]
    return jsonify(entries=[_sale_out(conn, g) for g in grps])


@cbp.post("/api/p/<code>/withdraw/<grp>")
def api_pay_withdraw(code, grp):
    from datetime import datetime, timedelta, timezone
    body, conn = _body(), cdb()
    club = _club_by_code(code, conn)
    device = _clean(body.get("device"), 40)
    r = conn.execute("SELECT * FROM sales WHERE grp=? AND club_id=? AND kind='sale' LIMIT 1",
                     (grp, club["id"] if club else -1)).fetchone()
    if not club or not device or not r or r["device"] != device:
        return _err("not_found", 404)
    t = datetime.strptime(r["at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) - t > timedelta(minutes=WITHDRAW_MIN):
        return _err(f"Older than {WITHDRAW_MIN} minutes – ask a club admin to fix it.", 403)
    conn.execute("UPDATE sales SET voided_at=?, note='taken back by the payer' WHERE grp=? AND voided_at IS NULL",
                 (_now(), grp))
    conn.commit()
    return jsonify(ok=True)


# ------------------------------------------------------------------ admin
@cbp.post("/api/c/<slug>/products")
def api_product(slug):
    club, _u, _r = _club_for(slug)
    b, conn = _body(), cdb()
    kind = b.get("kind") if b.get("kind") in KINDS else None
    name = _clean(b.get("name"), 60)
    if not kind or not name:
        return _err("Name and type are needed.")
    try:
        price = _price(b.get("price"))
        low = _int(b.get("low") or 0, 0, 100000)
    except ValueError:
        return _err("Price: euros, e.g. 10 or 8.50")
    pay = str(b.get("pay_url") or "").strip()
    if pay and (len(pay) > 400 or not re.match(r"^(https://|mobilepay://)\S+$", pay)):
        return _err("MobilePay link: paste the address from the MobilePay QR code (https://… or mobilepay://…).")
    msg = _clean(b.get("pay_msg"), 50)
    track = 1 if kind == "merch" and b.get("track") else 0
    try:
        dq = _int(b.get("deal_qty") or 0, 0, 1000) if kind == "merch" else 0
        dp = _price(b.get("deal_price") or 0) if dq > 1 else 0
    except ValueError:
        return _err("Quantity price: how many (2 or more) and their € together, e.g. 3 for 25.")
    if dq > 1 and not dp:
        dq = 0
    pid = b.get("id")
    vals = (kind, name, price, pay, msg, track, low if track else 0, 1 if b.get("archived") else 0, dq, dp)
    if pid:
        n = conn.execute("UPDATE products SET kind=?, name=?, price=?, pay_url=?, pay_msg=?, track=?, low=?, archived=?, "
                         "deal_qty=?, deal_price=? WHERE id=? AND club_id=?", vals + (pid, club["id"])).rowcount
        if not n:
            return _err("not_found", 404)
    else:
        pid = conn.execute("INSERT INTO products(kind,name,price,pay_url,pay_msg,track,low,archived,deal_qty,deal_price,"
                           "club_id) VALUES(?,?,?,?,?,?,?,?,?,?,?)", vals + (club["id"],)).lastrowid
    conn.commit()
    return jsonify(ok=True, id=pid)


@cbp.post("/api/c/<slug>/products/<int:pid>/delete")
def api_product_delete(slug, pid):
    """Delete a fee / item that was never used. One with sales or stock history can only be archived."""
    club, _u, _r = _club_for(slug)
    _body()
    conn = cdb()
    if not conn.execute("SELECT 1 FROM products WHERE id=? AND club_id=?", (pid, club["id"])).fetchone():
        return _err("not_found", 404)
    if conn.execute("SELECT 1 FROM sales WHERE product_id=? LIMIT 1", (pid,)).fetchone():
        return _err("in_use")
    conn.execute("DELETE FROM products WHERE id=?", (pid,))
    conn.commit()
    return jsonify(ok=True)


@cbp.post("/api/c/<slug>/pmoves")
def api_pmove(slug):
    """Merch stock: refill (+ rounds… items) or count (what's really there)."""
    club, u, _r = _club_for(slug)
    b, conn = _body(), cdb()
    p = conn.execute("SELECT * FROM products WHERE id=? AND club_id=? AND track", (b.get("product"), club["id"])).fetchone()
    if not p:
        return _err("Pick the product.")
    kind = b.get("kind")
    try:
        q = _int(b.get("qty"), 0 if kind == "count" else 1, 100000)
    except ValueError:
        return _err("A whole number.")
    if kind == "count":
        have = product_stock(conn, club["id"]).get(p["id"], 0)
        q, note = q - have, f"counted {q}, log said {have}"
    elif kind == "refill":
        note = ""
    else:
        return _err("bad_kind")
    note = (note + " · " if note and b.get("note") else note) + _clean(b.get("note"), 200)
    conn.execute("INSERT INTO sales(club_id,grp,kind,product_id,qty,date,at,note,by_uid) VALUES(?,?,?,?,?,?,?,?,?)",
                 (club["id"], _grp(), kind, p["id"], q, time.strftime("%Y-%m-%d"), _now(), note, u["id"]))
    conn.commit()
    return jsonify(ok=True)


@cbp.post("/api/c/<slug>/paycode")
def api_new_paycode(slug):
    """A leaked payments-page link: a new one; the old QR stops working."""
    club, _u, _r = _club_for(slug)
    conn = cdb()
    conn.execute("UPDATE clubs SET pay_code=NULL WHERE id=?", (club["id"],))
    conn.commit()
    return jsonify(ok=True, code=pay_code(conn, club))


@cbp.get("/api/c/<slug>/season")
def api_season(slug):
    """Who has paid a season fee in a year (admins): for checking at the match desk."""
    club, _u, _r = _club_for(slug)
    year = request.args.get("year") or time.strftime("%Y")
    if not re.match(r"^\d{4}$", year):
        abort(400)
    conn = cdb()
    rows = [dict(r) for r in conn.execute(
        "SELECT s.grp, s.name, s.date, s.paid, s.ref, COALESCE(s.amount, s.price) AS price, p.name AS pname FROM sales s "
        "JOIN products p ON p.id=s.product_id WHERE s.club_id=? AND s.kind='sale' AND p.kind='season' "
        "AND s.voided_at IS NULL AND s.date LIKE ? ORDER BY lower(s.name), s.date", (club["id"], year + "-%"))]
    years = [r[0] for r in conn.execute(
        "SELECT DISTINCT substr(s.date,1,4) FROM sales s JOIN products p ON p.id=s.product_id "
        "WHERE s.club_id=? AND p.kind='season' ORDER BY 1 DESC", (club["id"],))]
    return jsonify(year=year, years=years, rows=rows)


def log_rows(conn, club, month, role):
    """Fees and merch for the admin log, in the same shape as the ammo moves."""
    if role != "admin":
        return []
    rows = [dict(r) for r in conn.execute(
        "SELECT s.*, p.name AS pname, p.kind AS pkind FROM sales s JOIN products p ON p.id=s.product_id "
        "WHERE s.club_id=? AND s.date LIKE ? ORDER BY s.date DESC, s.at DESC, s.id", (club["id"], month + "-%"))]
    names = _user_names({r["by_uid"] for r in rows if r["by_uid"]} | {r["voided_by"] for r in rows if r["voided_by"]})
    for r in rows:
        r["kind"] = {"sale": "sale", "refill": "prefill", "count": "pcount"}[r["kind"]]
        r["by"] = names.get(r["by_uid"])
        r["voided_by_name"] = names.get(r["voided_by"])
        for k in ("device", "client_id", "uid", "by_uid", "voided_by"):
            r.pop(k, None)
    return rows


def months(conn, club_id):
    return [r[0] for r in conn.execute("SELECT DISTINCT substr(date,1,7) FROM sales WHERE club_id=?", (club_id,))]
