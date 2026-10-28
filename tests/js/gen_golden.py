"""Regenerate tests/js/golden.json from an independent solver (dev only, not a dependency of the app):

    pip install py-ballisticcalc==3.1.1
    python tests/js/gen_golden.py > tests/js/golden.json

py-ballisticcalc is LGPL; it is only run here to produce numbers. Its "Mil" is the NATO mil (1/6400 circle), so
angles are read in MRad. Its wind direction is "from", 0 = from behind; our clock face 6 = from behind. Inclined
shots are left out: it measures range horizontally, the solver uses slant (laser) range (covered by property tests).
"""
import json

import py_ballisticcalc
from py_ballisticcalc import *  # noqa: F401,F403


def reference(c):
    PreferredUnits.distance = Unit.Meter; PreferredUnits.velocity = Unit.MPS
    PreferredUnits.temperature = Unit.Celsius; PreferredUnits.pressure = Unit.hPa
    PreferredUnits.sight_height = Unit.Millimeter; PreferredUnits.drop = Unit.Meter
    tab = {"G1": TableG1, "G7": TableG7}[c["model"]]
    dm = DragModel(c["bc"], tab, Unit.Grain(c["weightGr"]), Unit.Millimeter(c["diameterMm"]), Unit.Millimeter(c["lengthMm"]))
    ammo = Ammo(dm, Unit.MPS(c["mv"]))
    gun = Weapon(sight_height=Unit.Millimeter(c["sightHeightMm"]), twist=0)
    za = Atmo(altitude=0, pressure=Unit.hPa(c["zero"]["hpa"]), temperature=Unit.Celsius(c["zero"]["tempC"]), humidity=c["zero"]["rh"])
    calc = Calculator()
    zshot = Shot(weapon=gun, ammo=ammo, atmo=za)
    calc.set_weapon_zero(zshot, Unit.Meter(c["zeroRangeM"]))
    a = c["atmo"]
    atmo = Atmo(altitude=0, pressure=Unit.hPa(a["hpa"]), temperature=Unit.Celsius(a["tempC"]), humidity=a["rh"])
    winds = [Wind(Unit.MPS(c["wind"]["ms"]), Unit.Degree((c["wind"]["clock"]*30-180)%360))] if c.get("wind") else []
    shot = Shot(weapon=gun, ammo=ammo, atmo=atmo, winds=winds, look_angle=Unit.Degree(c.get("angleDeg",0)))
    res = calc.fire(shot, trajectory_range=Unit.Meter(max(c["ranges"])+1), trajectory_step=Unit.Meter(c["step"]))
    out = []
    for r in c["ranges"]:
        p = res.get_at("distance", Unit.Meter(r)) if hasattr(res,"get_at") else None
        out.append({"range": r, "elev": -p.drop_angle.get_in(Unit.MRad), "wind": -p.windage_angle.get_in(Unit.MRad), "v": p.velocity.get_in(Unit.MPS), "tof": p.time})
    return out


STDZ = {"tempC": 15, "hpa": 1013.25, "rh": 0}
b65 = {"model": "G7", "bc": 0.305, "weightGr": 140, "diameterMm": 6.71, "lengthMm": 35.3}
b308 = {"model": "G7", "bc": 0.243, "weightGr": 175, "diameterMm": 7.82, "lengthMm": 31.2}
b308g1 = {"model": "G1", "bc": 0.475, "weightGr": 168, "diameterMm": 7.82, "lengthMm": 31.0}
b223 = {"model": "G1", "bc": 0.372, "weightGr": 77, "diameterMm": 5.70, "lengthMm": 25.0}
b338 = {"model": "G7", "bc": 0.381, "weightGr": 300, "diameterMm": 8.61, "lengthMm": 47.0}
R = [100, 200, 300, 400, 500, 600, 700, 800, 900, 1000]
cases = [
 ("6.5 CM G7, standard air", b65, 823, 50, 100, STDZ, STDZ, None, 0, R + [1100, 1200]),
 ("6.5 CM G7, cold -20 C", b65, 823, 50, 100, STDZ, {"tempC": -20, "hpa": 1013.25, "rh": 50}, None, 0, R),
 ("6.5 CM G7, hot +30 C humid", b65, 823, 50, 100, STDZ, {"tempC": 30, "hpa": 1013.25, "rh": 80}, None, 0, R),
 ("6.5 CM G7, 1000 m altitude", b65, 823, 50, 100, STDZ, {"tempC": 8.5, "hpa": 898.7, "rh": 30}, None, 0, R),
 ("6.5 CM G7, 300 m zero", b65, 823, 50, 300, STDZ, STDZ, None, 0, R),
 ("6.5 CM G7, wind 4 m/s from 9", b65, 823, 50, 100, STDZ, STDZ, {"ms": 4, "clock": 9}, 0, R),
 ("6.5 CM G7, wind 5 m/s from 2", b65, 823, 50, 100, STDZ, STDZ, {"ms": 5, "clock": 2}, 0, R),
 (".308 175 gr G7", b308, 790, 55, 100, STDZ, {"tempC": 5, "hpa": 1000, "rh": 60}, None, 0, R),
 (".308 168 gr G1", b308g1, 800, 55, 100, STDZ, STDZ, {"ms": 3, "clock": 3}, 0, R),
 (".223 77 gr G1", b223, 840, 60, 100, STDZ, STDZ, None, 0, R[:8]),
 (".338 LM 300 gr G7", b338, 830, 60, 100, STDZ, {"tempC": 0, "hpa": 990, "rh": 70}, None, 0, R + [1200, 1400, 1600]),
 ("zero in cold, fire warm", b65, 823, 50, 100, {"tempC": -10, "hpa": 1020, "rh": 40}, {"tempC": 25, "hpa": 1005, "rh": 40}, None, 0, R),
]
out = []
for name, b, mv, sh, zr, za, a, wind, ang, ranges in cases:
    c = dict(b, mv=mv, sightHeightMm=sh, zeroRangeM=zr, zero=za, atmo=a, ranges=ranges, step=1, angleDeg=ang)
    if wind: c["wind"] = wind
    exp = reference(c)
    out.append({"name": name, "input": {"bullet": b, "mv": mv, "sightHeightMm": sh, "zeroRangeM": zr, "zeroAtmo": za,
                "atmo": a, "wind": wind, "angleDeg": ang}, "expected": [
                {"range": e["range"], "elevMrad": round(e["elev"], 4), "windMrad": round(e["wind"], 4),
                 "v": round(e["v"], 2), "tof": round(e["tof"], 4)} for e in exp]})
print(json.dumps({"source": f"py-ballisticcalc {py_ballisticcalc.__version__} (LGPL, used only to generate these numbers; "
                  "not shipped). Spin drift and Coriolis off. Generator: tests/js/gen_golden.py",
                  "cases": out}, indent=1))
