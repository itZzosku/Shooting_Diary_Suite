"""Accounts: every account has its own database file, so one user can never see or change another's data.
Synthetic data only."""
import os
import sqlite3

import pytest
from flask import Flask
from werkzeug.security import generate_password_hash

import diary
from diary import users

H = {"X-Requested-With": "fetch"}
NOTE = "osku-only secret note"


def _app():
    app = Flask(__name__)
    app.config["SECRET_KEY"] = "test"
    diary.init_app(app)
    return app


def _login(app, name, pw):
    c = app.test_client()
    r = c.post("/diary/login", data={"username": name, "password": pw})
    assert r.status_code == 302, r.get_data(as_text=True)[:300]
    return c


@pytest.fixture()
def two():
    """osku (diary.db, admin) with data; mikko (users/mikko.db) fresh."""
    users.ensure("osku", "osku-password-1", admin=True, db_file="diary.db")
    m = users.by_name("mikko")
    if m:
        p = users.db_path(m)
        with users.uconnect() as c:
            c.execute("DELETE FROM users WHERE username='mikko'")
        if os.path.exists(p):
            os.remove(p)
    users.create("mikko", "mikko-password-1")
    app = _app()
    o = _login(app, "osku", "osku-password-1")
    o.put("/diary/api/guns/g-osku", json={"name": "Osku Rifle", "cal": ".308 Win"})
    o.put("/diary/api/sessions/s-osku", json={"date": "2026-10-01", "place": NOTE, "lines": [{"rounds": 20, "gunId": "g-osku"}]})
    o.put("/diary/api/profile", json={"name": "Osku", "sra": "123456"})
    o.put("/diary/api/chrono/loads/l-osku", json={"kind": "handload", "cal": ".308 Win", "bullet_model": "Scenar", "bullet_gr": 155})
    o.put("/diary/api/zero/scopes/s-osku", json={"name": "Osku Scope"})
    o.put("/diary/api/zero/zeros/z-osku", json={"scope_id": "s-osku", "rifle": "Osku Rifle", "elev": 1.0, "wind": 0})
    return app, o, _login(app, "mikko", "mikko-password-1")


def test_every_api_route_needs_login():
    app = _app()
    anon = app.test_client()
    for rule in app.url_map.iter_rules():
        if "/diary/api/" not in rule.rule:
            continue
        url = rule.rule.replace("<int:log_id>", "1")
        for arg in rule.arguments:
            url = url.replace(f"<{arg}>", "x")
        for method in rule.methods - {"HEAD", "OPTIONS"}:
            r = anon.open(url, method=method, json={}, headers=H)
            assert r.status_code == 401, (method, rule.rule, r.status_code)


def test_users_see_only_their_own_data(two):
    app, o, m = two
    assert m.get("/diary/api/account").get_json() == {"username": "mikko", "is_admin": False}
    assert m.get("/diary/api/sessions").get_json() == []
    assert m.get("/diary/api/guns").get_json() == []
    assert m.get("/diary/api/profile").get_json() == {}
    assert m.get("/diary/api/chrono/loads").get_json() == []
    assert m.get("/diary/api/chrono/strings").get_json() == []
    assert m.get("/diary/api/zero").get_json() == []
    home = m.get("/diary/api/home").get_json()
    assert home["diary"]["visits"] == 0 and home["zeroes"] == [] and home["velocity"]["strings"] == 0
    for r in (m.get("/diary/api/sessions"), m.get("/diary/api/profile"), m.get("/diary/api/home")):
        assert NOTE not in r.get_data(as_text=True) and "123456" not in r.get_data(as_text=True)
    # osku still sees everything
    assert NOTE in [s.get("place") for s in o.get("/diary/api/sessions").get_json()]
    assert "Osku Scope" in [z["name"] for z in o.get("/diary/api/home").get_json()["zeroes"]]


def test_users_cannot_change_each_others_data(two):
    app, o, m = two
    # Ids that exist only in osku's file are simply not there for mikko
    assert m.get("/diary/api/chrono/loads/l-osku").status_code == 404
    assert m.post("/diary/api/zero/zeros/z-osku/adjust", json={"d_elev": 5, "d_wind": 0}).status_code == 404
    assert m.post("/diary/api/zero/scopes/s-osku/mount", json={"zero_id": "z-osku"}).status_code == 404
    m.delete("/diary/api/guns/g-osku", headers=H)
    m.delete("/diary/api/sessions/s-osku", headers=H)
    m.delete("/diary/api/zero/scopes/s-osku", headers=H)
    # Same ids written by mikko land in mikko's file
    m.put("/diary/api/guns/g-osku", json={"name": "Mikko Rifle"})
    m.put("/diary/api/profile", json={"name": "Mikko"})
    og = {g["id"]: g["name"] for g in o.get("/diary/api/guns").get_json()}
    assert og["g-osku"] == "Osku Rifle" and "Mikko Rifle" not in og.values()
    assert o.get("/diary/api/profile").get_json()["name"] == "Osku"
    assert any(s["id"] == "s-osku" for s in o.get("/diary/api/sessions").get_json())
    sc = next(s for s in o.get("/diary/api/zero").get_json() if s["id"] == "s-osku")
    assert sc["zeros"][0]["elev"] == 1.0
    assert [g["name"] for g in m.get("/diary/api/guns").get_json()] == ["Mikko Rifle"]
    # And the files really are separate
    path = users.db_path(users.by_name("mikko"))
    names = [r[0] for r in sqlite3.connect(path).execute("SELECT data FROM guns")]
    assert len(names) == 1 and "Mikko Rifle" in names[0]


def test_login_rules_and_password_change(two):
    app, o, m = two
    c = app.test_client()
    assert c.post("/diary/login", data={"username": "mikko", "password": "osku-password-1"}).status_code == 200
    assert c.post("/diary/login", data={"username": "nobody", "password": "x" * 12}).status_code == 200
    # Change password: wrong current / too short refused; then other devices are logged out, this one isn't
    m2 = _login(app, "mikko", "mikko-password-1")
    assert m.put("/diary/api/account/password", json={"current": "wrong-password", "new": "x" * 12}).status_code == 403
    assert m.put("/diary/api/account/password", json={"current": "mikko-password-1", "new": "short"}).status_code == 400
    assert m.put("/diary/api/account/password", json={"current": "mikko-password-1", "new": "mikko-new-pass-2"}).status_code == 200
    assert m.get("/diary/api/guns").status_code == 200
    assert m2.get("/diary/api/guns").status_code == 401
    assert o.get("/diary/api/guns").status_code == 200            # other accounts untouched
    _login(app, "mikko", "mikko-new-pass-2")
    # Disabling an account cuts its existing logins too
    users.set_disabled("mikko", True)
    assert m.get("/diary/api/guns").status_code == 401
    assert app.test_client().post("/diary/login", data={"username": "mikko", "password": "mikko-new-pass-2"}).status_code == 200
    users.set_disabled("mikko", False)


def test_single_user_diary_becomes_an_account(tmp_path, monkeypatch):
    """A diary from before accounts (diary.db + one password) turns into the admin account 'osku' with the
    same password and data on first start."""
    monkeypatch.setattr(diary, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(diary, "DB_PATH", str(tmp_path / "diary.db"))
    diary.init_db()
    conn = diary.connect()
    diary.kv_set("password_hash", generate_password_hash("old-single-pass"), conn)
    diary.upsert_gun(conn, "g-old", {"name": "Old Rifle"})
    conn.commit()
    conn.close()
    app = _app()
    u = users.by_name("osku")
    assert u and u["is_admin"] and u["db_file"] == "diary.db"
    c = _login(app, "osku", "old-single-pass")
    assert [g["name"] for g in c.get("/diary/api/guns").get_json()] == ["Old Rifle"]
    # Next account gets its own file
    assert users.create("buddy", "buddy-password")["db_file"] == os.path.join("users", "buddy.db")


def test_new_account_chooses_password_on_first_login():
    """user-add without a password: empty password once → must choose one → logged in with it."""
    for n in ("newbie", "late"):
        if users.by_name(n):
            with users.uconnect() as c:
                c.execute("DELETE FROM users WHERE username=?", (n,))
    users.create("newbie")
    app = _app()
    c = app.test_client()
    # Not logged in yet: the empty password only opens the "choose your password" step
    r = c.post("/diary/login", data={"username": "newbie", "password": ""})
    assert r.status_code == 200 and b"Choose your password" in r.data
    assert c.get("/diary/api/guns").status_code == 401
    r = c.post("/diary/first-password", data={"password": "short", "password2": "short"})
    assert b"at least 10" in r.data and c.get("/diary/api/guns").status_code == 401
    r = c.post("/diary/first-password", data={"password": "newbie-pass-1", "password2": "other-pass-11"})
    assert b"match" in r.data
    r = c.post("/diary/first-password", data={"password": "newbie-pass-1", "password2": "newbie-pass-1"})
    assert r.status_code == 302 and c.get("/diary/api/account").get_json()["username"] == "newbie"
    # From now on the empty password is just wrong, the chosen one works
    assert b"Choose your password" not in app.test_client().post(
        "/diary/login", data={"username": "newbie", "password": ""}).data
    _login(app, "newbie", "newbie-pass-1")
    # Without the setup step, /first-password does nothing
    assert app.test_client().post("/diary/first-password", data={"password": "x" * 12, "password2": "x" * 12}).status_code == 302
    assert users.verify("newbie", "x" * 12) is None
    # The window closes: an unclaimed account can't be taken with an empty password after SETUP_DAYS
    users.create("late", setup_days=-1)
    r = app.test_client().post("/diary/login", data={"username": "late", "password": ""})
    assert b"Choose your password" not in r.data
    # --clear: back to choosing a new one, and existing logins end
    n = _login(app, "newbie", "newbie-pass-1")
    users.clear_password("newbie")
    assert n.get("/diary/api/guns").status_code == 401
    assert b"Choose your password" in app.test_client().post("/diary/login", data={"username": "newbie", "password": ""}).data


def test_admin_manages_accounts_in_the_app(two):
    app, o, m = two
    for n in ("buddy2",):
        if users.by_name(n):
            with users.uconnect() as c:
                c.execute("DELETE FROM users WHERE username=?", (n,))
    # Only admins
    assert m.get("/diary/api/admin/users").status_code == 403
    assert m.post("/diary/api/admin/users", json={"username": "sneaky"}).status_code == 403
    assert users.by_name("sneaky") is None
    assert m.get("/diary/api/home").get_json()["is_admin"] is False
    assert o.get("/diary/api/home").get_json()["is_admin"] is True
    # Add: no password, first login chooses it
    r = o.post("/diary/api/admin/users", json={"username": "Buddy2"})
    assert r.status_code == 200 and r.get_json()["user"]["setup_open"] is True
    assert o.post("/diary/api/admin/users", json={"username": "buddy2"}).status_code == 400      # exists
    assert o.post("/diary/api/admin/users", json={"username": "x"}).status_code == 400           # bad name
    assert o.post("/diary/api/admin/users", data="username=evil").status_code == 415             # JSON only (CSRF)
    names = [u["username"] for u in o.get("/diary/api/admin/users").get_json()]
    assert "buddy2" in names and "osku" in names
    assert b"Choose your password" in app.test_client().post("/diary/login", data={"username": "buddy2", "password": ""}).data
    # Disable / reset someone else, never yourself
    assert o.post("/diary/api/admin/users/osku/disable", json={}).status_code == 400
    assert o.post("/diary/api/admin/users/mikko/disable", json={}).status_code == 200
    assert m.get("/diary/api/guns").status_code == 401
    assert o.post("/diary/api/admin/users/mikko/enable", json={}).status_code == 200
    assert o.post("/diary/api/admin/users/mikko/clear-password", json={}).get_json()["user"]["setup_open"] is True
    assert users.verify("mikko", "mikko-password-1") is None
    assert o.post("/diary/api/admin/users/nobody/disable", json={}).status_code == 404
    assert o.post("/diary/api/admin/users/mikko/explode", json={}).status_code == 404


def test_my_data_and_delete_account(two):
    app, o, m = two
    m.put("/diary/api/guns/g-m", json={"name": "Mikko Rifle"})
    r = m.get("/diary/api/account/export")
    assert r.status_code == 200 and "mikko" in r.headers["Content-Disposition"]
    data = r.get_json()
    assert [g["name"] for g in data["guns"]] == ["Mikko Rifle"] and NOTE not in r.get_data(as_text=True)
    assert {"sessions", "profile", "loads", "chrono_strings", "scopes", "chrono_settings"} <= set(data)
    # Delete: admin only, never yourself; final copy kept, file and login gone
    assert m.post("/diary/api/admin/users/osku/delete", json={}).status_code == 403
    assert o.post("/diary/api/admin/users/osku/delete", json={}).status_code == 400
    path = users.db_path(users.by_name("mikko"))
    assert o.post("/diary/api/admin/users/mikko/delete", json={}).get_json()["deleted"] == "mikko"
    assert users.by_name("mikko") is None and not os.path.exists(path)
    assert m.get("/diary/api/guns").status_code == 401
    kept = os.path.join(diary.DATA_DIR, "deleted")
    jsons = sorted(f for f in os.listdir(kept) if f.startswith("mikko-") and f.endswith(".json"))
    assert jsons and "Mikko Rifle" in open(os.path.join(kept, jsons[-1]), encoding="utf-8").read()
    assert o.get("/diary/api/guns").status_code == 200


def test_sign_up_request_needs_admin_approval(two):
    app, o, m = two
    for n in ("rangebuddy", "spam1", "spam2"):
        if users.by_name(n):
            users.delete(n)
    diary._signups.clear()
    anon = app.test_client()
    assert b"Ask for an account" in anon.get("/diary/signup").data
    assert anon.get("/diary/privacy").status_code == 200 and b"/diary/privacy" in anon.get("/diary/login").data
    assert b"don&#39;t match" in anon.post("/diary/signup", data={"username": "rangebuddy", "password": "buddy-pass-123",
                                                                "password2": "other-pass-123"}).data
    r = anon.post("/diary/signup", data={"username": "RangeBuddy", "password": "buddy-pass-123", "password2": "buddy-pass-123"})
    assert b"Request sent" in r.data
    u = users.by_name("rangebuddy")
    assert u["pending"] and u["disabled"] and not u["is_admin"]
    # Can't log in yet, and is told why (not "wrong password"); a wrong password stays "wrong"
    assert b"waiting for an admin" in anon.post("/diary/login", data={"username": "rangebuddy", "password": "buddy-pass-123"}).data
    assert b"Wrong username" in anon.post("/diary/login", data={"username": "rangebuddy", "password": "nope-nope-nope"}).data
    # Taken name, short password
    assert b"already exists" in anon.post("/diary/signup", data={"username": "mikko", "password": "x" * 12, "password2": "x" * 12}).data
    # Admin sees it and approves; a non-admin can't
    assert o.get("/diary/api/home").get_json()["pending"] == 1
    row = [x for x in o.get("/diary/api/admin/users").get_json() if x["username"] == "rangebuddy"][0]
    assert row["pending"] is True
    assert m.post("/diary/api/admin/users/rangebuddy/approve", json={}).status_code == 403
    assert o.post("/diary/api/admin/users/rangebuddy/approve", json={}).status_code == 200
    assert o.post("/diary/api/admin/users/mikko/approve", json={}).status_code == 400     # not a request
    b = _login(app, "rangebuddy", "buddy-pass-123")
    assert b.get("/diary/api/guns").get_json() == []                                       # own, empty diary
    assert o.get("/diary/api/home").get_json()["pending"] == 0
    # Rate limit per address: 3 tries an hour (one used above for the mismatch, one ok, one taken name)
    assert b"Too many requests" in anon.post("/diary/signup", data={"username": "spam1", "password": "x" * 12, "password2": "x" * 12}).data
    assert users.by_name("spam1") is None
    users.delete("rangebuddy")


def test_deleted_accounts_are_purged_after_30_days(tmp_path):
    keep = os.path.join(diary.DATA_DIR, "deleted")
    os.makedirs(keep, exist_ok=True)
    old, new = os.path.join(keep, "gone-old.json"), os.path.join(keep, "gone-new.json")
    for p in (old, new):
        open(p, "w").write("{}")
    os.utime(old, (0, 0))
    assert users.purge_deleted() == [old] and os.path.exists(new)
    os.remove(new)
    # Backups: an account that no longer exists loses its old backups, live accounts keep theirs
    from diary import __main__ as cli
    users.ensure("osku", "osku-password-1", admin=True, db_file="diary.db")
    d = tmp_path / "b"
    d.mkdir()
    ghost, mine = d / "diary-ghost-2026-01-04.json", d / "diary-osku-2026-01-04.json"
    for p in (ghost, mine):
        p.write_text("{}")
        os.utime(p, (0, 0))
    try:
        cli.backup(str(d), 12)
    finally:
        diary.use_db(None)                      # backup() points the module at each account's file
    assert not ghost.exists() and mine.exists()


def test_visit_signature_is_checked(two):
    app, o, m = two
    png = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
    base = {"date": "2026-10-21", "lines": [{"rounds": 10}]}
    ok = o.put("/diary/api/sessions/s-sig", json=dict(base, sign={"name": "  Range   Officer ", "img": png, "at": "2026-10-21T18:00:00Z"}))
    assert ok.status_code == 200
    s = [x for x in o.get("/diary/api/sessions").get_json() if x["id"] == "s-sig"][0]
    assert s["sign"] == {"name": "Range Officer", "img": png, "at": "2026-10-21T18:00:00Z"}
    # Name only is fine; empty is dropped; anything but a PNG data URL, or too big, is refused
    assert o.put("/diary/api/sessions/s-sig", json=dict(base, sign={"name": "RO"})).status_code == 200
    o.put("/diary/api/sessions/s-sig", json=dict(base, sign={"name": " ", "img": None}))
    assert "sign" not in [x for x in o.get("/diary/api/sessions").get_json() if x["id"] == "s-sig"][0]
    for bad in ({"img": "javascript:alert(1)"}, {"img": "data:image/svg+xml;base64,AAAA"},
                {"img": "data:image/png;base64," + "A" * 400_000}, "nope"):
        assert o.put("/diary/api/sessions/s-sig", json=dict(base, sign=bad)).status_code == 400
    o.delete("/diary/api/sessions/s-sig", headers=H)


def test_service_worker_precache_files_exist():
    import re
    here = os.path.join(os.path.dirname(diary.__file__), "static")
    sw = open(os.path.join(here, "sw.js"), encoding="utf-8").read()
    block = sw[sw.index("const PRECACHE"):sw.index("];", sw.index("const PRECACHE"))]
    paths = re.findall(r'"static/([^"]+)"', block)
    assert len(paths) > 10
    bal = os.path.join(os.path.dirname(os.path.dirname(diary.__file__)), "ballistics", "static")
    for p in paths:                    # static/ballistics/* is served from the ballistics package
        f = os.path.join(bal, p[len("ballistics/"):]) if p.startswith("ballistics/") else os.path.join(here, p)
        assert os.path.isfile(f), p
    fonts = {f for f in os.listdir(os.path.join(here, "fonts")) if f.endswith(".woff2")}
    assert fonts <= {p.split("/")[-1] for p in paths}          # every font is precached


def _precache_hash():
    """Hash of every precached file's content (they are served cache-first, so a change needs a new VERSION)."""
    import hashlib
    import re
    here = os.path.join(os.path.dirname(diary.__file__), "static")
    bal = os.path.join(os.path.dirname(os.path.dirname(diary.__file__)), "ballistics", "static")
    sw = open(os.path.join(here, "sw.js"), encoding="utf-8").read()
    block = sw[sw.index("const PRECACHE"):sw.index("];", sw.index("const PRECACHE"))]
    h = hashlib.sha256()
    for p in sorted(re.findall(r'"static/([^"]+)"', block)):
        f = os.path.join(bal, p[len("ballistics/"):]) if p.startswith("ballistics/") else os.path.join(here, p)
        h.update(p.encode() + b"\0" + open(f, "rb").read() + b"\0")
    return h.hexdigest()[:16], sw


def test_service_worker_version_bumped_with_cached_files():
    """Phones keep serving the old copy of a cache-first file until VERSION changes. sw.js records the hash
    of the precached files it was last bumped for; change a file → bump VERSION and update the hash."""
    import re
    digest, sw = _precache_hash()
    m = re.search(r'CACHED_FILES_HASH = "([0-9a-f]+)"', sw)
    assert m and m.group(1) == digest, (
        f"A precached file changed: bump VERSION in diary/static/sw.js and set CACHED_FILES_HASH = \"{digest}\"")


def test_demo_account(two):
    app, o, m = two
    from diary import demo
    anon = app.test_client()
    assert anon.get("/diary/demo").status_code == 404                 # no demo until demo-reset
    assert b"Try the demo" not in anon.get("/diary/login").data
    try:
        print(demo.reset())
        u = users.by_name("demo")
        assert u["demo"] and not u["is_admin"]
        r = anon.get("/diary/demo")
        assert r.status_code == 302 and r.headers["Location"].endswith("/diary/")
        h = anon.get("/diary/api/home").get_json()
        assert h["demo"] is True and h["diary"]["visits"] >= 1
        assert len(anon.get("/diary/api/chrono/strings").get_json()) >= 10
        assert len(anon.get("/diary/api/zero").get_json()[0]["zeros"]) == 3
        assert anon.put("/diary/api/account/password", json={"current": "x", "new": "y" * 12}).status_code == 403
        assert anon.get("/diary/api/admin/users").status_code == 403
        assert b"Try the demo" in app.test_client().get("/diary/login").data
        # A visitor's change is gone after the next reset, and the reset logs visitors out
        anon.put("/diary/api/guns/g-new", json={"name": "Visitor gun"})
        demo.reset()
        assert anon.get("/diary/api/guns").status_code == 401
        anon.get("/diary/demo")
        assert "Visitor gun" not in [g["name"] for g in anon.get("/diary/api/guns").get_json()]
        # Other accounts are untouched
        assert "Osku Rifle" in [g["name"] for g in o.get("/diary/api/guns").get_json()]
    finally:
        diary.use_db(None)
        if users.by_name("demo"):
            users.delete("demo")


def test_health_endpoint(two, tmp_path, monkeypatch):
    app, o, m = two
    anon = app.test_client()
    monkeypatch.setenv("DIARY_BACKUP_DIR", str(tmp_path))
    monkeypatch.setenv("DIARY_DISK_MIN_MB", "1")
    r = anon.get("/diary/health")                                   # no login needed
    j = r.get_json()
    assert r.status_code == 200 and j["checks"]["database"] == "ok"
    assert j["checks"]["backup"] == "none" and j["status"] == "degraded"
    (tmp_path / "diary-osku-2026-10-18.json").write_text("{}")
    j = anon.get("/diary/health").get_json()
    assert j["checks"] == {"database": "ok", "disk": "ok", "backup": "ok"} and j["status"] == "ok"
    os.utime(tmp_path / "diary-osku-2026-10-18.json", (0, 0))
    assert anon.get("/diary/health").get_json()["checks"]["backup"] == "stale"
    monkeypatch.setenv("DIARY_BACKUP_MAX_DAYS", "0")
    assert "backup" not in anon.get("/diary/health").get_json()["checks"]
    body = anon.get("/diary/health").get_data(as_text=True)
    assert "osku" not in body and "mikko" not in body                # no account names
    from diary import health
    monkeypatch.setattr(health, "_db_ok", lambda: False)
    r = anon.get("/diary/health")
    assert r.status_code == 503 and r.get_json()["status"] == "down"


def test_gun_with_two_calibres(two):
    app, o, m = two
    body = {"name": "S&W 19-3", "type": "Revolveri", "cal": ".357 Magnum", "cals": [".38 Special", " "]}
    assert o.put("/diary/api/guns/g-sw19", json=body).status_code == 200
    g = [x for x in o.get("/diary/api/guns").get_json() if x["id"] == "g-sw19"][0]
    assert g["cal"] == ".357 Magnum" and g["cals"] == [".38 Special"]
    assert o.put("/diary/api/guns/g-sw19", json=dict(body, cals=".38")).status_code == 400   # must be a list
    o.delete("/diary/api/guns/g-sw19", headers=H)


def test_lockout_ends_when_admin_resets_password(two, monkeypatch):
    """5 wrong passwords lock the address for 15 min – unless an admin gives that account a new password."""
    app, o, _m = two
    monkeypatch.setattr(diary.time, "sleep", lambda s: None)
    diary._fails.clear()
    phone = app.test_client()
    env = {"REMOTE_ADDR": "10.9.8.7"}
    for _ in range(5):
        phone.post("/diary/login", data={"username": "mikko", "password": "wrong-guess-1"}, environ_base=env)
    r = phone.post("/diary/login", data={"username": "mikko", "password": "mikko-password-1"}, environ_base=env)
    assert r.status_code == 200 and "Too many wrong attempts" in r.get_data(as_text=True)          # locked, even with the right one
    other = phone.post("/diary/login", data={"username": "nobody", "password": "x"}, environ_base=env)
    assert "Too many wrong attempts" in other.get_data(as_text=True)                                # other names stay locked
    # Admin web page: clear the password → mikko can log in with an empty password and choose a new one
    assert o.post("/diary/api/admin/users/mikko/clear-password", json={}).status_code == 200
    r = phone.post("/diary/login", data={"username": "mikko", "password": ""}, environ_base=env)
    assert "Too many wrong attempts" not in r.get_data(as_text=True) and "Save and log in" in r.get_data(as_text=True)
    # Command line (another process: only the database changes): set_password lifts it too
    diary._fails.clear()
    users.set_password("mikko", "mikko-password-1")
    for _ in range(5):
        phone.post("/diary/login", data={"username": "mikko", "password": "wrong-guess-2"}, environ_base=env)
    assert "Too many wrong attempts" in phone.post("/diary/login", data={"username": "mikko", "password": "mikko-password-1"},
                                    environ_base=env).get_data(as_text=True)
    users.set_password("mikko", "mikko-password-2")
    assert phone.post("/diary/login", data={"username": "mikko", "password": "mikko-password-2"},
                      environ_base=env).status_code == 302
    diary._fails.clear()
