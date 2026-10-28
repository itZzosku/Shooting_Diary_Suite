"""Ballistic calculator: storage, validation, input resolution, isolation and backups. Synthetic data only.
The solver itself is tested under Node (tests/js, run by test_ballistics_js.py)."""
import os

import pytest

import diary
import ballistics as bal_pkg
from ballistics import api as ballistics
from diary import users

H = {"X-Requested-With": "fetch"}
BULLET = {"name": "Test 140", "maker": "Maker", "diameter_mm": 6.71, "length_mm": 35.3, "weight_gr": 140,
          "drag_model": "G7", "bc": 0.305}


def _app():
    from flask import Flask
    app = Flask(__name__)
    diary.init_app(app)
    bal_pkg.init_app(app)
    return app


def _login(app, name, pw):
    c = app.test_client()
    assert c.post("/diary/login", data={"username": name, "password": pw}).status_code == 302
    return c


@pytest.fixture()
def client():
    app = _app()
    users.ensure("osku", "test-password-1", admin=True, db_file="diary.db")
    conn = diary.connect()
    conn.executescript("DELETE FROM scope_zeros; DELETE FROM scopes; DELETE FROM guns;"
                       + "".join(f"DELETE FROM {t};" for t in ballistics.TABLES))
    diary.upsert_gun(conn, "g-ab10", {"name": "Maker AB-10", "cal": ".308 Win"})
    conn.commit()
    conn.close()
    c = _login(app, "osku", "test-password-1")
    c.put("/diary/api/zero/scopes/s-1", json={"name": "Test Scope", "unit": "MRAD", "click": 0.1, "zero_stop": -0.3})
    c.put("/diary/api/zero/zeros/z-1", json={"scope_id": "s-1", "rifle": "Maker AB-10", "gun_id": "g-ab10",
                                            "distance_m": 100, "elev": 2.3, "wind": -1.1})
    return c


def _all(c):
    r = c.get("/diary/api/ballistics")
    assert r.status_code == 200
    return r.get_json()


def test_schema_is_idempotent():
    path = os.path.join(diary.DATA_DIR, "bal-idem.db")
    diary.init_db(path)
    diary.init_db(path)
    conn = diary.connect(path)
    have = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    assert set(ballistics.TABLES) <= have


def test_profile_resolves_scope_zero_and_bullet(client):
    assert client.put("/diary/api/ballistics/bullets/b-1", json=BULLET).status_code == 200
    r = client.put("/diary/api/ballistics/profiles/p-1", json={
        "name": "AB-10 match", "gun_id": "g-ab10", "bullet_id": "b-1", "scope_id": "s-1", "zero_id": "z-1",
        "sight_height_mm": 55, "twist_mm": 254, "zero_range_m": 100, "mv_ms": 790, "mv_temp_c": 15})
    assert r.status_code == 200, r.get_json()
    inp = r.get_json()["profile"]["inputs"]
    assert inp["bullet"]["model"] == "G7" and inp["bullet"]["bc"] == 0.305
    assert inp["scope"] == {"name": "Test Scope", "unit": "MRAD", "click": 0.1, "zeroStop": -0.3,
                            "elevTurn": None, "windTurn": None}
    assert inp["zero"]["elev"] == 2.3 and inp["zero"]["wind"] == -1.1 and inp["zero"]["distanceM"] == 100
    assert inp["gun"] == "Maker AB-10" and inp["mv"] == 790 and inp["warnings"] == []
    data = _all(client)
    assert [p["id"] for p in data["profiles"]] == ["p-1"]
    assert data["pickers"]["scopes"][0]["zeros"][0]["id"] == "z-1"
    assert data["settings"]["card_step"] == 50


def test_missing_bullet_and_velocity_are_warnings_not_errors(client):
    r = client.put("/diary/api/ballistics/profiles/p-2", json={"name": "Bare", "sight_height_mm": 50,
                                                                 "zero_range_m": 100})
    assert r.status_code == 200
    assert set(r.get_json()["profile"]["inputs"]["warnings"]) == {"no_bullet", "no_velocity"}


@pytest.mark.parametrize("field,value", [
    ("mv_ms", 50), ("mv_ms", 2000), ("sight_height_mm", 5), ("twist_mm", 100), ("zero_range_m", 10),
    ("zero_temp_c", -60), ("zero_hpa", 300), ("zero_rh", 120), ("latitude", 95), ("twist_dir", "X"),
    ("gun_id", "bad id!"), ("mv_ms", "fast")])
def test_profile_validation(client, field, value):
    body = {"name": "P", "sight_height_mm": 50, "zero_range_m": 100, field: value}
    r = client.put("/diary/api/ballistics/profiles/p-bad", json=body)
    assert r.status_code == 400 and r.get_json()["error"].startswith("bad_")


@pytest.mark.parametrize("change,error", [
    ({"bc": 0.01}, "bad_bc"), ({"bc": None}, "bc_required"), ({"drag_model": "G9"}, "bad_drag_model"),
    ({"diameter_mm": None}, "diameter_mm_required"), ({"bc_bands": [[600, 0.3], [0, 0.29]]}, "bad_bc_bands")])
def test_bullet_validation(client, change, error):
    r = client.put("/diary/api/ballistics/bullets/b-bad", json=dict(BULLET, **change))
    assert r.status_code == 400 and r.get_json()["error"] == error


def test_multi_bc_bands_without_single_bc(client):
    body = dict(BULLET, bc=None, bc_bands=[[0, 0.29], [600, 0.305]])
    assert client.put("/diary/api/ballistics/bullets/b-band", json=body).status_code == 200
    b = next(x for x in _all(client)["bullets"] if x["id"] == "b-band")
    assert b["bc_bands"] == [[0, 0.29], [600, 0.305]]


def test_bullet_in_use_cannot_be_deleted(client):
    client.put("/diary/api/ballistics/bullets/b-1", json=BULLET)
    client.put("/diary/api/ballistics/profiles/p-1", json={"name": "P", "bullet_id": "b-1", "sight_height_mm": 50,
                                                            "zero_range_m": 100})
    r = client.delete("/diary/api/ballistics/bullets/b-1", headers=H)
    assert r.status_code == 409 and r.get_json()["error"] == "in_use"
    assert client.delete("/diary/api/ballistics/profiles/p-1", headers=H).status_code == 200
    assert client.delete("/diary/api/ballistics/bullets/b-1", headers=H).status_code == 200


def test_deletes_need_the_fetch_header(client):
    client.put("/diary/api/ballistics/matches/m-1", json={"name": "Match"})
    assert client.delete("/diary/api/ballistics/matches/m-1").status_code == 403


def test_match_stages_round_trip_and_cascade(client):
    assert client.put("/diary/api/ballistics/matches/m-1", json={"name": "Club PRS", "date": "2026-11-07"}).status_code == 200
    stage = {"name": "Stage 1", "match_id": "m-1", "ord": 0, "dial_mode": "first_hold_rest",
             "wind": {"ms": 3, "clock": 9},
             "targets": [{"range_m": 300, "w_cm": 40, "h_cm": 40}, {"label": "Steel", "range_m": 455}]}
    assert client.put("/diary/api/ballistics/stages/st-1", json=stage).status_code == 200
    s = _all(client)["stages"][0]
    assert s["targets"][0]["label"] == "T1" and s["targets"][1]["label"] == "Steel"
    assert s["wind"] == {"ms": 3, "clock": 9} and s["dial_mode"] == "first_hold_rest"
    assert client.delete("/diary/api/ballistics/matches/m-1", headers=H).status_code == 200
    assert _all(client)["stages"] == []


@pytest.mark.parametrize("change", [{"targets": []}, {"targets": [{"range_m": 0}]}, {"dial_mode": "guess"},
                                    {"wind": {"ms": 50, "clock": 3}}, {"targets": [{"w_cm": 30}]}])
def test_stage_validation(client, change):
    stage = dict({"name": "S", "targets": [{"range_m": 300}]}, **change)
    assert client.put("/diary/api/ballistics/stages/st-bad", json=stage).status_code == 400


def test_settings(client):
    assert client.put("/diary/api/ballistics/settings", json={"card_start": 25, "card_step": 10,
                                                              "card_end": 300}).status_code == 200
    assert _all(client)["settings"] == {"card_start": 25, "card_step": 10, "card_end": 300}
    assert client.put("/diary/api/ballistics/settings", json={"card_start": 300, "card_step": 10,
                                                              "card_end": 100}).status_code == 400


def test_login_required():
    anon = _app().test_client()
    assert anon.get("/diary/api/ballistics").status_code == 401
    assert anon.get("/diary/ballistics").status_code == 302


def test_accounts_are_isolated(client):
    client.put("/diary/api/ballistics/bullets/b-1", json=BULLET)
    m = users.by_name("ballmikko")
    if m:
        with users.uconnect() as c:
            c.execute("DELETE FROM users WHERE username='ballmikko'")
        if os.path.exists(users.db_path(m)):
            os.remove(users.db_path(m))
    users.create("ballmikko", "mikko-password-1")
    mc = _login(_app(), "ballmikko", "mikko-password-1")
    assert _all(mc)["bullets"] == []
    mc.delete("/diary/api/ballistics/bullets/b-1", headers=H)
    assert [b["id"] for b in _all(client)["bullets"]] == ["b-1"]


def test_export_import_round_trip(client):
    client.put("/diary/api/ballistics/bullets/b-1", json=BULLET)
    client.put("/diary/api/ballistics/profiles/p-1", json={"name": "P", "bullet_id": "b-1", "sight_height_mm": 50,
                                                            "zero_range_m": 100, "mv_ms": 800})
    client.put("/diary/api/ballistics/matches/m-1", json={"name": "M"})
    client.put("/diary/api/ballistics/stages/st-1", json={"name": "S", "match_id": "m-1",
                                                          "targets": [{"range_m": 400}]})
    conn = diary.connect()
    from diary.summary import export_data
    data = export_data(conn)
    for t in ballistics.TABLES:
        conn.execute(f"DELETE FROM {t}")
    ballistics.import_rows(conn, data, replace=True)
    conn.commit()
    again = export_data(conn)
    conn.close()
    for t in ballistics.TABLES:
        assert again[t] == data[t], t
    assert client.get("/diary/api/ballistics/summary").get_json()["profiles"] == 1


def test_page_and_static_files_exist(client):
    assert client.get("/diary/ballistics").status_code == 200
    for f in ("drag.js", "solver.js", "reticle.js"):
        assert client.get(f"/diary/static/ballistics/{f}").status_code == 200


def test_diary_core_does_not_import_ballistics():
    """ballistics/ is its own package: only its bridge.py imports diary, and diary never imports it (it only lists
    it in EXTENSION_MODULES for the command line)."""
    import glob, os, re
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for f in glob.glob(os.path.join(root, "diary", "*.py")):
        src = open(f, encoding="utf-8").read()
        assert not re.search(r"^\s*(from|import)\s+(\.|ballistics)\s*\.?\s*(ballistics|reticles|weather)\b", src, re.M), f
    for f in glob.glob(os.path.join(root, "ballistics", "*.py")):
        if f.endswith("bridge.py"):
            continue
        src = open(f, encoding="utf-8").read()
        assert not re.search(r"^\s*(from|import)\s+diary\b", src, re.M), f


def test_command_line_backup_carries_ballistics_rows(client, tmp_path):
    client.put("/diary/api/ballistics/bullets/b-1", json=BULLET)
    import json, subprocess, sys, os
    env = dict(os.environ)
    out = tmp_path / "x.json"
    r = subprocess.run([sys.executable, "-m", "diary", "export", "--user", "osku", str(out)], env=env, capture_output=True, text=True,
                       cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    assert r.returncode == 0, r.stderr
    assert [b["id"] for b in json.loads(out.read_text())["bal_bullets"]] == ["b-1"]


def test_every_ballistics_api_route_needs_login():
    app = _app()
    anon = app.test_client()
    n = 0
    for rule in app.url_map.iter_rules():
        if not rule.rule.startswith("/diary/api/ballistics"):
            continue
        url = rule.rule.replace("<bid>", "x").replace("<pid>", "x").replace("<mid>", "x").replace("<sid>", "x").replace("<rid>", "x")
        for m in rule.methods - {"HEAD", "OPTIONS"}:
            r = anon.open(url, method=m, json={} if m == "PUT" else None, headers={"X-Requested-With": "fetch"})
            assert r.status_code == 401, (m, url, r.status_code)
            n += 1
    assert n >= 15
