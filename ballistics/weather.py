"""Current conditions from the Finnish Meteorological Institute's open data (no API key), for the ballistic
calculator's "Fetch weather".

One WFS request for the latest observations of every station in a box around the shooter; each value
(temperature, humidity, pressure, wind) is taken from the nearest station that reports it, because many
stations measure temperature but not pressure. FMI's pressure is reduced to sea level (p_sea), so it is
converted back to station pressure with the range's altitude: the solver needs the pressure the bullet flies in.

Station wind is returned for reference only: 10 m up and kilometres away, it is not the wind on the range.
"""
import math
import threading
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

FMI_URL = "https://opendata.fmi.fi/wfs"
QUERY = "fmi::observations::weather::timevaluepair"
PARAMS = ("t2m", "rh", "p_sea", "ws_10min", "wd_10min")
BOX_DEG = (0.5, 1.0)               # ± lat, ± lon (≈ 55 × 50 km at 63 °N)
CACHE_SECONDS = 600
TIMEOUT = 8
_cache = {}
_lock = threading.Lock()


def _local(tag):
    return tag.rsplit("}", 1)[-1]


def _find_all(el, name):
    return [e for e in el.iter() if _local(e.tag) == name]


def parse(xml_bytes):
    """FMI timevaluepair XML → {station: {name, lat, lon, values: {param: (time, value)}}} with each parameter's
    newest non-missing value."""
    root = ET.fromstring(xml_bytes)
    stations = {}
    for member in _find_all(root, "member"):
        series = _find_all(member, "MeasurementTimeseries")
        if not series:
            continue
        sid = next((v for k, v in series[0].attrib.items() if _local(k) == "id"), "")
        param = sid.rsplit("-", 1)[-1]
        pos = next((e.text for e in _find_all(member, "pos") if e.text), None)
        if not pos:
            continue
        try:
            lat, lon = (float(x) for x in pos.split()[:2])
        except ValueError:
            continue
        name = None
        for e in _find_all(member, "name"):
            if (e.attrib.get("codeSpace") or "").endswith("/name") and e.text:
                name = e.text.strip()
                break
        key = (round(lat, 4), round(lon, 4))
        st = stations.setdefault(key, {"name": name or f"{lat:.2f}, {lon:.2f}", "lat": lat, "lon": lon, "values": {}})
        newest = None
        for tvp in _find_all(series[0], "MeasurementTVP"):
            t = next((e.text for e in tvp if _local(e.tag) == "time"), None)
            v = next((e.text for e in tvp if _local(e.tag) == "value"), None)
            try:
                f = float(v)
            except (TypeError, ValueError):
                continue
            if f != f:                                  # NaN: not measured
                continue
            if newest is None or (t or "") >= newest[0]:
                newest = (t or "", f)
        if newest:
            st["values"][param] = newest
    return list(stations.values())


def distance_km(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(a))


def station_pressure(hpa_sea, alt_m, temp_c=15.0):
    """Sea-level pressure → station pressure at alt_m (same formula as the solver's stationFromSeaLevel)."""
    t = (temp_c if temp_c is not None else 15.0) + 273.15 + 0.0065 * alt_m / 2
    return hpa_sea * math.exp(-9.80665 * alt_m / (287.058 * t))


def nearest(stations, lat, lon):
    """For each parameter, the nearest station that reports it."""
    out = {}
    for st in sorted(stations, key=lambda s: distance_km(lat, lon, s["lat"], s["lon"])):
        d = distance_km(lat, lon, st["lat"], st["lon"])
        for param, (t, v) in st["values"].items():
            out.setdefault(param, {"value": v, "time": t, "station": st["name"], "distance_km": round(d, 1)})
    return out


def _fetch(lat, lon):
    now = time.time()
    end = time.strftime("%Y-%m-%dT%H:%M:00Z", time.gmtime(now))
    start = time.strftime("%Y-%m-%dT%H:%M:00Z", time.gmtime(now - 3 * 3600))
    dlat, dlon = BOX_DEG
    q = {"service": "WFS", "version": "2.0.0", "request": "getFeature", "storedquery_id": QUERY,
         "bbox": f"{lon - dlon:.4f},{lat - dlat:.4f},{lon + dlon:.4f},{lat + dlat:.4f},epsg::4326",
         "parameters": ",".join(PARAMS), "starttime": start, "endtime": end, "timestep": "10"}
    req = urllib.request.Request(FMI_URL + "?" + urllib.parse.urlencode(q),
                                 headers={"User-Agent": "Shooting Diary ballistics (self-hosted)"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.read(8 * 1024 * 1024)


def current(lat, lon, alt_m=None, fetch=_fetch):
    """Conditions at (lat, lon): {temp_c, rh, hpa_sea, hpa_station?, wind_ms, wind_dir_deg, sources, fetched_at}.
    Raises OSError / ValueError when FMI can't be reached or answers nonsense."""
    key = (round(lat, 2), round(lon, 2))
    with _lock:
        hit = _cache.get(key)
    if hit and time.time() - hit[0] < CACHE_SECONDS:
        stations = hit[1]
    else:
        stations = parse(fetch(lat, lon))
        with _lock:
            _cache[key] = (time.time(), stations)
    near = nearest(stations, lat, lon)
    if not near:
        raise ValueError("no_stations")
    val = lambda p: near[p]["value"] if p in near else None  # noqa: E731
    out = {"temp_c": val("t2m"), "rh": val("rh"), "hpa_sea": val("p_sea"), "wind_ms": val("ws_10min"),
           "wind_dir_deg": val("wd_10min"), "sources": near, "alt_m": alt_m,
           "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    if out["hpa_sea"] is not None and alt_m is not None:
        out["hpa_station"] = round(station_pressure(out["hpa_sea"], alt_m, out["temp_c"]), 1)
    return out
