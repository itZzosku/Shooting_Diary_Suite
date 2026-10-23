"""Club ammo boxes (club/): shooters without accounts, stock from moves, roles. Synthetic data only."""
import sqlite3
import time

import pytest
from flask import Flask

import club
import diary
from diary import users


def _app():
    app = Flask(__name__)
    app.config["SECRET_KEY"] = "test"
    diary.init_app(app)
    club.init_app(app)
    return app


def _login(app, name, pw):
    c = app.test_client()
    r = c.post("/diary/login", data={"username": name, "password": pw})
    assert r.status_code == 302
    return c


@pytest.fixture()
def setup():
    """Club 'testclub': admin 'cadmin', holder 'chold' of 9 mm B; boxes 9 mm A/B, 7.62 A."""
    club.init_db()
    with sqlite3.connect(club.db_path()) as c:
        for t in ("clubs", "club_roles", "items", "boxes", "levels", "moves"):
            c.execute(f"DELETE FROM {t}")
    club._posts.clear()
    for n in ("cadmin", "chold", "cshooter"):
        if not users.by_name(n):
            users.create(n, n + "-password-1")
    app = _app()
    cid, _slug = club.create_club("testclub", "Test Club")
    club.set_role(cid, users.by_name("cadmin")["id"], "admin")
    a = _login(app, "cadmin", "cadmin-password-1")
    a.put("/ammo/api/c/testclub", json={"name": "Test Club", "mobilepay": "12345"})
    ids = {}
    for cal, name, price in (("9 mm Luger", "Geco", 0.30), ("9 mm Luger", "S&B", 0.25), ("7.62x39", "", 0.40)):
        ids[name or "762"] = a.post("/ammo/api/c/testclub/items", json={"calibre": cal, "name": name, "price": price}).json["id"]
    for name, cal, extra in (("9 mm A", "9 mm Luger", {"low": 200, "type": "Pistooli"}),
                             ("9 mm B", "9 mm Luger", {"holder_user": "chold"}),
                             ("7.62 A", "7.62x39", {"type": "Kivääri"})):
        r = a.post("/ammo/api/c/testclub/boxes", json=dict(name=name, calibre=cal, **extra))
        assert r.status_code == 200, r.json
        ids[name] = r.json["id"]
    st = a.get("/ammo/api/c/testclub").json
    codes = {b["name"]: b["code"] for b in st["boxes"]}
    return app, a, ids, codes


def _today():
    return time.strftime("%Y-%m-%d")


def _stock(a, box):
    return next(b for b in a.get("/ammo/api/c/testclub").json["boxes"] if b["name"] == box)["total"]


def test_buy_use_move_count(setup):
    app, a, ids, codes = setup
    assert a.post("/ammo/api/c/testclub/moves", json={"kind": "buy", "box": ids["9 mm B"], "item": ids["Geco"], "qty": 1000}).status_code == 200
    assert a.post("/ammo/api/c/testclub/moves", json={"kind": "move", "box": ids["9 mm B"], "to": ids["9 mm A"], "item": ids["Geco"], "qty": 300}).status_code == 200
    anon = app.test_client()
    info = anon.get(f"/ammo/api/b/{codes['9 mm A']}").json
    assert info["box"]["calibre"] == "9 mm Luger" and info["club"]["mobilepay"] == "12345" and info["me"] is None
    assert {i["name"] for i in info["items"]} == {"Geco"}                  # what's in the box
    assert {i["name"] for i in anon.get(f"/ammo/api/b/{codes['9 mm B']}").json["items"]} == {"Geco"}
    a.post("/ammo/api/c/testclub/moves", json={"kind": "buy", "box": ids["9 mm B"], "item": ids["S&B"], "qty": 50})
    assert {i["name"] for i in anon.get(f"/ammo/api/b/{codes['9 mm B']}").json["items"]} == {"Geco", "S&B"}
    assert [i["name"] for i in anon.get(f"/ammo/api/b/{codes['7.62 A']}").json["items"]] == [""]   # empty: every ammo of the calibre
    r = anon.post(f"/ammo/api/b/{codes['9 mm A']}/use", json={"name": "Matti M", "date": _today(), "device": "dev1",
                                                               "client_id": "c1", "lines": [{"item": ids["Geco"], "qty": 150}]})
    assert r.status_code == 200 and r.json["total"] == 45.0 and len(r.json["ref"]) == 4
    # Sent again from the offline queue: no double
    r2 = anon.post(f"/ammo/api/b/{codes['9 mm A']}/use", json={"name": "Matti M", "date": _today(), "device": "dev1",
                                                                "client_id": "c1", "lines": [{"item": ids["Geco"], "qty": 150}]})
    assert r2.json["ref"] == r.json["ref"]
    assert _stock(a, "9 mm A") == 150 and _stock(a, "9 mm B") == 750
    w = {x["kind"] + x.get("name", x.get("calibre", "")) for x in a.get("/ammo/api/c/testclub").json["warnings"]}
    assert "box9 mm A" in w                                                 # 150 < refill level 200
    a.post("/ammo/api/c/testclub/moves", json={"kind": "count", "box": ids["9 mm B"], "item": ids["Geco"], "qty": 690})
    assert _stock(a, "9 mm B") == 740
    # Ammo of another calibre can't go in, can't move more than there is
    assert a.post("/ammo/api/c/testclub/moves", json={"kind": "buy", "box": ids["9 mm A"], "item": ids["762"], "qty": 5}).status_code == 400
    assert a.post("/ammo/api/c/testclub/moves", json={"kind": "move", "box": ids["9 mm A"], "to": ids["9 mm B"], "item": ids["Geco"], "qty": 9999}).status_code == 400
    log = a.get("/ammo/api/c/testclub/log").json["moves"]
    assert any(m["kind"] == "use" and m["name"] == "Matti M" for m in log)
    assert all("device" not in m for m in log)


def test_shooter_validation_and_withdraw(setup):
    app, a, ids, codes = setup
    anon = app.test_client()
    url = f"/ammo/api/b/{codes['7.62 A']}/use"
    assert anon.post(url, json={"name": "M", "date": _today(), "lines": [{"item": ids["762"], "qty": 10}]}).status_code == 400
    assert anon.post(url, json={"name": "Matti", "date": "2001-01-01", "lines": [{"item": ids["762"], "qty": 10}]}).status_code == 400
    assert anon.post(url, json={"name": "Matti", "date": _today(), "lines": [{"item": ids["Geco"], "qty": 10}]}).status_code == 400
    assert anon.post(url, json={"name": "Matti", "date": _today(), "lines": [{"item": ids["762"], "qty": 0}]}).status_code == 400
    assert anon.get("/ammo/api/b/notarealcode123").status_code == 404
    r = anon.post(url, json={"name": "Matti", "date": _today(), "device": "devX", "paid": False,
                             "lines": [{"item": ids["762"], "qty": 30}]})
    grp = r.json["grp"]
    assert not r.json["paid"]
    mine = anon.post(f"/ammo/api/b/{codes['7.62 A']}/mine", json={"device": "devX"}).json["entries"]
    assert [e["grp"] for e in mine] == [grp]
    assert anon.post(f"/ammo/api/b/{codes['7.62 A']}/withdraw/{grp}", json={"device": "other"}).status_code == 404
    assert anon.post(f"/ammo/api/b/{codes['7.62 A']}/withdraw/{grp}", json={"device": "devX"}).status_code == 200
    assert _stock(a, "7.62 A") == 0
    # A new secret: the old link stops working
    old = codes["7.62 A"]
    a.post(f"/ammo/api/c/testclub/boxes/{ids['7.62 A']}/newcode", json={})
    assert anon.get(f"/ammo/api/b/{old}").status_code == 404


def test_roles(setup):
    app, a, ids, codes = setup
    anon, h = app.test_client(), _login(app, "chold", "chold-password-1")
    s = _login(app, "cshooter", "cshooter-password-1")
    assert anon.get("/ammo/api/c/testclub").status_code == 401
    assert s.get("/ammo/api/c/testclub").status_code == 404                 # no role: the club isn't there
    st = h.get("/ammo/api/c/testclub").json
    assert st["role"] == "holder"
    assert {b["name"]: bool(b["code"]) for b in st["boxes"]} == {"9 mm A": False, "9 mm B": True, "7.62 A": False}
    assert h.post("/ammo/api/c/testclub/moves", json={"kind": "buy", "box": ids["9 mm B"], "item": ids["S&B"], "qty": 500}).status_code == 200
    assert h.post("/ammo/api/c/testclub/moves", json={"kind": "buy", "box": ids["9 mm A"], "item": ids["S&B"], "qty": 500}).status_code == 403
    assert h.post("/ammo/api/c/testclub/items", json={"calibre": "x", "price": 1}).status_code == 403
    anon.post(f"/ammo/api/b/{codes['9 mm B']}/use", json={"name": "Secret Name", "date": _today(), "lines": [{"item": ids["S&B"], "qty": 10}]})
    assert all(m["name"] != "Secret Name" for m in h.get("/ammo/api/c/testclub/log").json["moves"])
    assert h.get("/ammo/testclub/admin").status_code == 200 and s.get("/ammo/testclub/admin").status_code == 404


def test_logged_in_fills_name_and_adds_to_diary(setup):
    app, a, ids, codes = setup
    s = _login(app, "cshooter", "cshooter-password-1")
    s.put("/diary/api/profile", json={"name": "Seppo Shooter"})
    assert s.get(f"/ammo/api/b/{codes['9 mm A']}").json["me"]["name"] == "Seppo Shooter"
    r = s.post(f"/ammo/api/b/{codes['9 mm A']}/use", json={"name": "Seppo Shooter", "date": _today(), "diary": True,
                                                            "device": "d", "lines": [{"item": ids["Geco"], "qty": 50}]})
    assert r.json["diary"] is True
    visits = s.get("/diary/api/sessions").json
    line = [ln for v in visits if v["date"] == _today() for ln in v["lines"] if ln.get("club") == r.json["grp"]]
    assert line and line[0]["rounds"] == 50 and line[0]["type"] == "Pistooli" and line[0]["cal"] == "9 mm Luger"
    s.post(f"/ammo/api/b/{codes['9 mm A']}/withdraw/{r.json['grp']}", json={"device": "d"})
    assert not [v for v in s.get("/diary/api/sessions").json if any(ln.get("club") == r.json["grp"] for ln in v["lines"])]


def test_bridge_is_the_only_diary_import():
    import pathlib
    for p in pathlib.Path(club.__file__).parent.glob("*.py"):
        if p.name != "bridge.py":
            assert "diary" not in [ln.split()[1].split(".")[0] for ln in p.read_text().splitlines()
                                   if ln.startswith(("import ", "from "))], p


def test_club_id_from_name_and_delete():
    club.init_db()
    with club.connect() as c:
        c.execute("DELETE FROM clubs WHERE slug LIKE 'vaares%'")
    assert club.create_club("", "VaaRes SRA")[1] == "vaares-sra"
    assert club.create_club("", "Vaares  SRA!")[1] == "vaares-sra-2"
    assert club.create_club("", "Ähtärin Ampujat")[1] == "ahtarin-ampujat"
    from club.__main__ import main
    with pytest.raises(SystemExit):
        main(["club-delete", "nosuchclub"])
    main(["club-delete", "vaares-sra-2"])
    assert club.club_by_slug("vaares-sra-2", club.connect()) is None
    assert club.club_by_slug("vaares-sra", club.connect())
