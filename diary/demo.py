"""Demo account: a shared showcase anyone can open at /diary/demo without a password.

`python -m diary demo-reset` (re)creates the account "demo" and fills it with invented sample data through the
app's own API, so it always matches what the app accepts. Run it nightly from cron; visitors can change
anything in between. The account can't change its password, and the password itself is random and never
shown (a reset also logs every demo visitor out).
"""
import datetime as dt
import os
import random
import secrets

DEMO_USER = "demo"
H = {"X-Requested-With": "fetch"}


def is_demo(user):
    return bool(user and user.get("demo"))


def demo_user():
    from . import users
    u = users.by_name(DEMO_USER)
    return u if is_demo(u) and not u["disabled"] else None


def reset(today=None):
    """Recreate the demo account's data. Returns a short summary line."""
    from flask import Flask
    from . import init_app, init_db, users
    pw = secrets.token_urlsafe(24)
    u = users.by_name(DEMO_USER)
    if u and not is_demo(u):
        raise SystemExit(f'An ordinary account is already called "{DEMO_USER}" – rename it first (user-rename).')
    if u:
        path = users.db_path(u)
        for p in (path, path + "-wal", path + "-shm"):
            if os.path.exists(p):
                os.remove(p)
        init_db(path)
        users.set_password(DEMO_USER, pw)
    else:
        users.create(DEMO_USER, pw)
    with users.uconnect() as c:
        c.execute("UPDATE users SET demo=1, disabled=0, pending=0 WHERE username=?", (DEMO_USER,))

    app = Flask("demo-reset")
    init_app(app)
    client = app.test_client()
    r = client.post("/diary/login", data={"username": DEMO_USER, "password": pw})
    if r.status_code != 302:
        raise SystemExit("Could not log in as the demo account")
    return build(client, today or dt.date.today())


def _ok(r):
    if r.status_code >= 400:
        raise SystemExit(f"Demo data refused: {r.status_code} {r.get_data(as_text=True)[:200]}")
    return r.get_json(silent=True)


def build(c, today):
    rnd = random.Random(7)
    day = lambda n: (today - dt.timedelta(days=n)).isoformat()
    put = lambda url, body: _ok(c.put("/diary/api/" + url, json=body, headers=H))
    post = lambda url, body: _ok(c.post("/diary/api/" + url, json=body, headers=H))

    put("profile", {"name": "Demo Shooter", "sra": "", "ipsc": "", "hunterNo": ""})
    guns = [
        ("g-t3x", {"name": "Tikka T3x TAC", "type": "Kivääri", "cal": "6.5 Creedmoor", "mode": "2 - lippaallinen kertatuli", "startCount": 420}),
        ("g-ar", {"name": "AR-15 16\"", "type": "Kivääri", "cal": ".223 Rem", "mode": "3 - itselataava kertatuli", "startCount": 900}),
        ("g-cz", {"name": "CZ 457 Varmint", "type": "Pienoiskivääri", "cal": ".22 LR", "mode": "2 - lippaallinen kertatuli", "startCount": 2500}),
        ("g-g17", {"name": "Glock 17", "type": "Pistooli", "cal": "9 mm Luger", "mode": "3 - itselataava kertatuli", "startCount": 0}),
    ]
    for i, (gid, g) in enumerate(guns):
        put("guns/" + gid, dict(g, acquired="", archived=False, order=i + 1))
    gmap = dict(guns)

    # Range visits over the last ~20 months
    places = [("Demo Range North", "Demo Shooting Club"), ("Demo Range South", "Demo Reservists"),
              ("Demo Indoor Range", "Demo Shooting Club")]
    n_visits = 0
    for k in range(34):
        ago = 600 - k * 17 - rnd.randint(0, 8)
        if ago < 2:
            continue
        place, org = rnd.choice(places)
        picks = rnd.sample(["g-t3x", "g-ar", "g-cz", "g-g17"], rnd.choice([1, 1, 2, 2, 3]))
        if place == "Demo Indoor Range":
            picks = [g for g in picks if g in ("g-cz", "g-g17")] or ["g-g17"]
        lines = [{"gunId": g, "gun": gmap[g]["name"], "type": gmap[g]["type"], "cal": gmap[g]["cal"],
                  "mode": gmap[g]["mode"], "rounds": rnd.choice([20, 30, 40, 50, 60, 100] if g in ("g-cz", "g-g17") else [10, 15, 20, 25, 30]),
                  "info": rnd.choice(["", "", "SRA", "PRS training"]) if g in ("g-t3x", "g-ar") else ""} for g in picks]
        h = rnd.choice([9, 10, 13, 17, 18])
        put(f"sessions/s-demo-{k:02d}", {"date": day(ago), "klo": f"{h}.00-{h + 2}.00", "place": place, "org": org,
                                         "lines": lines, "createdAt": day(ago) + "T12:00:00Z", "updatedAt": day(ago) + "T12:00:00Z"})
        n_visits += 1

    # Velocities: a charge ladder, the chosen charge across seasons (temperature effect), .22 LR and .223
    l65 = lambda ch: {"kind": "handload", "cal": "6.5 Creedmoor", "bullet_maker": "Lapua", "bullet_model": "Scenar",
                      "bullet_gr": 139, "brass": "Lapua", "powder": "N555", "charge_gr": ch}
    l223 = {"kind": "handload", "cal": ".223 Rem", "bullet_maker": "Sierra", "bullet_model": "MatchKing", "bullet_gr": 69,
            "brass": "Lapua", "powder": "N133", "charge_gr": 22.0}
    l22 = {"kind": "factory", "cal": ".22 LR", "maker": "SK", "product": "Long Range Match", "bullet_gr": 40}
    strings = []

    def string(ago, hour, gun, load, base, sd, n, temp):
        shots = [{"n": i + 1, "v_ms": round(rnd.gauss(base, sd), 1), "t": f"{hour:02d}:{10 + i:02d}:{rnd.randint(0, 59):02d}"} for i in range(n)]
        strings.append({"date": day(ago), "gun_id": gun, "load": load, "shots": shots, "temp_c": temp, "rh": rnd.randint(45, 85), "hpa": rnd.randint(995, 1025)})

    ladder_day = 140
    for j, ch in enumerate([43.0, 43.5, 44.0, 44.5, 45.0]):
        string(ladder_day, 11 + j, "g-t3x", l65(ch), 802 + (ch - 43.0) * 13.5, 2.8, 5, 14)
    for ago, temp in ((300, -6), (240, 3), (170, 11), (120, 19), (75, 24), (30, 8), (6, 1)):
        string(ago, 12, "g-t3x", l65(44.0), 815 + 0.62 * temp, 2.2, 8, temp)
    for ago, temp in ((200, 9), (90, 21), (12, 4)):
        string(ago, 14, "g-ar", l223, 868 + 0.4 * temp, 3.5, 10, temp)
    for ago, temp in ((260, 0), (110, 18), (20, 6)):
        string(ago, 10, "g-cz", l22, 322 + 0.35 * temp, 2.0, 10, temp)
    res = post("chrono/commit", {"strings": strings})
    loads = {l["name"]: l["id"] for l in _ok(c.get("/diary/api/chrono/loads"))}
    find = lambda part: next((lid for name, lid in loads.items() if part in name), "")

    # Scope zeroes: one scope moved between three rifles
    put("zero/scopes/s-demo", {"name": "Demo 5-25x56 FFP", "unit": "MRAD", "click": 0.1, "zero_stop": -0.5,
                               "elev_turn": 10, "wind_turn": 10, "notes": "## Demo notes\n\n- [x] Zero the 6.5 at 100 m\n- [ ] Check the .22 at 50 m",
                               "status": "", "ord": 0, "archived": False})
    zeros = [("z-t3x", "g-t3x", "6.5 Creedmoor", 100, find("N555 44.0"), 0.0, 0.0, "Picatinny 20 MOA"),
             ("z-ar", "g-ar", ".223 Rem", 100, find("MatchKing"), 1.6, -0.4, "Picatinny 0 MOA"),
             ("z-cz", "g-cz", ".22 LR", 50, find("Long Range Match"), 6.2, 0.3, "Picatinny 20 MOA")]
    for zid, gid, cal, dist, lid, el, wi, mount in zeros:
        put("zero/zeros/" + zid, {"scope_id": "s-demo", "rifle": gmap[gid]["name"], "gun_id": gid, "label": "", "load_id": lid,
                                  "cal": cal, "distance_m": dist, "mount": mount, "elev": el, "wind": wi, "date": day(160)})
    post("zero/zeros/z-ar/adjust", {"d_elev": 0.2, "d_wind": -0.1, "date": day(90), "note": "100 m, 5 shots", "op_id": "demo-adj"})
    post("zero/scopes/s-demo/mount", {"zero_id": "z-t3x", "date": day(30), "op_id": "demo-mount"})
    post("zero/zeros/z-t3x/check", {"ok": True, "date": day(6), "note": "cold bore on zero", "op_id": "demo-check"})

    # Guns loaned at a match
    put("loans/ln-demo-1", {"date": day(45), "event": "Demo club match", "note": "",
                            "lines": [{"who": "Friend A", "gunId": "g-ar", "gun": gmap["g-ar"]["name"], "cal": ".223 Rem", "rounds": 120, "price": 0.6},
                                      {"who": "Friend B", "gunId": None, "gun": "", "cal": "9 mm", "rounds": 100, "price": 0.3}],
                            "paid": ["Friend B"]})
    put("loans/ln-demo-2", {"date": day(10), "event": "Demo practice day", "note": "",
                            "lines": [{"who": "Friend A", "gunId": "g-t3x", "gun": gmap["g-t3x"]["name"], "cal": "6.5 Creedmoor", "rounds": 20, "price": 2.0}],
                            "paid": []})
    return f"Demo account reset: {n_visits} visits, {len(res['created'])} velocity strings, 3 zero positions, 2 loans."
