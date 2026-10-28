"""Ballistics phase 2: velocity from chrono strings, FMI weather parsing, saved ranges, the shared reticle
library. Synthetic data only; FMI is never contacted (a fixture stands in for its XML)."""
import os

import pytest

import diary
from diary import ballistics, reticles, users, weather

H = {"X-Requested-With": "fetch"}
BULLET = {"name": "Test 140", "diameter_mm": 6.71, "length_mm": 35.3, "weight_gr": 140, "drag_model": "G7", "bc": 0.305}


def _app():
    from flask import Flask
    app = Flask(__name__)
    diary.init_app(app)
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
    conn.executescript("DELETE FROM chrono_shots; DELETE FROM chrono_strings; DELETE FROM loads; DELETE FROM guns; "
                       "DELETE FROM kv WHERE key LIKE 'bal_%';" + "".join(f"DELETE FROM {t};" for t in ballistics.TABLES))
    diary.upsert_gun(conn, "g-1", {"name": "Test Rifle", "cal": "6.5 Creedmoor"})
    conn.execute("INSERT INTO loads(id,name,kind,cal,bullet_gr) VALUES('l-1','6.5 CM · Test 140 gr','factory','6.5 Creedmoor',140)")
    # four days, the newest last; day temperatures 0, 10, 20, 30 °C with velocities rising 1 m/s per °C
    for i, (date, temp, base) in enumerate([("2026-06-01", 0, 800), ("2026-07-01", 10, 810),
                                             ("2026-08-01", 20, 820), ("2026-09-01", 30, 830)]):
        sid = f"s-{i}"
        conn.execute("INSERT INTO chrono_strings(id,date,gun_id,load_id,source,temp_c,created_at) VALUES(?,?,?,?,?,?,?)",
                     (sid, date, "g-1", "l-1", "test", temp, date))
        for n, dv in enumerate([-2, 0, 2, 0, 60]):          # the last shot is excluded
            conn.execute("INSERT INTO chrono_shots(string_id,n,v_ms,excluded,temp_c) VALUES(?,?,?,?,?)",
                         (sid, n + 1, base + dv, 1 if dv == 60 else 0, temp))
    conn.commit()
    conn.close()
    with reticles.connect() as lc:
        lc.execute("DELETE FROM reticles")
    return _login(app, "osku", "test-password-1")


def _profile(c, **kw):
    c.put("/diary/api/ballistics/bullets/b-1", json=BULLET)
    body = dict({"name": "P", "gun_id": "g-1", "load_id": "l-1", "bullet_id": "b-1", "sight_height_mm": 50,
                 "zero_range_m": 100, "mv_ms": 700, "mv_temp_c": 15}, **kw)
    r = c.put("/diary/api/ballistics/profiles/p-1", json=body)
    assert r.status_code == 200, r.get_json()
    return r.get_json()["profile"]["inputs"]


# ------------------------------------------------------------------ chrono velocity
def test_velocity_from_the_newest_strings(client):
    inp = _profile(client, mv_source="chrono", mv_strings=2)
    assert inp["mvSource"] == "chrono"
    assert inp["mv"] == 825.0                      # newest two days: 820 and 830, excluded shot dropped
    assert inp["mvTempC"] == 25.0 and inp["chrono"]["strings"] == 2 and inp["chrono"]["n"] == 8
    assert inp["chrono"]["from"] == "2026-08-01" and inp["chrono"]["to"] == "2026-09-01"


def test_temperature_sensitivity_from_chrono(client):
    inp = _profile(client, temp_sens_source="chrono")
    assert inp["tempSensSource"] == "chrono" and abs(inp["tempSens"] - 1.0) < 1e-6
    assert inp["mv"] == 700 and inp["mvSource"] == "manual"         # velocity source stays manual


def test_chrono_without_data_falls_back_to_manual(client):
    inp = _profile(client, mv_source="chrono", gun_id=None)
    assert inp["mvSource"] == "manual" and inp["mv"] == 700 and "no_chrono_data" in inp["warnings"]


def test_corrections_and_flags_round_trip(client):
    inp = _profile(client, spin_drift=True, coriolis=1, aero_jump=False, azimuth_deg=270, twist_mm=203)
    assert inp["spinDrift"] is True and inp["coriolis"] is True and inp["aeroJump"] is False
    assert inp["azimuthDeg"] == 270


# ------------------------------------------------------------------ weather
FMI_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<wfs:FeatureCollection xmlns:wfs="http://www.opengis.net/wfs/2.0" xmlns:om="http://www.opengis.net/om/2.0"
  xmlns:omso="http://inspire.ec.europa.eu/schemas/omso/3.0" xmlns:gml="http://www.opengis.net/gml/3.2"
  xmlns:wml2="http://www.opengis.net/waterml/2.0" xmlns:target="http://xml.fmi.fi/namespace/om/atmosphericfeatures/1.1"
  xmlns:sams="http://www.opengis.net/samplingSpatial/2.0">
%s
</wfs:FeatureCollection>"""
MEMBER = """<wfs:member><omso:PointTimeSeriesObservation>
 <om:featureOfInterest><sams:SF_SpatialSamplingFeature>
  <sams:sampledFeature><target:LocationCollection><target:member><target:Location>
   <gml:name codeSpace="http://xml.fmi.fi/namespace/locationcode/name">%(name)s</gml:name>
   <gml:name codeSpace="http://xml.fmi.fi/namespace/locationcode/geoid">-1</gml:name>
  </target:Location></target:member></target:LocationCollection></sams:sampledFeature>
  <sams:shape><gml:Point><gml:pos>%(lat)s %(lon)s </gml:pos></gml:Point></sams:shape>
 </sams:SF_SpatialSamplingFeature></om:featureOfInterest>
 <om:result><wml2:MeasurementTimeseries gml:id="obs-obs-1-1-%(param)s">
  <wml2:point><wml2:MeasurementTVP><wml2:time>2026-10-28T08:00:00Z</wml2:time><wml2:value>%(old)s</wml2:value></wml2:MeasurementTVP></wml2:point>
  <wml2:point><wml2:MeasurementTVP><wml2:time>2026-10-28T08:10:00Z</wml2:time><wml2:value>%(new)s</wml2:value></wml2:MeasurementTVP></wml2:point>
 </wml2:MeasurementTimeseries></om:result>
</omso:PointTimeSeriesObservation></wfs:member>"""


def _xml():
    m = []
    near = dict(name="Near Station", lat="63.01", lon="22.71")
    far = dict(name="Far Airport", lat="63.20", lon="22.90")
    m += [MEMBER % dict(near, param="t2m", old="4.0", new="5.5"), MEMBER % dict(near, param="rh", old="80", new="82"),
          MEMBER % dict(near, param="p_sea", old="NaN", new="NaN"),           # no barometer here
          MEMBER % dict(far, param="p_sea", old="1012.0", new="1013.2"), MEMBER % dict(far, param="t2m", old="3", new="3.1"),
          MEMBER % dict(far, param="ws_10min", old="4.0", new="3.6"), MEMBER % dict(far, param="wd_10min", old="250", new="260")]
    return FMI_XML % "\n".join(m).encode()


def test_fmi_parse_and_nearest_station_per_value():
    st = weather.parse(_xml())
    assert {s["name"] for s in st} == {"Near Station", "Far Airport"}
    near = weather.nearest(st, 63.0, 22.7)
    assert near["t2m"]["value"] == 5.5 and near["t2m"]["station"] == "Near Station"
    assert near["p_sea"]["value"] == 1013.2 and near["p_sea"]["station"] == "Far Airport"   # nearest with a barometer
    assert near["ws_10min"]["value"] == 3.6


def test_station_pressure_from_sea_level():
    assert weather.station_pressure(1013.25, 0) == pytest.approx(1013.25)
    assert 895 < weather.station_pressure(1013.25, 1000, 8.5) < 902


def test_weather_endpoint(client, monkeypatch):
    weather._cache.clear()
    monkeypatch.setattr(weather, "_fetch", lambda lat, lon: _xml())
    monkeypatch.setattr(weather.current, "__defaults__", (None, weather._fetch))
    r = client.get("/diary/api/ballistics/weather?lat=63.0&lon=22.7&alt=50")
    assert r.status_code == 200, r.get_json()
    j = r.get_json()
    assert j["temp_c"] == 5.5 and j["rh"] == 82 and j["hpa_sea"] == 1013.2
    assert 1006 < j["hpa_station"] < 1008 and j["sources"]["p_sea"]["station"] == "Far Airport"
    assert client.get("/diary/api/ballistics/weather?lat=95&lon=22").status_code == 400


def test_weather_failure_is_a_502(client, monkeypatch):
    weather._cache.clear()
    def boom(lat, lon):
        raise OSError("no network")
    monkeypatch.setattr(weather.current, "__defaults__", (None, boom))
    r = client.get("/diary/api/ballistics/weather?lat=61.0&lon=25.0")
    assert r.status_code == 502 and r.get_json()["error"] == "weather_unavailable"


# ------------------------------------------------------------------ saved ranges
def test_saved_ranges(client):
    r = client.put("/diary/api/ballistics/ranges", json=[{"name": "Club range", "lat": 63.0, "lon": 22.7, "alt_m": 40}])
    assert r.status_code == 200
    got = client.get("/diary/api/ballistics").get_json()["ranges"]
    assert got[0]["name"] == "Club range" and got[0]["id"].startswith("r-")
    assert client.put("/diary/api/ballistics/ranges", json=[{"name": "X", "lat": 99, "lon": 0}]).status_code == 400
    conn = diary.connect()
    data = ballistics.export_rows(conn)
    conn.close()
    assert "Club range" in data["bal_kv"]["bal_ranges"]


# ------------------------------------------------------------------ reticle library
ELEMENTS = [{"t": "line", "x1": -10, "y1": 0, "x2": 10, "y2": 0, "w": 0.03}, {"t": "dot", "x": 0, "y": 0, "r": 0.05},
            {"t": "text", "x": 2, "y": 0.5, "text": "2", "size": 0.3}]
RET = {"name": "Generic mil", "maker": "Test", "unit": "MRAD", "focal_plane": "FFP", "spec": {"v": 1}, "elements": ELEMENTS}


def _mikko():
    m = users.by_name("retmikko")
    if m:
        with users.uconnect() as c:
            c.execute("DELETE FROM users WHERE username='retmikko'")
        if os.path.exists(users.db_path(m)):
            os.remove(users.db_path(m))
    users.create("retmikko", "mikko-password-1")
    return _login(_app(), "retmikko", "mikko-password-1")


def test_library_admin_writes_everyone_reads(client):
    r = client.put("/diary/api/ballistics/reticles/r-gen", json=RET)
    assert r.status_code == 200 and r.get_json()["reticle"]["rev"] == 1
    assert client.put("/diary/api/ballistics/reticles/r-gen", json=RET).get_json()["reticle"]["rev"] == 2
    m = _mikko()
    lib = m.get("/diary/api/ballistics").get_json()
    assert [x["id"] for x in lib["reticles"]] == ["r-gen"] and lib["is_admin"] is False
    assert m.put("/diary/api/ballistics/reticles/r-gen", json=RET).status_code == 403
    assert m.delete("/diary/api/ballistics/reticles/r-gen", headers=H).status_code == 403
    assert lib["reticles"][0]["updated_by"] == "osku"


def test_archived_reticle_still_resolves_for_its_profiles(client):
    client.put("/diary/api/ballistics/reticles/r-old", json=RET)
    _profile(client, reticle_id="r-old")
    assert client.delete("/diary/api/ballistics/reticles/r-old", headers=H).status_code == 200
    assert [x["id"] for x in client.get("/diary/api/ballistics/reticles").get_json()] == []
    mine = client.get("/diary/api/ballistics").get_json()["reticles"]
    assert [x["id"] for x in mine] == ["r-old"] and mine[0]["archived"] == 1


@pytest.mark.parametrize("change,error", [
    ({"focal_plane": "SFP"}, "design_mag_required"), ({"unit": "NATO"}, "bad_unit"), ({"elements": []}, "bad_elements"),
    ({"elements": [{"t": "line", "x1": 0}]}, "bad_elements"), ({"elements": [{"t": "blob"}]}, "bad_elements"),
    ({"elements": [{"t": "text", "x": 0, "y": 0, "size": 1, "text": "x" * 40}]}, "bad_elements"),
    ({"spec": "nope"}, "bad_spec"), ({"name": ""}, "name_required")])
def test_reticle_validation(client, change, error):
    r = client.put("/diary/api/ballistics/reticles/r-bad", json=dict(RET, **change))
    assert r.status_code == 400 and r.get_json()["error"] == error


def test_library_backup(client, tmp_path):
    client.put("/diary/api/ballistics/reticles/r-gen", json=RET)
    p = reticles.backup(str(tmp_path), keep=2)
    assert p and os.path.exists(p)
