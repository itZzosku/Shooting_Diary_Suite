"""Club ammo – shooters log rounds taken from the club's ammo boxes; admins keep track of stock.

Separate from the diary: its own package, pages at /ammo and database (DATA_DIR/clubs.db); everything it needs
from the diary goes through bridge.py. Shooters need no
account – each physical box has a secret link (printed as a QR code on its lid) that opens the form for that box.
Someone logged into the diary gets their name filled in and can add the rounds to their own diary too.

  /ammo/<club>/<code>       the shooter form for one box (public; the code is the box's secret)
  /ammo/<club>/admin        stock, purchases, refills, counts, hand-overs, prices, boxes (diary login + club role)

Every table carries club_id, so more clubs are just more rows. Stock is never stored: it is the sum of the moves
(use, buy, move, count), which keeps the history as the audit trail. A voided move stays in the log, crossed out.

Roles per club: "admin" (everything). Whoever holds a box (boxes.holder_uid) may refill, count and receive
purchases for that box. A site admin (diary is_admin) can create clubs and acts as admin of every club.
"""
import json
import os
import re
import secrets
import sqlite3
import threading
import time
from datetime import date as _date, datetime, timedelta, timezone

from flask import Blueprint, abort, g, jsonify, redirect, request, send_from_directory

from . import bridge
from .bridge import current_user

HERE = os.path.dirname(os.path.abspath(__file__))
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

cbp = Blueprint("club", __name__, static_folder="static", static_url_path="/static")
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,30}$")
CODE_RE = re.compile(r"^[A-Za-z0-9_-]{8,40}$")
REF_CHARS = "ACDEFHJKMNPRTUVWXY34679"        # payment reference: no 0/O, 1/I/L, 5/S, 8/B
WITHDRAW_MIN = 30                             # a shooter can take back their own entry this long
MAX_ROUNDS = 5000
TYPES = ("Kivääri", "Pienoiskivääri", "Pistooli", "Pienoispistooli", "Revolveri", "Pienoisrevolveri", "Haulikko",
         "Yhdistelmäase", "Mustaruutiase", "Kaasuase", "Merkinantopistooli", "Muu ampuma-ase")

SCHEMA = """
CREATE TABLE IF NOT EXISTS clubs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    mobilepay TEXT NOT NULL DEFAULT '',
    created TEXT NOT NULL,
    pay_msg TEXT NOT NULL DEFAULT ''          -- MobilePay message before the reference, e.g. HARRASTEMAKSU
);
CREATE TABLE IF NOT EXISTS club_roles (
    club_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    role TEXT NOT NULL,
    PRIMARY KEY (club_id, user_id)
);
CREATE TABLE IF NOT EXISTS items (            -- an ammo the club sells: calibre + brand/load + € per round
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    club_id INTEGER NOT NULL,
    calibre TEXT NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    price REAL NOT NULL DEFAULT 0,
    archived INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS boxes (            -- a physical ammo box: one calibre (+ more in cals), held by one person
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    club_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    calibre TEXT NOT NULL,
    type TEXT NOT NULL DEFAULT '',            -- firearm type for the diary line (Pistooli, Kivääri, ...)
    code TEXT NOT NULL UNIQUE,                -- the secret in the box's QR link
    holder_name TEXT NOT NULL DEFAULT '',
    holder_uid INTEGER,
    low INTEGER NOT NULL DEFAULT 0,           -- refill this box below this many rounds (0 = no warning)
    pay_url TEXT NOT NULL DEFAULT '',         -- MobilePay link (number + category) opened after Save
    archived INTEGER NOT NULL DEFAULT 0,
    created TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS levels (           -- buy more when all boxes of a calibre together are below this
    club_id INTEGER NOT NULL,
    calibre TEXT NOT NULL,
    low INTEGER NOT NULL,
    PRIMARY KEY (club_id, calibre)
);
CREATE TABLE IF NOT EXISTS moves (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    club_id INTEGER NOT NULL,
    grp TEXT NOT NULL,                        -- one submission / one refill: its rows share this
    kind TEXT NOT NULL,                       -- use | buy | move | count | handover
    box_id INTEGER NOT NULL,
    item_id INTEGER,
    qty INTEGER NOT NULL DEFAULT 0,           -- change to the box's stock (use is negative)
    price REAL,                               -- € per round at the time (use, buy)
    name TEXT NOT NULL DEFAULT '',            -- the shooter (use) / new holder (handover)
    uid INTEGER,                              -- diary account of the shooter, if logged in
    date TEXT NOT NULL,
    at TEXT NOT NULL,
    paid INTEGER,
    ref TEXT,                                 -- payment reference shown with the MobilePay amount
    device TEXT,                              -- random id of the shooter's phone: lets them take an entry back
    client_id TEXT UNIQUE,                    -- the phone's id for the submission, so a resend isn't a double
    note TEXT NOT NULL DEFAULT '',
    by_uid INTEGER,                           -- who entered it (admin / holder moves)
    voided_at TEXT,
    voided_by INTEGER
);
CREATE INDEX IF NOT EXISTS moves_club ON moves(club_id, date);
CREATE INDEX IF NOT EXISTS moves_grp ON moves(grp);
"""


# ------------------------------------------------------------------ database
def init_app(app):
    """Register at /ammo. Uses the app's session cookie (the diary login) through bridge.py."""
    init_db()
    app.register_blueprint(cbp, url_prefix="/ammo")


def db_path():
    return os.path.join(bridge.data_dir(), "clubs.db")


def connect():
    conn = sqlite3.connect(db_path(), timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    os.makedirs(bridge.data_dir(), exist_ok=True)
    conn = connect()
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    if "pay_msg" not in {r[1] for r in conn.execute("PRAGMA table_info(clubs)")}:
        conn.execute("ALTER TABLE clubs ADD COLUMN pay_msg TEXT NOT NULL DEFAULT ''")    # text before the reference
    if "pay_url" not in {r[1] for r in conn.execute("PRAGMA table_info(boxes)")}:
        conn.execute("ALTER TABLE boxes ADD COLUMN pay_url TEXT NOT NULL DEFAULT ''")   # MobilePay link of the box
    if "cals" not in {r[1] for r in conn.execute("PRAGMA table_info(boxes)")}:
        conn.execute("ALTER TABLE boxes ADD COLUMN cals TEXT NOT NULL DEFAULT '[]'")    # more calibres in the same box
    if "lows" not in {r[1] for r in conn.execute("PRAGMA table_info(boxes)")}:
        conn.execute("ALTER TABLE boxes ADD COLUMN lows TEXT NOT NULL DEFAULT '{}'")    # refill level per calibre
    from . import shop, match
    shop.init_db(conn)                        # fees and merch
    match.init_db(conn)                       # matches: competitors and officers' counts
    conn.commit()
    conn.close()


def cdb():
    if "club_db" not in g:
        g.club_db = connect()
    return g.club_db


@cbp.teardown_app_request
def _close(_exc):
    conn = g.pop("club_db", None)
    if conn is not None:
        conn.close()


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _grp():
    return secrets.token_hex(6)


def _ref():
    return "".join(secrets.choice(REF_CHARS) for _ in range(4))


def _clean(s, n):
    return " ".join(str(s or "").split())[:n]


def _int(v, lo, hi):
    try:
        v = int(v)
    except (TypeError, ValueError):
        raise ValueError("bad_number")
    if not lo <= v <= hi:
        raise ValueError("bad_number")
    return v


def _price(v):
    try:
        v = round(float(v), 3)
    except (TypeError, ValueError):
        raise ValueError("bad_price")
    if not 0 <= v <= 100:
        raise ValueError("bad_price")
    return v


# ------------------------------------------------------------------ clubs, boxes (also used by the command line)
def slug_from(name, conn):
    """Club id for links from the name: "VaaRes SRA" → vaares-sra (-2, -3 … if taken)."""
    import unicodedata
    base = unicodedata.normalize("NFKD", str(name or "").lower()).encode("ascii", "ignore").decode()
    base = re.sub(r"[^a-z0-9]+", "-", base).strip("-")[:28].strip("-") or "club"
    if len(base) < 2:
        base = "club-" + base
    slug, n = base, 2
    while conn.execute("SELECT 1 FROM clubs WHERE slug=?", (slug,)).fetchone():
        slug, n = f"{base}-{n}", n + 1
    return slug


def create_club(slug, name, conn=None):
    """Returns (id, slug). An empty slug is made from the name."""
    c = conn or connect()
    slug = str(slug or "").strip().lower() or slug_from(name, c)
    if conn is None:
        c.close()
    if not SLUG_RE.match(slug):
        raise ValueError("Club id: 2–31 lowercase letters, digits or -")
    name = _clean(name, 80)
    if not name:
        raise ValueError("Name the club")
    c = conn or connect()
    try:
        cur = c.execute("INSERT INTO clubs(slug,name,created) VALUES(?,?,?)", (slug, name, _now()))
        c.commit()
        return cur.lastrowid, slug
    except sqlite3.IntegrityError:
        raise ValueError("That club id is taken")
    finally:
        if conn is None:
            c.close()


def delete_club(slug, conn=None):
    """The club and everything in it (boxes, prices, the whole log). Returns counts, or None if no such club."""
    c = conn or connect()
    try:
        r = c.execute("SELECT id FROM clubs WHERE slug=?", (slug,)).fetchone()
        if not r:
            return None
        out = {}
        for t in ("officer_counts", "competitors"):
            c.execute(f"DELETE FROM {t} WHERE match_id IN (SELECT id FROM matches WHERE club_id=?)", (r["id"],))
        for t in ("moves", "boxes", "items", "levels", "club_roles", "sales", "products", "matches"):
            out[t] = c.execute(f"DELETE FROM {t} WHERE club_id=?", (r["id"],)).rowcount
        c.execute("DELETE FROM clubs WHERE id=?", (r["id"],))
        c.commit()
        return out
    finally:
        if conn is None:
            c.close()


def club_counts(slug, conn):
    r = conn.execute("SELECT id FROM clubs WHERE slug=?", (slug,)).fetchone()
    if not r:
        return None
    return {t: conn.execute(f"SELECT COUNT(*) FROM {t} WHERE club_id=?", (r["id"],)).fetchone()[0]
            for t in ("moves", "boxes", "items")}


def set_role(club_id, user_id, role, conn=None):
    c = conn or connect()
    if role:
        c.execute("INSERT INTO club_roles(club_id,user_id,role) VALUES(?,?,?) "
                  "ON CONFLICT(club_id,user_id) DO UPDATE SET role=excluded.role", (club_id, user_id, role))
    else:
        c.execute("DELETE FROM club_roles WHERE club_id=? AND user_id=?", (club_id, user_id))
    c.commit()
    if conn is None:
        c.close()


def club_by_slug(slug, conn=None):
    r = (conn or cdb()).execute("SELECT * FROM clubs WHERE slug=?", (slug,)).fetchone()
    return dict(r) if r else None


def all_clubs():
    with connect() as c:
        return [dict(r) for r in c.execute("SELECT * FROM clubs ORDER BY name")]


def role_of(club, user, conn=None):
    """'admin', 'officer' (range / stat officer: enters rounds per competitor), 'holder' (holds a box) or None."""
    if not user or not club:
        return None
    if user.get("is_admin"):
        return "admin"
    conn = conn or cdb()
    r = conn.execute("SELECT role FROM club_roles WHERE club_id=? AND user_id=?", (club["id"], user["id"])).fetchone()
    if r:
        return r["role"]
    if conn.execute("SELECT 1 FROM boxes WHERE club_id=? AND holder_uid=? AND NOT archived",
                    (club["id"], user["id"])).fetchone():
        return "holder"
    return None


def my_clubs(user):
    """Clubs the user can manage (for the diary home page tile)."""
    if not user or not os.path.exists(db_path()):
        return []
    with connect() as c:
        out = []
        for r in c.execute("SELECT * FROM clubs ORDER BY name"):
            role = role_of(dict(r), user, c)
            if role:
                out.append({"slug": r["slug"], "name": r["name"], "role": role, "low": len(warnings(c, r["id"]))})
        return out


def new_code(conn):
    while True:
        code = secrets.token_urlsafe(9)
        if not conn.execute("SELECT 1 FROM boxes WHERE code=?", (code,)).fetchone():
            return code


# ------------------------------------------------------------------ stock
def stock(conn, club_id):
    """{(box_id, item_id): rounds} from all moves that aren't voided."""
    return {(r["box_id"], r["item_id"]): r["n"] for r in conn.execute(
        "SELECT box_id, item_id, SUM(qty) AS n FROM moves WHERE club_id=? AND voided_at IS NULL "
        "AND item_id IS NOT NULL GROUP BY box_id, item_id", (club_id,))}


def box_stock(conn, club_id):
    out = {}
    for (b, _i), n in stock(conn, club_id).items():
        out[b] = out.get(b, 0) + n
    return out


def box_cals(b):
    """All calibres a box holds: its main one first, then the others (e.g. 12/70 and 7.62x54R in one box)."""
    try:
        more = json.loads(b["cals"] or "[]")
    except (ValueError, TypeError, IndexError, KeyError):
        more = []
    return [b["calibre"]] + [c for c in more if c and c != b["calibre"]]


def box_lows(b):
    """{calibre: refill level} of a box; a box from before per-calibre levels keeps its one level on its main calibre."""
    try:
        lows = {k: int(v) for k, v in json.loads(b["lows"] or "{}").items() if int(v) > 0}
    except (ValueError, TypeError, IndexError, KeyError, AttributeError):
        lows = {}
    if not lows and b["low"]:
        lows = {b["calibre"]: b["low"]}
    cals = box_cals(b)
    return {c: lows[c] for c in cals if c in lows}


def box_cal_stock(conn, club_id):
    """{box_id: {calibre: rounds}}."""
    cal = {r["id"]: r["calibre"] for r in conn.execute("SELECT id, calibre FROM items WHERE club_id=?", (club_id,))}
    out = {}
    for (b, i), n in stock(conn, club_id).items():
        if i in cal:
            d = out.setdefault(b, {})
            d[cal[i]] = d.get(cal[i], 0) + n
    return out


def cal_stock(conn, club_id):
    """{calibre: rounds in all boxes} (by the ammo's calibre, so a box with two calibres counts right)."""
    cal = {r["id"]: r["calibre"] for r in conn.execute("SELECT id, calibre FROM items WHERE club_id=?", (club_id,))}
    arch = {r["id"] for r in conn.execute("SELECT id FROM boxes WHERE club_id=? AND archived", (club_id,))}
    out = {}
    for (b, i), n in stock(conn, club_id).items():
        if b not in arch and i in cal:
            out[cal[i]] = out.get(cal[i], 0) + n
    return out


def _cal_list(v, main):
    if isinstance(v, str):
        v = v.split(",")
    if not isinstance(v, list):
        return []
    out = []
    for c in v:
        c = _clean(c, 40)
        if c and c != main and c not in out:
            out.append(c)
    return out[:5]


def warnings(conn, club_id):
    """Boxes below their refill level and calibres below their buy-more level."""
    bcs = box_cal_stock(conn, club_id)
    out, per_cal = [], cal_stock(conn, club_id)
    for b in conn.execute("SELECT * FROM boxes WHERE club_id=? AND NOT archived", (club_id,)):
        multi = len(box_cals(b)) > 1
        for c, low in box_lows(b).items():
            n = bcs.get(b["id"], {}).get(c, 0)
            if n < low:
                out.append({"kind": "box", "box": b["id"], "name": b["name"], "calibre": c if multi else "",
                            "n": n, "low": low})
    for lv in conn.execute("SELECT * FROM levels WHERE club_id=?", (club_id,)):
        n = per_cal.get(lv["calibre"], 0)
        if lv["low"] and n < lv["low"]:
            out.append({"kind": "calibre", "calibre": lv["calibre"], "n": n, "low": lv["low"]})
    from . import shop
    return out + shop.merch_warnings(conn, club_id)


# ------------------------------------------------------------------ pages
def _page(name):
    resp = send_from_directory(os.path.join(HERE, "static"), name, max_age=0)
    resp.headers["Cache-Control"] = "no-store"
    return resp


@cbp.get("/")
def index():
    u = current_user()
    if not u:
        return redirect(bridge.login_url())
    clubs = my_clubs(u)
    if len(clubs) == 1:
        return redirect(f"/ammo/{clubs[0]['slug']}/{'ro' if clubs[0]['role'] == 'officer' else 'admin'}")
    return _page("clubs.html")


@cbp.get("/sw.js")
def service_worker():
    resp = send_from_directory(os.path.join(HERE, "static"), "sw.js", mimetype="application/javascript", max_age=0)
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@cbp.get("/<slug>/admin")
def admin_page(slug):
    u = current_user()
    if not u:
        return redirect(bridge.login_url())
    club = club_by_slug(slug)
    role = role_of(club, u) if club else None
    if not role:
        abort(404)
    if role == "officer":
        return redirect(f"/ammo/{slug}/ro")
    return _page("admin.html")


@cbp.get("/<slug>/qr")
def qr_page(slug):
    """Printable QR stickers for the boxes (admins)."""
    u = current_user()
    if not u:
        return redirect(bridge.login_url())
    club = club_by_slug(slug)
    if not club or role_of(club, u) != "admin":
        abort(404)
    return _page("qr.html")


@cbp.get("/<slug>/<code>")
def box_page(slug, code):
    if not SLUG_RE.match(slug) or not CODE_RE.match(code):
        abort(404)
    # The page works offline (service worker); whether the box exists is checked by its API call
    return _page("box.html")


@cbp.after_request
def _headers(resp):
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    if "/api/" in request.path:
        resp.headers["Cache-Control"] = "no-store"
    return resp


def _body():
    if not request.is_json:
        abort(415)
    b = request.get_json(silent=True)
    if not isinstance(b, dict):
        abort(400)
    return b


def _err(msg, code=400):
    return jsonify(error=msg), code


# ------------------------------------------------------------------ shooter API (no login; the box code is the key)
_posts, _posts_lock = {}, threading.Lock()
MAX_POSTS, POST_WINDOW = 300, 60 * 60         # entries per hour from one address (mobile data: many phones share one)


def _limited():
    ip = request.remote_addr or "?"
    with _posts_lock:
        n, t0 = _posts.get(ip, (0, time.time()))
        if time.time() - t0 > POST_WINDOW:
            n, t0 = 0, time.time()
        _posts[ip] = (n + 1, t0)
        return n >= MAX_POSTS


def _box(code, conn):
    if not CODE_RE.match(code or ""):
        return None, None
    b = conn.execute("SELECT * FROM boxes WHERE code=? AND NOT archived", (code,)).fetchone()
    if not b:
        return None, None
    c = conn.execute("SELECT * FROM clubs WHERE id=?", (b["club_id"],)).fetchone()
    return dict(b), dict(c)


def _items_for(conn, club_id, calibres):
    """Ammo of these calibres, in the box's calibre order, dearest first within a calibre."""
    if isinstance(calibres, str):
        calibres = [calibres]
    rows = [dict(r) for r in conn.execute(
        f"SELECT id, name, price, calibre FROM items WHERE club_id=? AND NOT archived AND calibre IN "
        f"({','.join('?' * len(calibres))}) ORDER BY price DESC, name", [club_id] + list(calibres))]
    return sorted(rows, key=lambda r: calibres.index(r["calibre"]))


def _box_items(conn, club, box):
    """What the shooter can pick: the ammo that's in this box (by the log); every ammo of the calibre if none is."""
    cals = box_cals(box)
    items = _items_for(conn, club["id"], cals)
    st = stock(conn, club["id"])
    have = [i for i in items if st.get((box["id"], i["id"]), 0) > 0]
    # Per calibre: what's in the box, or every ammo of that calibre if none of it is
    return [i for i in items if i in have or not any(h["calibre"] == i["calibre"] for h in have)]


@cbp.get("/api/b/<code>")
def api_box(code):
    conn = cdb()
    box, club = _box(code, conn)
    if not box:
        return _err("not_found", 404)
    u = current_user()
    from . import match
    m = match.active_match_for_box(conn, club["id"], box["id"], _local_today())
    mout = {"id": m["id"], "name": m["name"], "competitors": [{"id": c["id"], "name": c["name"], "squad": c["squad"]}
                                                             for c in match.competitors(conn, m["id"])]} if m else None
    return jsonify(club={"name": club["name"], "slug": club["slug"], "mobilepay": club["mobilepay"],
                         "pay_msg": club["pay_msg"]}, match=mout,
                   box={"name": box["name"], "calibre": " + ".join(box_cals(box)), "cals": box_cals(box),
                        "pay_url": box["pay_url"]},
                   items=_box_items(conn, club, box),
                   me={"name": bridge.display_name(u), "user": u["username"]} if u else None,
                   withdraw_min=WITHDRAW_MIN)


def _local_today():
    return time.strftime("%Y-%m-%d", time.localtime())


def _valid_date(s):
    if not DATE_RE.match(s or ""):
        return False
    try:
        d = _date.fromisoformat(s)
    except ValueError:
        return False
    today = datetime.now(timezone.utc).date()
    return today - timedelta(days=14) <= d <= today + timedelta(days=1)


@cbp.post("/api/b/<code>/use")
def api_use(code):
    body = _body()
    if body.get("website"):                    # hidden field: only bots fill it in
        return jsonify(ok=True, ref="", total=0)
    conn = cdb()
    box, club = _box(code, conn)
    if not box:
        return _err("not_found", 404)
    cid = _clean(body.get("client_id"), 40) or None
    if cid:
        prev = conn.execute("SELECT * FROM moves WHERE client_id=?", (cid,)).fetchone()
        if prev:                               # the phone sent it again (offline queue): same answer
            return jsonify(ok=True, **_entry_out(conn, prev["grp"]))
    if _limited():
        return _err("Too many entries from here – try again later.", 429)
    comp = None
    if body.get("comp"):                       # picked from the match's competitor list
        try:
            comp = conn.execute("SELECT c.id, c.name FROM competitors c JOIN matches m ON m.id=c.match_id "
                                "WHERE c.id=? AND m.club_id=?", (int(body["comp"]), club["id"])).fetchone()
        except (TypeError, ValueError):
            comp = None
    name = comp["name"] if comp else _clean(body.get("name"), 60)
    if len(name) < 2:
        return _err("Write your name.")
    d = str(body.get("date") or "")
    if not _valid_date(d):
        return _err("Check the date.")
    items = {i["id"]: i for i in _items_for(conn, club["id"], box_cals(box))}
    lines = body.get("lines")
    if not isinstance(lines, list) or not 1 <= len(lines) <= 5:
        return _err("Add the rounds.")
    rows = []
    try:
        for ln in lines:
            if not isinstance(ln, dict):
                raise ValueError
            it = items.get(int(ln.get("item") or 0))
            if not it:
                return _err("That ammo isn't in this box any more – reload the page.")
            rows.append((it, _int(ln.get("qty"), 1, MAX_ROUNDS)))
    except (TypeError, ValueError):
        return _err("Rounds: a whole number from 1 to %d." % MAX_ROUNDS)
    u = current_user()
    grp, ref, at = _grp(), _ref(), _now()
    device = _clean(body.get("device"), 40) or None
    paid = 1 if body.get("paid", True) else 0
    for i, (it, q) in enumerate(rows):
        conn.execute("INSERT INTO moves(club_id,grp,kind,box_id,item_id,qty,price,name,uid,date,at,paid,ref,device,"
                     "client_id,comp_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (club["id"], grp, "use", box["id"], it["id"], -q, it["price"], comp["name"] if comp else name,
                      u["id"] if u else None, d, at, paid, ref, device, cid if i == 0 else None, comp["id"] if comp else None))
    conn.commit()
    out = _entry_out(conn, grp)
    if u and body.get("diary"):
        try:
            out["diary"] = add_to_diary(u, club, box, grp, d, rows)
        except (sqlite3.Error, ValueError, KeyError):
            out["diary"] = False
    return jsonify(ok=True, **out)


def _entry_out(conn, grp):
    rows = conn.execute("SELECT m.*, i.name AS iname FROM moves m LEFT JOIN items i ON i.id=m.item_id "
                        "WHERE grp=? ORDER BY m.id", (grp,)).fetchall()
    total = round(sum(-r["qty"] * (r["price"] or 0) for r in rows), 2)
    return {"grp": grp, "ref": rows[0]["ref"], "total": total, "date": rows[0]["date"], "at": rows[0]["at"],
            "paid": bool(rows[0]["paid"]), "voided": bool(rows[0]["voided_at"]),
            "lines": [{"item": r["iname"] or "", "qty": -r["qty"], "price": r["price"]} for r in rows]}


@cbp.post("/api/b/<code>/mine")
def api_mine(code):
    """This phone's entries in this box from the last 30 days (newest first)."""
    body = _body()
    conn = cdb()
    box, _club = _box(code, conn)
    device = _clean(body.get("device"), 40)
    if not box or not device:
        return jsonify(entries=[])
    since = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 30 * 86400))
    grps = [r["grp"] for r in conn.execute(
        "SELECT grp, MAX(at) AS at FROM moves WHERE box_id=? AND device=? AND kind='use' AND at>=? "
        "GROUP BY grp ORDER BY at DESC LIMIT 20", (box["id"], device, since))]
    return jsonify(entries=[_entry_out(conn, gp) for gp in grps])


@cbp.post("/api/b/<code>/withdraw/<grp>")
def api_withdraw(code, grp):
    """The shooter takes back their own entry from the same phone, within WITHDRAW_MIN minutes."""
    body = _body()
    conn = cdb()
    box, club = _box(code, conn)
    device = _clean(body.get("device"), 40)
    if not box or not device:
        return _err("not_found", 404)
    r = conn.execute("SELECT * FROM moves WHERE grp=? AND box_id=? AND kind='use' LIMIT 1", (grp, box["id"])).fetchone()
    if not r or r["device"] != device:
        return _err("not_found", 404)
    t = datetime.strptime(r["at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) - t > timedelta(minutes=WITHDRAW_MIN):
        return _err(f"Older than {WITHDRAW_MIN} minutes – ask a club admin to fix it.", 403)
    conn.execute("UPDATE moves SET voided_at=?, note='taken back by the shooter' WHERE grp=? AND voided_at IS NULL",
                 (_now(), grp))
    conn.commit()
    if r["uid"]:
        bridge.remove_rounds(r["uid"], grp)
    return jsonify(ok=True)


def add_to_diary(user, club, box, grp, d, rows):
    """One diary line per calibre shot (a box can hold several); the box's firearm type only for a one-calibre box."""
    per = {}
    for it, q in rows:
        per[it["calibre"]] = per.get(it["calibre"], 0) + q
    typ = box["type"] if len(box_cals(box)) == 1 else ""
    ok = True
    for cal, q in per.items():
        ok = bridge.add_rounds(user, d, q, cal, typ, f"{club['name']} ammo · {box['name']}", club["name"], grp) and ok
    return ok


# ------------------------------------------------------------------ admin API (diary login + a role in the club)
def _club_for(slug, need="admin"):
    u = current_user()
    if not u:
        abort(401)
    club = club_by_slug(slug)
    role = role_of(club, u) if club else None
    ok = {"admin": ("admin",), "any": ("admin", "holder"), "officer": ("admin", "officer")}[need]
    if role not in ok:
        abort(404 if not role else 403)
    return club, u, role


def _may_box(conn, club, u, role, box_id):
    if role == "admin":
        return True
    b = conn.execute("SELECT holder_uid FROM boxes WHERE id=? AND club_id=?", (box_id, club["id"])).fetchone()
    return bool(b and b["holder_uid"] == u["id"])


@cbp.get("/api/clubs")
def api_clubs():
    u = current_user()
    if not u:
        abort(401)
    return jsonify(clubs=my_clubs(u), site_admin=bool(u["is_admin"]))


@cbp.post("/api/clubs")
def api_new_club():
    u = current_user()
    if not u or not u["is_admin"]:
        abort(403)
    b = _body()
    try:
        cid, slug = create_club(b.get("slug"), b.get("name"), cdb())
    except ValueError as e:
        return _err(str(e))
    set_role(cid, u["id"], "admin", cdb())
    return jsonify(ok=True, slug=slug)


def _user_names(ids):
    out = {}
    for i in ids:
        u = bridge.get_user(i)
        if u:
            out[i] = u["username"]
    return out


@cbp.get("/api/c/<slug>")
def api_state(slug):
    club, u, role = _club_for(slug, need="any")
    conn = cdb()
    st = stock(conn, club["id"])
    items = [dict(r) for r in conn.execute("SELECT * FROM items WHERE club_id=? ORDER BY calibre, archived, name",
                                           (club["id"],))]
    boxes = []
    for r in conn.execute("SELECT * FROM boxes WHERE club_id=? ORDER BY calibre, name", (club["id"],)):
        b = dict(r)
        b["stock"] = {str(i): n for (bx, i), n in st.items() if bx == b["id"] and n}
        b["total"] = sum(n for (bx, _i), n in st.items() if bx == b["id"])
        b["mine"] = b["holder_uid"] == u["id"]
        b["lows"] = box_lows(b)
        b["cals"] = box_cals(b)
        if role != "admin" and not b["mine"]:
            b["code"] = None                   # only admins and the holder see a box's secret link
        boxes.append(b)
    levels = {r["calibre"]: r["low"] for r in conn.execute("SELECT * FROM levels WHERE club_id=?", (club["id"],))}
    cal_totals = cal_stock(conn, club["id"])
    people = [{"user_id": r["user_id"], "role": r["role"]}
              for r in conn.execute("SELECT * FROM club_roles WHERE club_id=? ORDER BY role", (club["id"],))]
    names = _user_names({p["user_id"] for p in people} | {b["holder_uid"] for b in boxes if b["holder_uid"]})
    for p in people:
        p["username"] = names.get(p["user_id"], "?")
    for b in boxes:
        b["holder_user"] = names.get(b["holder_uid"]) if b["holder_uid"] else None
    from . import shop
    admin = role == "admin"
    return jsonify(club={k: club[k] for k in ("slug", "name", "mobilepay", "pay_msg")}, role=role, me=u["username"],
                   items=items, boxes=boxes, levels=levels, cal_totals=cal_totals, people=people if admin else [],
                   warnings=warnings(conn, club["id"]), types=TYPES,
                   products=shop._products(conn, club["id"]) if admin else [],
                   pay_code=shop.pay_code(conn, club) if admin else None)


@cbp.put("/api/c/<slug>")
def api_club_put(slug):
    club, _u, _r = _club_for(slug)
    b = _body()
    name = _clean(b.get("name"), 80) or club["name"]
    mp = _clean(b.get("mobilepay"), 40)
    msg = _clean(b.get("pay_msg"), 40)          # + space + 4-character reference: fits MobilePay's 60
    cdb().execute("UPDATE clubs SET name=?, mobilepay=?, pay_msg=? WHERE id=?", (name, mp, msg, club["id"]))
    cdb().commit()
    return jsonify(ok=True)


@cbp.post("/api/c/<slug>/items")
def api_item(slug):
    club, _u, _r = _club_for(slug)
    b, conn = _body(), cdb()
    cal, name = _clean(b.get("calibre"), 40), _clean(b.get("name"), 60)
    if not cal:
        return _err("Calibre is needed.")
    try:
        price = _price(b.get("price"))
    except ValueError:
        return _err("Price: € per round, e.g. 0.35")
    iid = b.get("id")
    if iid:
        r = conn.execute("SELECT * FROM items WHERE id=? AND club_id=?", (iid, club["id"])).fetchone()
        if not r:
            return _err("not_found", 404)
        if r["calibre"] != cal and conn.execute("SELECT 1 FROM moves WHERE item_id=? LIMIT 1", (iid,)).fetchone():
            return _err("This ammo is already in use – archive it and add a new one for another calibre.")
        conn.execute("UPDATE items SET calibre=?, name=?, price=?, archived=? WHERE id=?",
                     (cal, name, price, 1 if b.get("archived") else 0, iid))
    else:
        if conn.execute("SELECT 1 FROM items WHERE club_id=? AND calibre=? AND name=? AND NOT archived",
                        (club["id"], cal, name)).fetchone():
            return _err("That ammo is already on the list.")
        iid = conn.execute("INSERT INTO items(club_id,calibre,name,price) VALUES(?,?,?,?)",
                           (club["id"], cal, name, price)).lastrowid
    conn.commit()
    return jsonify(ok=True, id=iid)


def _holder(b):
    """holder: free text name; holder_user: optional diary username (gets holder rights for the box)."""
    hn = _clean(b.get("holder_name"), 60)
    hu = str(b.get("holder_user") or "").strip().lower()
    uid = None
    if hu:
        u = bridge.user_by_name(hu)
        if not u:
            raise ValueError(f"No diary account called {hu}")
        uid = u["id"]
        hn = hn or hu
    return hn, uid


@cbp.post("/api/c/<slug>/boxes")
def api_boxes(slug):
    club, u, _r = _club_for(slug)
    b, conn = _body(), cdb()
    name, cal = _clean(b.get("name"), 40), _clean(b.get("calibre"), 40)
    cals = json.dumps(_cal_list(b.get("cals"), cal), ensure_ascii=False)
    typ = b.get("type") if b.get("type") in TYPES else ""
    if not name or not cal:
        return _err("Name and calibre are needed.")
    pay = str(b.get("pay_url") or "").strip()
    if pay and (len(pay) > 400 or not re.match(r"^(https://|mobilepay://)\S+$", pay)):
        return _err("MobilePay link: paste the address from the MobilePay QR code (https://… or mobilepay://…).")
    try:
        allc = [cal] + json.loads(cals)
        raw = b.get("lows") if isinstance(b.get("lows"), dict) else {cal: b.get("low") or 0}
        lows_d = {c: _int(raw.get(c) or 0, 0, 100000) for c in allc}
        lows = json.dumps({c: n for c, n in lows_d.items() if n}, ensure_ascii=False)
        low = 0                                # the old single level; levels are per calibre now (lows)
        hn, huid = _holder(b)
    except ValueError as e:
        return _err(str(e) if "account" in str(e) else "Refill level: a whole number.")
    bid = b.get("id")
    if bid:
        r = conn.execute("SELECT * FROM boxes WHERE id=? AND club_id=?", (bid, club["id"])).fetchone()
        if not r:
            return _err("not_found", 404)
        # A calibre that still has rounds in the box can't be taken off it
        keep = set([cal] + json.loads(cals))
        icals = {i["id"]: i["calibre"] for i in conn.execute("SELECT id, calibre FROM items WHERE club_id=?", (club["id"],))}
        if any(n and icals.get(i) not in keep for (bx, i), n in stock(conn, club["id"]).items() if bx == r["id"]):
            return _err("Empty the box (move or count it to 0) before changing its calibre.")
        if (r["holder_name"], r["holder_uid"]) != (hn, huid):
            _handover_row(conn, club, r["id"], hn, u, r["holder_name"])
        conn.execute("UPDATE boxes SET name=?, calibre=?, type=?, low=?, holder_name=?, holder_uid=?, archived=?, "
                     "pay_url=?, cals=?, lows=? WHERE id=?", (name, cal, typ, low, hn, huid, 1 if b.get("archived") else 0,
                                                              pay, cals, lows, bid))
    else:
        bid = conn.execute("INSERT INTO boxes(club_id,name,calibre,type,code,holder_name,holder_uid,low,created,pay_url,"
                           "cals,lows) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                           (club["id"], name, cal, typ, new_code(conn), hn, huid, low, _now(), pay, cals, lows)).lastrowid
        if hn:
            _handover_row(conn, club, bid, hn, u, "")
    conn.commit()
    return jsonify(ok=True, id=bid)


def _handover_row(conn, club, box_id, new, by, old):
    today = time.strftime("%Y-%m-%d")
    conn.execute("INSERT INTO moves(club_id,grp,kind,box_id,qty,name,date,at,note,by_uid) VALUES(?,?,?,?,?,?,?,?,?,?)",
                 (club["id"], _grp(), "handover", box_id, 0, new, today, _now(),
                  f"from {old}" if old else "", by["id"]))


@cbp.post("/api/c/<slug>/items/<int:iid>/delete")
def api_item_delete(slug, iid):
    """Delete an ammo price that was never used; one with history can only be archived."""
    club, _u, _r = _club_for(slug)
    _body()
    conn = cdb()
    if not conn.execute("SELECT 1 FROM items WHERE id=? AND club_id=?", (iid, club["id"])).fetchone():
        return _err("not_found", 404)
    if conn.execute("SELECT 1 FROM moves WHERE item_id=? LIMIT 1", (iid,)).fetchone():
        return _err("in_use")
    conn.execute("DELETE FROM items WHERE id=?", (iid,))
    conn.commit()
    return jsonify(ok=True)


@cbp.post("/api/c/<slug>/boxes/<int:bid>/delete")
def api_box_delete(slug, bid):
    """Delete a box that never had rounds in it (hand-overs don't count); otherwise archive it."""
    club, _u, _r = _club_for(slug)
    _body()
    conn = cdb()
    if not conn.execute("SELECT 1 FROM boxes WHERE id=? AND club_id=?", (bid, club["id"])).fetchone():
        return _err("not_found", 404)
    if conn.execute("SELECT 1 FROM moves WHERE box_id=? AND kind!='handover' LIMIT 1", (bid,)).fetchone():
        return _err("in_use")
    conn.execute("DELETE FROM moves WHERE box_id=?", (bid,))
    conn.execute("DELETE FROM boxes WHERE id=?", (bid,))
    conn.commit()
    return jsonify(ok=True)


@cbp.post("/api/c/<slug>/boxes/<int:bid>/newcode")
def api_newcode(slug, bid):
    """A leaked link: the box gets a new secret (print a new sticker); the old link stops working."""
    club, _u, _r = _club_for(slug)
    conn = cdb()
    n = conn.execute("UPDATE boxes SET code=? WHERE id=? AND club_id=?", (new_code(conn), bid, club["id"])).rowcount
    conn.commit()
    return jsonify(ok=bool(n))


@cbp.post("/api/c/<slug>/levels")
def api_levels(slug):
    club, _u, _r = _club_for(slug)
    b, conn = _body(), cdb()
    cal = _clean(b.get("calibre"), 40)
    try:
        low = _int(b.get("low") or 0, 0, 1000000)
    except ValueError:
        return _err("A whole number.")
    if low:
        conn.execute("INSERT INTO levels(club_id,calibre,low) VALUES(?,?,?) ON CONFLICT(club_id,calibre) "
                     "DO UPDATE SET low=excluded.low", (club["id"], cal, low))
    else:
        conn.execute("DELETE FROM levels WHERE club_id=? AND calibre=?", (club["id"], cal))
    conn.commit()
    return jsonify(ok=True)


@cbp.post("/api/c/<slug>/moves")
def api_move(slug):
    """Admin / box holder: buy (rounds into a box), move (box → box), count (what's really in a box)."""
    club, u, role = _club_for(slug, need="any")
    b, conn = _body(), cdb()
    kind = b.get("kind")
    boxes = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM boxes WHERE club_id=?", (club["id"],))}
    items = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM items WHERE club_id=?", (club["id"],))}
    try:
        box = boxes[int(b.get("box"))]
        item = items[int(b.get("item"))]
    except (KeyError, TypeError, ValueError):
        return _err("Pick the box and the ammo.")
    if item["calibre"] not in box_cals(box):
        return _err(f"{box['name']} is a {' + '.join(box_cals(box))} box.")
    d = str(b.get("date") or time.strftime("%Y-%m-%d"))
    if not DATE_RE.match(d):
        return _err("Check the date.")
    note = _clean(b.get("note"), 200)
    grp, at = _grp(), _now()

    def row(bx, q, price=None, nt=note):
        conn.execute("INSERT INTO moves(club_id,grp,kind,box_id,item_id,qty,price,date,at,note,by_uid) "
                     "VALUES(?,?,?,?,?,?,?,?,?,?,?)", (club["id"], grp, kind, bx, item["id"], q, price, d, at, nt, u["id"]))
    try:
        if kind == "buy":
            if not _may_box(conn, club, u, role, box["id"]):
                abort(403)
            q = _int(b.get("qty"), 1, 1000000)
            price = _price(b["price"]) if b.get("price") not in (None, "") else None
            row(box["id"], q, price)
        elif kind == "move":
            try:
                to = boxes[int(b.get("to"))]
            except (KeyError, TypeError, ValueError):
                return _err("Pick the box to move to.")
            if to["id"] == box["id"] or item["calibre"] not in box_cals(to):
                return _err("Move between two boxes of the same calibre.")
            if not (_may_box(conn, club, u, role, box["id"]) or _may_box(conn, club, u, role, to["id"])):
                abort(403)
            q = _int(b.get("qty"), 1, 1000000)
            have = stock(conn, club["id"]).get((box["id"], item["id"]), 0)
            if q > have:
                return _err(f"{box['name']} has only {have} of that ammo.")
            row(box["id"], -q)
            row(to["id"], q)
        elif kind == "count":
            if not _may_box(conn, club, u, role, box["id"]):
                abort(403)
            counted = _int(b.get("qty"), 0, 1000000)
            have = stock(conn, club["id"]).get((box["id"], item["id"]), 0)
            row(box["id"], counted - have, None, (f"counted {counted}, log said {have}" + (" · " + note if note else "")))
        else:
            return _err("bad_kind")
    except ValueError:
        return _err("Rounds: a whole number.")
    conn.commit()
    return jsonify(ok=True, grp=grp)


@cbp.get("/api/c/<slug>/log")
def api_log(slug):
    """Moves for one month (YYYY-MM, default this month), optionally one box; newest first."""
    club, u, role = _club_for(slug, need="any")
    conn = cdb()
    month = request.args.get("month") or time.strftime("%Y-%m")
    if not re.match(r"^\d{4}-\d{2}$", month):
        abort(400)
    q = ("SELECT m.*, i.name AS iname, i.calibre AS ical, b.name AS bname FROM moves m "
         "LEFT JOIN items i ON i.id=m.item_id LEFT JOIN boxes b ON b.id=m.box_id "
         "WHERE m.club_id=? AND m.date LIKE ?")
    args = [club["id"], month + "-%"]
    if request.args.get("box"):
        q += " AND m.box_id=?"
        args.append(int(request.args["box"]))
    rows = [dict(r) for r in conn.execute(q + " ORDER BY m.date DESC, m.at DESC, m.id", args)]
    names = _user_names({r["by_uid"] for r in rows if r["by_uid"]} | {r["voided_by"] for r in rows if r["voided_by"]})
    out = []
    for r in rows:
        if role != "admin" and r["kind"] == "use":
            r["name"] = ""                     # box holders see stock movements, not who shot what
        r["by"] = names.get(r["by_uid"])
        r["voided_by_name"] = names.get(r["voided_by"])
        for k in ("device", "client_id", "uid", "by_uid", "voided_by"):
            r.pop(k, None)
        out.append(r)
    from . import shop
    if not request.args.get("box"):
        out += shop.log_rows(conn, club, month, role)      # fees and merch (admins)
        out.sort(key=lambda r: (r["date"], r["at"]), reverse=True)
    months = sorted({m["m"] for m in conn.execute("SELECT DISTINCT substr(date,1,7) AS m FROM moves WHERE club_id=?",
                                                  (club["id"],))} | set(shop.months(conn, club["id"])), reverse=True)
    return jsonify(month=month, months=months, moves=out)


@cbp.post("/api/c/<slug>/void/<grp>")
def api_void(slug, grp):
    club, u, _r = _club_for(slug)
    b, conn = _body(), cdb()
    undo = bool(b.get("undo"))
    tables = [t for t in ("moves", "sales")
              if conn.execute(f"SELECT 1 FROM {t} WHERE grp=? AND club_id=? LIMIT 1", (grp, club["id"])).fetchone()]
    if not tables:
        return _err("not_found", 404)
    for t in tables:
        if undo:
            conn.execute(f"UPDATE {t} SET voided_at=NULL, voided_by=NULL WHERE grp=?", (grp,))
        else:
            conn.execute(f"UPDATE {t} SET voided_at=?, voided_by=? WHERE grp=? AND voided_at IS NULL", (_now(), u["id"], grp))
    conn.commit()
    return jsonify(ok=True)


@cbp.post("/api/c/<slug>/paid/<grp>")
def api_paid(slug, grp):
    club, _u, _r = _club_for(slug)
    b, conn = _body(), cdb()
    n = conn.execute("UPDATE moves SET paid=? WHERE grp=? AND club_id=? AND kind='use'",
                     (1 if b.get("paid") else 0, grp, club["id"])).rowcount
    n += conn.execute("UPDATE sales SET paid=? WHERE grp=? AND club_id=? AND kind='sale'",
                      (1 if b.get("paid") else 0, grp, club["id"])).rowcount
    conn.commit()
    return jsonify(ok=bool(n))


@cbp.post("/api/c/<slug>/people")
def api_people(slug):
    """Give a diary account a role in the club – "admin" or "officer" (range / stat officer) – or remove it ("")."""
    club, me, _r = _club_for(slug)
    b = _body()
    u = bridge.user_by_name(str(b.get("username") or "").strip().lower())
    if not u:
        return _err("No diary account with that name.")
    role = b.get("role") if b.get("role") in ("admin", "officer") else None
    if u["id"] == me["id"] and role != "admin":
        return _err("Not yourself – ask another admin.")
    set_role(club["id"], u["id"], role, cdb())
    return jsonify(ok=True)


# Fees and merch (season fee, one-time fee, patches …): routes on the same blueprint
from . import shop  # noqa: E402,F401
# Treasurer's export (CSV / Excel / data for the PDF)
from . import export  # noqa: E402,F401
# Matches: competitors from SSI, range / stat officers' counts per stage, reconciliation
from . import match  # noqa: E402,F401
