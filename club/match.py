"""Matches: a second, independent count of club ammo. Range / stat officers (diary accounts with the club role
"officer") enter how many rounds of club ammo each competitor shot on each stage; shooters log the same on the box
form (picking their name from the competitor list while a match is on); the report puts the two side by side.

  matches          a match day: name, date, number of stages, the boxes it uses (none = all)
  competitors      from the SSI participants list (pasted into the admin page): number, name, squad, division
  officer_counts   one row per competitor × stage × ammo, last value wins (who entered it and when is kept)
  moves.comp_id    the competitor a shooter's own entry belongs to
"""
import json
import time

from flask import abort, jsonify

from . import (DATE_RE, _body, _clean, _club_for, _err, _int, _now, _page, _user_names, bridge, box_cals, cbp, cdb,
               current_user, club_by_slug, role_of, SLUG_RE)
from . import ssi

SCHEMA = """
CREATE TABLE IF NOT EXISTS matches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    club_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    date TEXT NOT NULL,
    stages INTEGER NOT NULL DEFAULT 1,
    boxes TEXT NOT NULL DEFAULT '[]',
    created TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS competitors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id INTEGER NOT NULL,
    num TEXT NOT NULL DEFAULT '',
    name TEXT NOT NULL,
    squad TEXT NOT NULL DEFAULT '',
    division TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS competitors_match ON competitors(match_id);
CREATE TABLE IF NOT EXISTS officer_counts (
    match_id INTEGER NOT NULL,
    competitor_id INTEGER NOT NULL,
    stage INTEGER NOT NULL,
    item_id INTEGER NOT NULL,
    qty INTEGER NOT NULL,
    by_uid INTEGER,
    at TEXT NOT NULL,
    PRIMARY KEY (match_id, competitor_id, stage, item_id)
);
"""
MAX_STAGES, MAX_COMPETITORS = 30, 400


def init_db(conn):
    conn.executescript(SCHEMA)
    if "comp_id" not in {r[1] for r in conn.execute("PRAGMA table_info(moves)")}:
        conn.execute("ALTER TABLE moves ADD COLUMN comp_id INTEGER")        # shooter's entry → competitor
    if "ssi_url" not in {r[1] for r in conn.execute("PRAGMA table_info(matches)")}:
        conn.execute("ALTER TABLE matches ADD COLUMN ssi_url TEXT NOT NULL DEFAULT ''")   # where it was read from
    if "category" not in {r[1] for r in conn.execute("PRAGMA table_info(competitors)")}:
        conn.execute("ALTER TABLE competitors ADD COLUMN category TEXT NOT NULL DEFAULT ''")  # SSI category (loan gun)
    if "stage_ammo" not in {r[1] for r in conn.execute("PRAGMA table_info(matches)")}:
        conn.execute("ALTER TABLE matches ADD COLUMN stage_ammo TEXT NOT NULL DEFAULT '{}'")  # {"3": [item ids]}


def _match(conn, club, mid):
    r = conn.execute("SELECT * FROM matches WHERE id=? AND club_id=?", (mid, club["id"])).fetchone()
    if not r:
        abort(404)
    m = dict(r)
    m["boxes"] = json.loads(m["boxes"] or "[]")
    m["stage_ammo"] = json.loads(m.get("stage_ammo") or "{}")
    return m


def _box_ids(conn, club, m):
    return m["boxes"] or [r["id"] for r in conn.execute("SELECT id FROM boxes WHERE club_id=? AND NOT archived",
                                                       (club["id"],))]


def match_items(conn, club, m):
    """The ammo officers can enter: every ammo of the calibres in the match's boxes."""
    cals = []
    for b in conn.execute(f"SELECT * FROM boxes WHERE id IN ({','.join('?' * len(_box_ids(conn, club, m))) or 'NULL'})",
                          _box_ids(conn, club, m)):
        cals += [c for c in box_cals(b) if c not in cals]
    if not cals:
        return []
    rows = [dict(r) for r in conn.execute(
        f"SELECT id, calibre, name, price FROM items WHERE club_id=? AND NOT archived AND calibre IN "
        f"({','.join('?' * len(cals))})", [club["id"]] + cals)]
    return sorted(rows, key=lambda r: (cals.index(r["calibre"]), -r["price"], r["name"]))


def active_match_for_box(conn, club_id, box_id, today):
    """The match running today that uses this box (for the shooter form), or None."""
    for r in conn.execute("SELECT * FROM matches WHERE club_id=? AND date=? ORDER BY id DESC", (club_id, today)):
        boxes = json.loads(r["boxes"] or "[]")
        if not boxes or box_id in boxes:
            return dict(r)
    return None


def competitors(conn, mid):
    return [dict(r) for r in conn.execute("SELECT * FROM competitors WHERE match_id=? ORDER BY "
                                          "CASE WHEN squad='' THEN 1 ELSE 0 END, squad, name", (mid,))]


# ------------------------------------------------------------------ pages
@cbp.get("/<slug>/ro")
def ro_page(slug):
    """The range / stat officer page (diary login; club role officer or admin)."""
    from flask import redirect
    u = current_user()
    if not u:
        return redirect(bridge.login_url())
    club = club_by_slug(slug) if SLUG_RE.match(slug) else None
    if not club or role_of(club, u) not in ("admin", "officer"):
        abort(404)
    return _page("ro.html")


# ------------------------------------------------------------------ admin: matches
def _clean_match(b):
    name = _clean(b.get("name"), 80)
    d = str(b.get("date") or "")
    if not name or not DATE_RE.match(d):
        raise ValueError("Name and date are needed.")
    stages = _int(b.get("stages") or 1, 1, MAX_STAGES)
    boxes = [int(x) for x in (b.get("boxes") or []) if str(x).isdigit()][:20]
    sa = b.get("stage_ammo") if isinstance(b.get("stage_ammo"), dict) else {}
    stage_ammo = {}                            # default ammo per stage: the officer page opens the stage with these lines
    for k, v in sa.items():
        if str(k).isdigit() and 1 <= int(k) <= stages and isinstance(v, list):
            ids = list(dict.fromkeys(int(x) for x in v if str(x).isdigit()))[:6]
            if ids:
                stage_ammo[str(int(k))] = ids
    return name, d, stages, json.dumps(boxes), json.dumps(stage_ammo)


@cbp.get("/api/c/<slug>/matches")
def api_matches(slug):
    club, _u, _r = _club_for(slug)
    conn = cdb()
    out = []
    for r in conn.execute("SELECT * FROM matches WHERE club_id=? ORDER BY date DESC, id DESC", (club["id"],)):
        m = dict(r)
        m["boxes"] = json.loads(m["boxes"] or "[]")
        m["stage_ammo"] = json.loads(m.get("stage_ammo") or "{}")
        m["n_comp"] = conn.execute("SELECT COUNT(*) FROM competitors WHERE match_id=?", (m["id"],)).fetchone()[0]
        m["n_counts"] = conn.execute("SELECT COUNT(*) FROM officer_counts WHERE match_id=? AND qty>0",
                                     (m["id"],)).fetchone()[0]
        out.append(m)
    officers = [r["user_id"] for r in conn.execute("SELECT user_id FROM club_roles WHERE club_id=? AND role='officer'",
                                                   (club["id"],))]
    names = _user_names(set(officers))
    return jsonify(matches=out, officers=[names.get(o, "?") for o in officers], ssi=ssi.configured())


@cbp.post("/api/c/<slug>/matches")
def api_match_save(slug):
    club, _u, _r = _club_for(slug)
    b, conn = _body(), cdb()
    try:
        name, d, stages, boxes, stage_ammo = _clean_match(b)
        ids = ssi.parse_url(b.get("ssi_url"))
        ssi_url = "https://shootnscoreit.com/event/%s/%s/" % ids if ids else ""
    except ValueError as e:
        return _err(str(e) if "needed" in str(e) else "Stages: 1–%d." % MAX_STAGES)
    if b.get("id"):
        n = conn.execute("UPDATE matches SET name=?, date=?, stages=?, boxes=?, ssi_url=?, stage_ammo=? "
                         "WHERE id=? AND club_id=?", (name, d, stages, boxes, ssi_url, stage_ammo, b["id"], club["id"])).rowcount
        if not n:
            return _err("not_found", 404)
        mid = b["id"]
    else:
        mid = conn.execute("INSERT INTO matches(club_id,name,date,stages,boxes,created,ssi_url,stage_ammo) "
                           "VALUES(?,?,?,?,?,?,?,?)", (club["id"], name, d, stages, boxes, _now(), ssi_url, stage_ammo)).lastrowid
    conn.commit()
    return jsonify(ok=True, id=mid)


@cbp.post("/api/c/<slug>/ssi")
def api_ssi(slug):
    """Read a match from SSI's API by its link: name, date, stages and the competitors with squads."""
    _club_for(slug)
    try:
        return jsonify(ok=True, **ssi.fetch(str(_body().get("url") or "")))
    except ssi.SsiError as e:
        return _err(str(e))


@cbp.post("/api/c/<slug>/matches/<int:mid>/delete")
def api_match_delete(slug, mid):
    club, _u, _r = _club_for(slug)
    _body()
    conn = cdb()
    _match(conn, club, mid)
    conn.execute("UPDATE moves SET comp_id=NULL WHERE comp_id IN (SELECT id FROM competitors WHERE match_id=?)", (mid,))
    for t in ("officer_counts", "competitors"):
        conn.execute(f"DELETE FROM {t} WHERE match_id=?", (mid,))
    conn.execute("DELETE FROM matches WHERE id=?", (mid,))
    conn.commit()
    return jsonify(ok=True)


@cbp.post("/api/c/<slug>/matches/<int:mid>/competitors")
def api_competitors(slug, mid):
    """Replace or extend the competitor list: rows [{num, name, squad, division}] (parsed from the SSI paste)."""
    club, _u, _r = _club_for(slug)
    b, conn = _body(), cdb()
    _match(conn, club, mid)
    rows = b.get("rows")
    if not isinstance(rows, list) or len(rows) > MAX_COMPETITORS:
        return _err("bad_rows")
    clean = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        name = _clean(r.get("name"), 80)
        if len(name) >= 2:
            clean.append((_clean(r.get("num"), 12), name, _clean(r.get("squad"), 30), _clean(r.get("division"), 40),
                          _clean(r.get("category"), 60)))
    if b.get("replace"):
        if conn.execute("SELECT 1 FROM officer_counts WHERE match_id=? LIMIT 1", (mid,)).fetchone():
            return _err("Officers have already entered rounds – add competitors instead of replacing the list.")
        conn.execute("UPDATE moves SET comp_id=NULL WHERE comp_id IN (SELECT id FROM competitors WHERE match_id=?)", (mid,))
        conn.execute("DELETE FROM competitors WHERE match_id=?", (mid,))
    have = {r["name"].lower(): r["id"] for r in conn.execute("SELECT id, name FROM competitors WHERE match_id=?", (mid,))}
    added = updated = 0
    for num, name, squad, div, cat in clean:
        if name.lower() in have:              # already on the list: take the new number / squad / division (re-fetch)
            if have[name.lower()]:
                updated += conn.execute("UPDATE competitors SET num=?, squad=?, division=?, category=? WHERE id=? AND "
                                        "(num!=? OR squad!=? OR division!=? OR category!=?)",
                                        (num, squad, div, cat, have[name.lower()], num, squad, div, cat)).rowcount
                have[name.lower()] = None
            continue
        have[name.lower()] = None
        conn.execute("INSERT INTO competitors(match_id,num,name,squad,division,category) VALUES(?,?,?,?,?,?)",
                     (mid, num, name, squad, div, cat))
        added += 1
    conn.commit()
    return jsonify(ok=True, added=added, updated=updated)


@cbp.post("/api/c/<slug>/matches/<int:mid>/competitors/<int:cid>/delete")
def api_competitor_delete(slug, mid, cid):
    club, _u, _r = _club_for(slug)
    _body()
    conn = cdb()
    _match(conn, club, mid)
    if conn.execute("SELECT 1 FROM officer_counts WHERE competitor_id=? AND qty>0 LIMIT 1", (cid,)).fetchone():
        return _err("Officers have entered rounds for this competitor.")
    conn.execute("UPDATE moves SET comp_id=NULL WHERE comp_id=?", (cid,))
    conn.execute("DELETE FROM competitors WHERE id=? AND match_id=?", (cid, mid))
    conn.commit()
    return jsonify(ok=True)


def report(conn, club, m):
    """Per competitor: rounds per ammo by the officers and by the shooter's own entries, paid or not."""
    items = {i["id"]: i for i in match_items(conn, club, m)}
    comps = competitors(conn, m["id"])
    by_name = {c["name"].lower(): c["id"] for c in comps}
    off, own, paid, unpaid, refs = {}, {}, {}, {}, {}
    for r in conn.execute("SELECT competitor_id, item_id, SUM(qty) AS n FROM officer_counts WHERE match_id=? "
                          "GROUP BY competitor_id, item_id", (m["id"],)):
        if r["n"]:
            off.setdefault(r["competitor_id"], {})[r["item_id"]] = r["n"]
    # The shooter's own entries that day: linked to the competitor, or (typed name) the same name
    for r in conn.execute("SELECT comp_id, name, item_id, qty, price, paid, ref, box_id FROM moves WHERE club_id=? AND "
                          "kind='use' AND voided_at IS NULL AND date=?", (club["id"], m["date"])):
        cid = r["comp_id"] if r["comp_id"] in {c["id"] for c in comps} else by_name.get((r["name"] or "").lower())
        if not cid:
            continue
        own.setdefault(cid, {})
        own[cid][r["item_id"]] = own[cid].get(r["item_id"], 0) - r["qty"]
        amt = round(-r["qty"] * (r["price"] or 0), 2)
        (paid if r["paid"] else unpaid)[cid] = round((paid if r["paid"] else unpaid).get(cid, 0) + amt, 2)
        refs.setdefault(cid, set()).add(r["ref"])
    out = []
    for c in comps:
        o, s = off.get(c["id"], {}), own.get(c["id"], {})
        if not o and not s:
            continue
        lines = [{"item": i, "officer": o.get(i, 0), "own": s.get(i, 0)} for i in sorted(set(o) | set(s))]
        to, ts = sum(o.values()), sum(s.values())
        status = ("ok" if to == ts and not unpaid.get(c["id"]) else "unpaid" if to == ts else
                  "missing" if not ts else "less" if ts < to else "more")
        owes = round(sum(max(0, ln["officer"] - ln["own"]) * (items.get(ln["item"], {}).get("price") or 0)
                         for ln in lines), 2)
        out.append({"id": c["id"], "name": c["name"], "num": c["num"], "squad": c["squad"], "officer": to, "own": ts,
                    "lines": lines, "paid": paid.get(c["id"], 0), "unpaid": unpaid.get(c["id"], 0), "owes": owes,
                    "refs": sorted(x for x in refs.get(c["id"], set()) if x), "status": status})
    return out


@cbp.get("/api/c/<slug>/matches/<int:mid>")
def api_match(slug, mid):
    club, u, role = _club_for(slug, need="officer")
    conn = cdb()
    m = _match(conn, club, mid)
    counts = [dict(r) for r in conn.execute("SELECT competitor_id, stage, item_id, qty, by_uid, at FROM officer_counts "
                                            "WHERE match_id=?", (mid,))]
    names = _user_names({c["by_uid"] for c in counts if c["by_uid"]})
    for c in counts:
        c["by"] = names.get(c.pop("by_uid"))
    out = dict(match=m, competitors=competitors(conn, mid), items=match_items(conn, club, m), counts=counts,
               role=role, me=u["username"], club={"name": club["name"]})
    if role == "admin":
        out["report"] = report(conn, club, m)
    return jsonify(out)


# ------------------------------------------------------------------ officers
@cbp.get("/api/c/<slug>/ro")
def api_ro(slug):
    """The matches an officer can enter: today's first, then the rest (newest first)."""
    club, u, role = _club_for(slug, need="officer")
    today = time.strftime("%Y-%m-%d", time.localtime())
    rows = [dict(r) for r in cdb().execute("SELECT id, name, date, stages FROM matches WHERE club_id=? "
                                           "ORDER BY date=? DESC, date DESC LIMIT 20", (club["id"], today))]
    return jsonify(matches=rows, today=today, club={"name": club["name"]}, role=role)


@cbp.post("/api/c/<slug>/matches/<int:mid>/count")
def api_count(slug, mid):
    """One officer entry: competitor × stage × ammo = rounds (0 removes it). Sent again from the offline queue: fine."""
    club, u, _role = _club_for(slug, need="officer")
    b, conn = _body(), cdb()
    m = _match(conn, club, mid)
    try:
        cid, stage, item, qty = int(b.get("comp")), _int(b.get("stage"), 1, m["stages"]), int(b.get("item")), \
            _int(b.get("qty") or 0, 0, 2000)
    except (TypeError, ValueError):
        return _err("Rounds: a whole number.")
    if not conn.execute("SELECT 1 FROM competitors WHERE id=? AND match_id=?", (cid, mid)).fetchone():
        return _err("not_found", 404)
    if item not in {i["id"] for i in match_items(conn, club, m)}:
        return _err("That ammo isn't used in this match.")
    if qty:
        conn.execute("INSERT INTO officer_counts(match_id,competitor_id,stage,item_id,qty,by_uid,at) VALUES(?,?,?,?,?,?,?) "
                     "ON CONFLICT(match_id,competitor_id,stage,item_id) DO UPDATE SET qty=excluded.qty, "
                     "by_uid=excluded.by_uid, at=excluded.at", (mid, cid, stage, item, qty, u["id"], _now()))
    else:
        conn.execute("DELETE FROM officer_counts WHERE match_id=? AND competitor_id=? AND stage=? AND item_id=?",
                     (mid, cid, stage, item))
    conn.commit()
    return jsonify(ok=True)

