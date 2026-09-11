import math
import os
import json
import time
import csv
import io
import heapq
import urllib.request
import urllib.parse
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, Response

app = FastAPI(title="FlyBrief Real-Route Engine", version="0.6.0")

# ============================================================
# КОНФИГ
# ============================================================
FLIGHTPLANDB_KEY = os.environ.get("FLIGHTPLANDB_KEY", "").strip()
NAVIGRAPH_CLIENT_ID = os.environ.get("NAVIGRAPH_CLIENT_ID", "").strip()
NAVIGRAPH_CLIENT_SECRET = os.environ.get("NAVIGRAPH_CLIENT_SECRET", "").strip()

# Кеши
AIRPORTS_CACHE = "airports_cache.json"
RUNWAYS_CACHE  = "runways_cache.json"
NAVAIDS_CACHE  = "navaids_cache.json"
AIRWAYS_CACHE  = "airways_cache.json"      # X-Plane earth_awy.dat + fixes
CACHE_MAX_AGE_SEC = 60 * 60 * 24 * 30

# OurAirports (CSV)
AIRPORTS_CSV_URL = "https://davidmegginson.github.io/ourairports-data/airports.csv"
RUNWAYS_CSV_URL  = "https://davidmegginson.github.io/ourairports-data/runways.csv"
NAVAIDS_CSV_URL  = "https://davidmegginson.github.io/ourairports-data/navaids.csv"

# X-Plane navdata (Laminar Research) — публичные зеркала
# Формат earth_awy.dat / earth_fix.dat / earth_nav.dat — стандартный X-Plane
XPLANE_SOURCES = [
    # github mirrors — они стабильнее, чем data.x-plane.com (который периодически недоступен)
    "https://raw.githubusercontent.com/akaflieg/airsim-navdata/master/",
    "https://raw.githubusercontent.com/navdata-xplane/navdata/master/",
]
XPLANE_FILES = ["earth_awy.dat", "earth_fix.dat", "earth_nav.dat"]

GRAPH_EDGE_MAX_NM = 250

PROFILES = {
    "B77W": {"name": "Boeing 777-300ER",   "speed": 490, "burn": 6800, "climb": 1800, "cont": 0.05, "hold": 3500},
    "B738": {"name": "Boeing 737-800",     "speed": 450, "burn": 2500, "climb":  800, "cont": 0.05, "hold": 1200},
    "A20N": {"name": "Airbus A320neo",     "speed": 440, "burn": 2200, "climb":  700, "cont": 0.05, "hold": 1100},
    "A21N": {"name": "Airbus A321neo",     "speed": 450, "burn": 2500, "climb":  800, "cont": 0.05, "hold": 1200},
    "A333": {"name": "Airbus A330-300",    "speed": 470, "burn": 5600, "climb": 1400, "cont": 0.05, "hold": 2800},
    "A359": {"name": "Airbus A350-900",    "speed": 480, "burn": 6000, "climb": 1500, "cont": 0.05, "hold": 3000},
    "B788": {"name": "Boeing 787-8",       "speed": 480, "burn": 5800, "climb": 1500, "cont": 0.05, "hold": 2900},
    "B789": {"name": "Boeing 787-9",       "speed": 485, "burn": 6100, "climb": 1500, "cont": 0.05, "hold": 3000},
    "B744": {"name": "Boeing 747-400",     "speed": 490, "burn": 9000, "climb": 2200, "cont": 0.05, "hold": 4000},
    "B748": {"name": "Boeing 747-8",       "speed": 495, "burn": 8800, "climb": 2200, "cont": 0.05, "hold": 4000},
    "E190": {"name": "Embraer E190",       "speed": 420, "burn": 1600, "climb":  500, "cont": 0.05, "hold":  800},
    "AT76": {"name": "ATR 72-600",         "speed": 275, "burn":  600, "climb":  250, "cont": 0.05, "hold":  400},
    "DH8D": {"name": "Bombardier Q400",    "speed": 360, "burn":  900, "climb":  300, "cont": 0.05, "hold":  500},
    "C172": {"name": "Cessna 172 Skyhawk", "speed": 110, "burn":   35, "climb":   15, "cont": 0.05, "hold":   20},
    "C25A": {"name": "Cessna Citation CJ2","speed": 380, "burn":  900, "climb":  400, "cont": 0.05, "hold":  600},
}

FALLBACK_DB = {
    "ULLI": {"name": "Pulkovo",          "lat": 59.8003, "lon":  30.2625, "country": "RU"},
    "UUEE": {"name": "Sheremetyevo",     "lat": 55.9726, "lon":  37.4146, "country": "RU"},
    "UUWW": {"name": "Vnukovo",          "lat": 55.5961, "lon":  37.2675, "country": "RU"},
    "UUDD": {"name": "Domodedovo",       "lat": 55.4088, "lon":  37.9061, "country": "RU"},
    "URSS": {"name": "Sochi",            "lat": 43.4499, "lon":  39.9566, "country": "RU"},
    "EDDF": {"name": "Frankfurt",        "lat": 50.0379, "lon":   8.5622, "country": "DE"},
    "EDDM": {"name": "Munich",           "lat": 48.3538, "lon":  11.7861, "country": "DE"},
    "EGLL": {"name": "London Heathrow",  "lat": 51.4700, "lon":  -0.4543, "country": "GB"},
    "LFPG": {"name": "Paris CDG",        "lat": 49.0097, "lon":   2.5479, "country": "FR"},
    "EHAM": {"name": "Amsterdam",        "lat": 52.3105, "lon":   4.7683, "country": "NL"},
    "OMDB": {"name": "Dubai Intl",       "lat": 25.2532, "lon":  55.3657, "country": "AE"},
    "KJFK": {"name": "New York JFK",     "lat": 40.6398, "lon": -73.7789, "country": "US"},
    "KLAX": {"name": "Los Angeles",      "lat": 33.9416, "lon":-118.4085, "country": "US"},
}

# ============================================================
# ХРАНИЛИЩА
# ============================================================
DB = {}            # ICAO -> {name, lat, lon, country}
RUNWAYS = {}       # ICAO -> [{id, hdg, length_ft, lat, lon}]
NAVAIDS = {}       # ident -> {name, type, lat, lon, country, freq_khz}

FIXES = {}         # X-Plane earth_fix.dat: ident -> {lat, lon}
AIRWAYS = {}       # X-Plane earth_awy.dat: {route_name: [(fix_a, fix_b, direction, ...)]}
AWY_GRAPH = {}     # fix_ident -> [(neighbor, dist_nm, airway_name)]

DB_LOADED_AT = 0.0
XPLANE_LOADED = False

# ============================================================
# КЕШ
# ============================================================
def _load_json_cache(path):
    try:
        if not os.path.exists(path):
            return None
        if time.time() - os.path.getmtime(path) > CACHE_MAX_AGE_SEC:
            return None
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and len(data) > 50:
            return data
    except Exception:
        pass
    return None


def _save_json_cache(path, data):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception:
        pass


def _download(url, timeout=90):
    req = urllib.request.Request(url, headers={"User-Agent": "FlyBrief/0.6"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="ignore")


# ============================================================
# ПАРСИНГ CSV (OurAirports)
# ============================================================
def _parse_airports_csv(text):
    result = {}
    reader = csv.DictReader(io.StringIO(text))
    for row in reader:
        try:
            ident = (row.get("ident") or "").strip().upper()
            if not ident or len(ident) < 3 or len(ident) > 5:
                continue
            atype = (row.get("type") or "").strip()
            if atype not in ("large_airport", "medium_airport", "small_airport"):
                continue
            scheduled = (row.get("scheduled_service") or "").strip().lower()
            if atype in ("medium_airport", "small_airport") and scheduled != "yes":
                continue
            lat = float(row.get("latitude_deg") or 0)
            lon = float(row.get("longitude_deg") or 0)
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                continue
            result[ident] = {
                "name": (row.get("name") or ident).strip(),
                "lat": round(lat, 6),
                "lon": round(lon, 6),
                "country": (row.get("iso_country") or "").strip().upper(),
                "type": atype,
                "iata": (row.get("iata_code") or "").strip().upper(),
            }
        except Exception:
            continue
    return result


def _parse_runways_csv(text, apt_set):
    result = {}
    reader = csv.DictReader(io.StringIO(text))
    for row in reader:
        try:
            apt = (row.get("airport_ident") or "").strip().upper()
            if apt not in apt_set:
                continue
            le = (row.get("le_ident") or "").strip()
            he = (row.get("he_ident") or "").strip()
            if not le:
                continue
            try:
                length_ft = int(float(row.get("length_ft") or 0))
            except Exception:
                length_ft = 0
            try:
                hdg_le = float(row.get("le_heading_degT") or 0)
            except Exception:
                hdg_le = None
            try:
                lat_le = float(row.get("le_latitude_deg") or 0)
                lon_le = float(row.get("le_longitude_deg") or 0)
            except Exception:
                lat_le = None
                lon_le = None
            entry = result.setdefault(apt, [])
            entry.append({"id": le, "hdg": round(hdg_le, 1) if hdg_le is not None else None,
                          "length_ft": length_ft, "lat": lat_le, "lon": lon_le})
            if he:
                hdg_he = (hdg_le + 180) % 360 if hdg_le is not None else None
                entry.append({"id": he, "hdg": round(hdg_he, 1) if hdg_he is not None else None,
                              "length_ft": length_ft, "lat": None, "lon": None})
        except Exception:
            continue
    for icao in result:
        result[icao] = sorted(result[icao], key=lambda r: -(r.get("length_ft") or 0))
    return result


def _parse_navaids_csv(text):
    result = {}
    reader = csv.DictReader(io.StringIO(text))
    for row in reader:
        try:
            ident = (row.get("ident") or "").strip().upper()
            if not ident or len(ident) > 5:
                continue
            ntype = (row.get("type") or "").strip().upper()
            if ntype not in ("VOR", "VOR-DME", "VORTAC", "TACAN", "NDB", "NDB-DME", "DME"):
                continue
            try:
                lat = float(row.get("latitude_deg") or 0)
                lon = float(row.get("longitude_deg") or 0)
            except Exception:
                continue
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                continue
            if ident in result:
                old_type = result[ident]["type"]
                if "VOR" not in ntype and "VOR" in old_type:
                    continue
            result[ident] = {
                "name": (row.get("name") or ident).strip(),
                "type": ntype,
                "lat": round(lat, 6),
                "lon": round(lon, 6),
                "country": (row.get("iso_country") or "").strip().upper(),
                "freq_khz": (row.get("frequency_khz") or "").strip(),
            }
        except Exception:
            continue
    return result


# ============================================================
# ПАРСИНГ X-PLANE navdata
# Формат earth_fix.dat:
#   lat lon ident  (пробелы)
# Формат earth_awy.dat:
#   fix_a lat_a lon_a fix_b lat_b lon_b airway_name direction base_fl top_fl
#   direction: 1=N->S (a->b), 2=S->N, 3=both
# ============================================================
def _parse_earth_fix(text):
    result = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("I") or line.startswith("A"):
            continue
        parts = line.split()
        if len(parts) < 3:
            continue
        try:
            lat = float(parts[0])
            lon = float(parts[1])
            ident = parts[2].upper()
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                continue
            result[ident] = {"lat": round(lat, 6), "lon": round(lon, 6)}
        except Exception:
            continue
    return result


def _parse_earth_awy(text):
    """Возвращает (airways_dict, ayw_graph_dict)."""
    airways = {}       # name -> [ {from_fix, to_fix, direction, base_fl, top_fl}, ... ]
    graph = {}         # fix -> [(neighbor, airway_name), ...]

    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("I") or line.startswith("A"):
            continue
        parts = line.split()
        if len(parts) < 10:
            continue
        try:
            fix_a = parts[0].upper()
            # parts[1], parts[2] — lat_a, lon_a (не используем, берём из FIXES)
            fix_b = parts[3].upper()
            # parts[4], parts[5] — lat_b, lon_b
            awy_name = parts[6].upper()
            direction = int(parts[7])
            base_fl = int(parts[8])
            top_fl = int(parts[9])
        except Exception:
            continue

        airways.setdefault(awy_name, []).append({
            "from": fix_a, "to": fix_b,
            "dir": direction, "base_fl": base_fl, "top_fl": top_fl,
        })

        # direction: 1 = a->b, 2 = b->a, 3 = both
        if direction in (1, 3):
            graph.setdefault(fix_a, []).append((fix_b, awy_name))
        if direction in (2, 3):
            graph.setdefault(fix_b, []).append((fix_a, awy_name))

    return airways, graph


# ============================================================
# ЗАГРУЗКА
# ============================================================
def load_ourairports():
    global DB, RUNWAYS, NAVAIDS

    cached = _load_json_cache(AIRPORTS_CACHE)
    if cached:
        DB = cached
        print(f"[FlyBrief] airports cache: {len(DB)}")
    else:
        try:
            print("[FlyBrief] downloading airports...")
            DB = _parse_airports_csv(_download(AIRPORTS_CSV_URL))
            _save_json_cache(AIRPORTS_CACHE, DB)
            print(f"[FlyBrief] airports: {len(DB)}")
        except Exception as e:
            print(f"[FlyBrief] airports fail: {e}")
            DB = dict(FALLBACK_DB)

    cached = _load_json_cache(RUNWAYS_CACHE)
    if cached:
        RUNWAYS = cached
        print(f"[FlyBrief] runways cache: {len(RUNWAYS)}")
    else:
        try:
            print("[FlyBrief] downloading runways...")
            RUNWAYS = _parse_runways_csv(_download(RUNWAYS_CSV_URL), set(DB.keys()))
            _save_json_cache(RUNWAYS_CACHE, RUNWAYS)
            print(f"[FlyBrief] runways: {len(RUNWAYS)}")
        except Exception as e:
            print(f"[FlyBrief] runways fail: {e}")

    cached = _load_json_cache(NAVAIDS_CACHE)
    if cached:
        NAVAIDS = cached
        print(f"[FlyBrief] navaids cache: {len(NAVAIDS)}")
    else:
        try:
            print("[FlyBrief] downloading navaids...")
            NAVAIDS = _parse_navaids_csv(_download(NAVAIDS_CSV_URL))
            _save_json_cache(NAVAIDS_CACHE, NAVAIDS)
            print(f"[FlyBrief] navaids: {len(NAVAIDS)}")
        except Exception as e:
            print(f"[FlyBrief] navaids fail: {e}")


def load_xplane_navdata():
    """Скачивает earth_awy.dat, earth_fix.dat, earth_nav.dat с GitHub-зеркал.
    Если хоть один файл недоступен — X-Plane слой отключается, работаем на A* по navaids."""
    global FIXES, AIRWAYS, AWY_GRAPH, XPLANE_LOADED

    cached = _load_json_cache(AIRWAYS_CACHE)
    if cached and cached.get("fixes") and cached.get("airways"):
        FIXES = cached["fixes"]
        AIRWAYS = cached["airways"]
        # Перестроить граф из закешированных airways
        AWY_GRAPH = {}
        for awy_name, segments in AIRWAYS.items():
            for seg in segments:
                d = seg.get("dir", 3)
                a, b = seg["from"], seg["to"]
                if d in (1, 3):
                    AWY_GRAPH.setdefault(a, []).append((b, awy_name))
                if d in (2, 3):
                    AWY_GRAPH.setdefault(b, []).append((a, awy_name))
        XPLANE_LOADED = True
        print(f"[FlyBrief] XPlane cache: {len(FIXES)} fixes, {len(AIRWAYS)} airways, "
              f"{len(AWY_GRAPH)} nodes")
        return

    fix_text = None
    awy_text = None

    for base in XPLANE_SOURCES:
        try:
            print(f"[FlyBrief] trying XPlane mirror: {base}")
            fix_text = _download(base + "earth_fix.dat", timeout=60)
            awy_text = _download(base + "earth_awy.dat", timeout=60)
            if fix_text and awy_text:
                break
        except Exception as e:
            print(f"[FlyBrief] mirror {base} fail: {e}")
            fix_text = None
            awy_text = None

    if not fix_text or not awy_text:
        print("[FlyBrief] XPlane navdata unavailable — fallback to navaids A*")
        XPLANE_LOADED = False
        return

    try:
        FIXES = _parse_earth_fix(fix_text)
        AIRWAYS, AWY_GRAPH = _parse_earth_awy(awy_text)
        XPLANE_LOADED = True
        print(f"[FlyBrief] XPlane loaded: {len(FIXES)} fixes, {len(AIRWAYS)} airways, "
              f"{len(AWY_GRAPH)} graph nodes")

        # кеш только fixes + airways (граф перестраивается при загрузке — быстро)
        _save_json_cache(AIRWAYS_CACHE, {"fixes": FIXES, "airways": AIRWAYS})
    except Exception as e:
        print(f"[FlyBrief] XPlane parse fail: {e}")
        XPLANE_LOADED = False


def build_navaid_graph():
    """Fallback-граф: navaids, соединённые если расстояние ≤ GRAPH_EDGE_MAX_NM.
    Используется когда X-Plane airways недоступны."""
    global AWY_GRAPH
    if XPLANE_LOADED and AWY_GRAPH:
        return  # уже есть граф из airways

    print("[FlyBrief] building fallback navaid graph...")
    useful = ("VOR", "VOR-DME", "VORTAC", "TACAN", "NDB-DME", "NDB")
    items = [(ident, nav) for ident, nav in NAVAIDS.items() if nav["type"] in useful]
    n = len(items)
    gr = {}
    for i in range(n):
        ident_i, nav_i = items[i]
        for j in range(i + 1, n):
            ident_j, nav_j = items[j]
            if abs(nav_i["lat"] - nav_j["lat"]) > 5 or abs(nav_i["lon"] - nav_j["lon"]) > 5:
                continue
            d = dist(nav_i["lat"], nav_i["lon"], nav_j["lat"], nav_j["lon"])
            if d <= GRAPH_EDGE_MAX_NM:
                gr.setdefault(ident_i, []).append((ident_j, "DCT"))
                gr.setdefault(ident_j, []).append((ident_i, "DCT"))
    AWY_GRAPH = gr
    print(f"[FlyBrief] fallback graph: {len(gr)} nodes")


# ============================================================
# ГЕОМЕТРИЯ
# ============================================================
def dist(lat1, lon1, lat2, lon2):
    R = 3440.065
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def bearing(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def move_point(lat, lon, brg, dist_nm):
    R = 3440.065
    p1, l1 = math.radians(lat), math.radians(lon)
    b = math.radians(brg)
    d = dist_nm / R
    p2 = math.asin(math.sin(p1) * math.cos(d) + math.cos(p1) * math.sin(d) * math.cos(b))
    l2 = l1 + math.atan2(math.sin(b) * math.sin(d) * math.cos(p1),
                         math.cos(d) - math.sin(p1) * math.sin(p2))
    return math.degrees(p2), ((math.degrees(l2) + 540) % 360) - 180


def gc_interpolate(lat1, lon1, lat2, lon2, step_nm=200):
    p1, l1 = math.radians(lat1), math.radians(lon1)
    p2, l2 = math.radians(lat2), math.radians(lon2)
    d = 2 * math.asin(math.sqrt(
        math.sin((p2 - p1) / 2) ** 2 +
        math.cos(p1) * math.cos(p2) * math.sin((l2 - l1) / 2) ** 2))
    total_nm = d * 3440.065
    if total_nm < step_nm:
        return []
    n = max(1, int(total_nm // step_nm))
    out = []
    for i in range(1, n):
        f = i / n
        a = math.sin((1 - f) * d) / math.sin(d) if math.sin(d) != 0 else 0
        b = math.sin(f * d) / math.sin(d) if math.sin(d) != 0 else 0
        x = a * math.cos(p1) * math.cos(l1) + b * math.cos(p2) * math.cos(l2)
        y = a * math.cos(p1) * math.sin(l1) + b * math.cos(p2) * math.sin(l2)
        z = a * math.sin(p1) + b * math.sin(p2)
        lat = math.degrees(math.atan2(z, math.sqrt(x * x + y * y)))
        lon = math.degrees(math.atan2(y, x))
        out.append((lat, lon))
    return out


# ============================================================
# ЗАГРУЗКА ВСЕГО
# ============================================================
load_ourairports()
load_xplane_navdata()
build_navaid_graph()

DB_LOADED_AT = time.time()


# ============================================================
# A* ПО AIRWAYS / GRAPH
# ============================================================
def _all_points():
    """Единый словарь ident -> {lat, lon} для A* (fixes + navaids)."""
    pts = {}
    for ident, fx in FIXES.items():
        pts[ident] = {"lat": fx["lat"], "lon": fx["lon"]}
    for ident, nav in NAVAIDS.items():
        if ident not in pts:
            pts[ident] = {"lat": nav["lat"], "lon": nav["lon"]}
    return pts


ALL_PTS = _all_points()


def _find_nearest_pt(lat, lon, max_nm=80):
    """Ищет ближайшую точку в ALL_PTS, которая есть в графе AWY_GRAPH."""
    best, best_d = None, 999999.0
    for ident in AWY_GRAPH.keys():
        p = ALL_PTS.get(ident)
        if not p:
            continue
        d = dist(lat, lon, p["lat"], p["lon"])
        if d < best_d:
            best_d, best = d, ident
    return best if best_d <= max_nm else None


def astar_graph(dep_icao, arr_icao):
    """A* по AWY_GRAPH (airways или navaid-fallback).
    Возвращает (points, uses_airways) или (None, False)."""
    if dep_icao not in DB or arr_icao not in DB:
        return None, False
    if not AWY_GRAPH:
        return None, False

    dep = DB[dep_icao]
    arr = DB[arr_icao]

    start = _find_nearest_pt(dep["lat"], dep["lon"], max_nm=120)
    goal = _find_nearest_pt(arr["lat"], arr["lon"], max_nm=120)
    if not start or not goal or start == goal:
        return None, False

    open_set = [(0.0, start)]
    came_from = {}
    came_awy = {}
    g_score = {start: 0.0}
    visited = set()
    iterations = 0
    max_iter = 25000

    goal_pt = ALL_PTS[goal]

    while open_set:
        iterations += 1
        if iterations > max_iter:
            return None, False

        _, current = heapq.heappop(open_set)
        if current in visited:
            continue
        visited.add(current)

        if current == goal:
            # восстановить путь
            path = [current]
            awys = []
            cur = current
            while cur in came_from:
                prev = came_from[cur]
                path.append(prev)
                awys.append(came_awy.get(cur, "DCT"))
                cur = prev
            path.reverse()
            awys.reverse()

            # построить points
            pts = [{"id": dep_icao, "lat": dep["lat"], "lon": dep["lon"]}]
            used_awy = False
            for idx, ident in enumerate(path):
                p = ALL_PTS.get(ident)
                if not p:
                    continue
                pts.append({"id": ident, "lat": p["lat"], "lon": p["lon"]})
                if idx < len(awys) and awys[idx] != "DCT":
                    used_awy = True
            pts.append({"id": arr_icao, "lat": arr["lat"], "lon": arr["lon"]})
            return pts, used_awy

        cur_pt = ALL_PTS[current]
        for neighbor, awy_name in AWY_GRAPH.get(current, []):
            if neighbor in visited:
                continue
            nb_pt = ALL_PTS.get(neighbor)
            if not nb_pt:
                continue
            d = dist(cur_pt["lat"], cur_pt["lon"], nb_pt["lat"], nb_pt["lon"])
            tentative = g_score[current] + d
            if tentative < g_score.get(neighbor, 1e18):
                came_from[neighbor] = current
                came_awy[neighbor] = awy_name
                g_score[neighbor] = tentative
                h = dist(nb_pt["lat"], nb_pt["lon"], goal_pt["lat"], goal_pt["lon"])
                heapq.heappush(open_set, (tentative + h, neighbor))

    return None, False


# ============================================================
# NAVIGRAPH API (опционально — если ключи в env)
# ============================================================
_nav_token = {"token": None, "expires_at": 0}


def _nav_get_token():
    if not NAVIGRAPH_CLIENT_ID or not NAVIGRAPH_CLIENT_SECRET:
        return None
    now = time.time()
    if _nav_token["token"] and now < _nav_token["expires_at"] - 60:
        return _nav_token["token"]
    try:
        data = urllib.parse.urlencode({
            "grant_type": "client_credentials",
            "client_id": NAVIGRAPH_CLIENT_ID,
            "client_secret": NAVIGRAPH_CLIENT_SECRET,
        }).encode()
        req = urllib.request.Request(
            "https://identity.api.navigraph.com/connect/token",
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"})
        with urllib.request.urlopen(req, timeout=10) as r:
            resp = json.loads(r.read().decode("utf-8"))
        _nav_token["token"] = resp.get("access_token")
        _nav_token["expires_at"] = now + int(resp.get("expires_in", 3600))
        return _nav_token["token"]
    except Exception as e:
        print(f"[Navigraph] token error: {e}")
        return None


def navigraph_route(dep, arr):
    token = _nav_get_token()
    if not token:
        return None
    try:
        params = urllib.parse.urlencode({
            "origin": dep, "destination": arr,
            "aircraft": "B738", "cruiseAltitude": 35000,
        })
        url = f"https://api.navigraph.com/v1/routes/generate?{params}"
        req = urllib.request.Request(url, headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "User-Agent": "FlyBrief/0.6",
        })
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read().decode("utf-8"))
        nodes = data.get("waypoints") or data.get("route", {}).get("nodes") or []
        pts = []
        for n in nodes:
            if n.get("lat") is None or n.get("lon") is None:
                continue
            pts.append({
                "id": n.get("identifier") or n.get("ident") or "WPT",
                "lat": float(n["lat"]), "lon": float(n["lon"]),
            })
        if len(pts) >= 3:
            return pts
    except Exception as e:
        print(f"[Navigraph] route error: {e}")
    return None


# ============================================================
# MANUAL ROUTES
# ============================================================
MANUAL_ROUTES = {
    ("ULLI", "UUWW"): [
        {"id": "ULLI",  "lat": 59.8003, "lon": 30.2625},
        {"id": "LED",   "lat": 59.8010, "lon": 30.3010},
        {"id": "KOTAM", "lat": 59.1230, "lon": 31.9540},
        {"id": "LUKOR", "lat": 58.4410, "lon": 33.2120},
        {"id": "GEKLA", "lat": 57.6540, "lon": 34.5010},
        {"id": "SUGOL", "lat": 56.8820, "lon": 35.8110},
        {"id": "UWPS",  "lat": 53.1110, "lon": 45.0190},
        {"id": "UUWW",  "lat": 55.5961, "lon": 37.2675},
    ],
    ("KJFK", "OMDB"): [
        {"id": "KJFK",  "lat": 40.6398, "lon": -73.7789},
        {"id": "COATE", "lat": 42.4430, "lon": -71.1210},
        {"id": "ALLEX", "lat": 46.1200, "lon": -60.4000},
        {"id": "BIKF",  "lat": 63.9850, "lon": -22.6056},
        {"id": "GURLU", "lat": 56.3210, "lon":  10.1200},
        {"id": "ODILO", "lat": 48.1200, "lon":  16.3400},
        {"id": "SITAN", "lat": 34.2100, "lon":  43.1200},
        {"id": "OMDB",  "lat": 25.2532, "lon":  55.3657},
    ],
    ("EDDF", "EDDM"): [
        {"id": "EDDF",  "lat": 50.0379, "lon":  8.5622},
        {"id": "MARUN", "lat": 49.7500, "lon":  9.0000},
        {"id": "MOGTI", "lat": 49.2500, "lon":  9.9000},
        {"id": "AKANU", "lat": 48.8500, "lon": 10.7000},
        {"id": "ROKIL", "lat": 48.5000, "lon": 11.3000},
        {"id": "EDDM",  "lat": 48.3538, "lon": 11.7861},
    ],
    ("EGLL", "LFPG"): [
        {"id": "EGLL",  "lat": 51.4700, "lon": -0.4543},
        {"id": "MID",   "lat": 51.0500, "lon": -0.6300},
        {"id": "DVR",   "lat": 51.1600, "lon":  1.3600},
        {"id": "SFD",   "lat": 50.7000, "lon":  2.1000},
        {"id": "LFPG",  "lat": 49.0097, "lon":  2.5479},
    ],
    ("KJFK", "KLAX"): [
        {"id": "KJFK",  "lat": 40.6398, "lon": -73.7789},
        {"id": "DPKAY", "lat": 41.0000, "lon": -78.0000},
        {"id": "JOT",   "lat": 41.5460, "lon": -88.3180},
        {"id": "DBL",   "lat": 39.4400, "lon":-107.0000},
        {"id": "HEC",   "lat": 34.7970, "lon":-115.1770},
        {"id": "KLAX",  "lat": 33.9416, "lon":-118.4085},
    ],
}


def fetch_fpdb_route(dep, arr):
    if not FLIGHTPLANDB_KEY:
        return None
    try:
        url = f"https://api.flightplandatabase.com/search/plan?fromICAO={dep}&toICAO={arr}&limit=1"
        req = urllib.request.Request(url, headers={
            "User-Agent": "FlyBrief/0.6",
            "X-API-Key": FLIGHTPLANDB_KEY,
            "Accept": "application/json",
        })
        with urllib.request.urlopen(req, timeout=6) as r:
            data = json.loads(r.read().decode("utf-8"))
        if not isinstance(data, list) or not data:
            return None
        nodes = data[0].get("route", {}).get("nodes") or []
        pts = []
        for n in nodes:
            if n.get("lat") is None or n.get("lon") is None:
                continue
            pts.append({"id": n.get("ident") or n.get("name") or "WPT",
                        "lat": float(n["lat"]), "lon": float(n["lon"])})
        if len(pts) >= 3:
            return pts
    except Exception as e:
        print(f"[FlyBrief] FPDB error: {e}")
    return None


# ============================================================
# SID/STAR
# ============================================================
SID_STAR = {
    "EDDF": {
        "SID": {
            "18":  [{"id": "MARUN", "lat": 49.7500, "lon": 9.0000},
                    {"id": "MOGTI", "lat": 49.2500, "lon": 9.9000}],
            "25C": [{"id": "MARUN", "lat": 49.7500, "lon": 9.0000}],
        },
        "STAR": {
            "18":  [{"id": "AKANU", "lat": 48.8500, "lon": 10.7000},
                    {"id": "ROKIL", "lat": 48.5000, "lon": 11.3000}],
            "25C": [{"id": "AKANU", "lat": 48.8500, "lon": 10.7000}],
        },
    },
    "EDDM": {
        "SID": {
            "26L": [{"id": "AKANU", "lat": 48.8500, "lon": 10.7000}],
            "08R": [{"id": "MOGTI", "lat": 49.2500, "lon": 9.9000}],
        },
        "STAR": {
            "26L": [{"id": "ROKIL", "lat": 48.5000, "lon": 11.3000}],
            "08R": [{"id": "MARUN", "lat": 49.7500, "lon": 9.0000}],
        },
    },
    "EGLL": {
        "SID": {
            "27R": [{"id": "MID", "lat": 51.0500, "lon": -0.6300}],
            "09L": [{"id": "DVR", "lat": 51.1600, "lon": 1.3600}],
        },
        "STAR": {
            "27R": [{"id": "SFD", "lat": 50.7000, "lon": 2.1000}],
            "09L": [{"id": "SFD", "lat": 50.7000, "lon": 2.1000}],
        },
    },
    "LFPG": {
        "SID": {
            "27L": [{"id": "SFD", "lat": 50.7000, "lon": 2.1000}],
            "09R": [{"id": "MID", "lat": 51.0500, "lon": -0.6300}],
        },
        "STAR": {
            "27L": [{"id": "DVR", "lat": 51.1600, "lon": 1.3600}],
            "09R": [{"id": "DVR", "lat": 51.1600, "lon": 1.3600}],
        },
    },
    "EHAM": {
        "SID": {
            "24":  [{"id": "ANDIK", "lat": 52.5000, "lon": 4.9000}],
            "18C": [{"id": "LEKKO", "lat": 52.2000, "lon": 4.5000}],
        },
        "STAR": {
            "24":  [{"id": "SULUT", "lat": 52.0000, "lon": 4.5000}],
            "18C": [{"id": "ARTIP", "lat": 52.6000, "lon": 4.7000}],
        },
    },
    "OMDB": {
        "SID": {
            "30R": [{"id": "DESDI", "lat": 25.1000, "lon": 55.5000}],
            "12L": [{"id": "IMBUK", "lat": 25.4000, "lon": 55.2000}],
        },
        "STAR": {
            "30R": [{"id": "REREK", "lat": 25.5000, "lon": 55.8000}],
            "12L": [{"id": "LAVAN", "lat": 24.9000, "lon": 55.0000}],
        },
    },
    "KJFK": {
        "SID": {
            "31L": [{"id": "COATE", "lat": 42.4430, "lon": -71.1210}],
            "04R": [{"id": "BETTE", "lat": 40.5000, "lon": -72.5000}],
        },
        "STAR": {
            "31L": [{"id": "ALLEX", "lat": 46.1200, "lon": -60.4000}],
            "04R": [{"id": "ALLEX", "lat": 46.1200, "lon": -60.4000}],
        },
    },
    "KLAX": {
        "SID": {
            "25R": [{"id": "HEC", "lat": 34.7970, "lon": -115.1770}],
            "24L": [{"id": "HEC", "lat": 34.7970, "lon": -115.1770}],
        },
        "STAR": {
            "25R": [{"id": "JOT", "lat": 41.5460, "lon": -88.3180}],
            "24L": [{"id": "JOT", "lat": 41.5460, "lon": -88.3180}],
        },
    },
    "ULLI": {
        "SID": {
            "10R": [{"id": "LED", "lat": 59.8010, "lon": 30.3010},
                    {"id": "KOTAM", "lat": 59.1230, "lon": 31.9540}],
            "28L": [{"id": "LUKOR", "lat": 58.4410, "lon": 33.2120},
                    {"id": "GEKLA", "lat": 57.6540, "lon": 34.5010}],
        },
        "STAR": {
            "10R": [{"id": "SUGOL", "lat": 56.8820, "lon": 35.8110},
                    {"id": "UWPS", "lat": 53.1110, "lon": 45.0190}],
            "28L": [{"id": "UWPS", "lat": 53.1110, "lon": 45.0190}],
        },
    },
    "UUEE": {
        "SID": {
            "24C": [{"id": "BP", "lat": 55.9000, "lon": 37.3000}],
            "06C": [{"id": "KN", "lat": 56.1000, "lon": 37.6000}],
        },
        "STAR": {
            "24C": [{"id": "SW", "lat": 55.7000, "lon": 37.2000}],
            "06C": [{"id": "SW", "lat": 55.7000, "lon": 37.2000}],
        },
    },
    "UUWW": {
        "SID": {
            "06": [{"id": "VKO", "lat": 55.5961, "lon": 37.2675}],
            "24": [{"id": "VKO", "lat": 55.5961, "lon": 37.2675}],
        },
        "STAR": {
            "06": [{"id": "VKO", "lat": 55.5961, "lon": 37.2675}],
            "24": [{"id": "VKO", "lat": 55.5961, "lon": 37.2675}],
        },
    },
    "UUDD": {
        "SID": {
            "14L": [{"id": "DME", "lat": 55.4088, "lon": 37.9061}],
            "32R": [{"id": "DME", "lat": 55.4088, "lon": 37.9061}],
        },
        "STAR": {
            "14L": [{"id": "DME", "lat": 55.4088, "lon": 37.9061}],
            "32R": [{"id": "DME", "lat": 55.4088, "lon": 37.9061}],
        },
    },
    "URSS": {
        "SID": {
            "06": [{"id": "AER", "lat": 43.4499, "lon": 39.9566}],
            "24": [{"id": "AER", "lat": 43.4499, "lon": 39.9566}],
        },
        "STAR": {
            "06": [{"id": "AER", "lat": 43.4499, "lon": 39.9566}],
            "24": [{"id": "AER", "lat": 43.4499, "lon": 39.9566}],
        },
    },
    "WSSS": {
        "SID": {
            "02L": [{"id": "SIN", "lat": 1.3644, "lon": 103.9915}],
            "20R": [{"id": "SIN", "lat": 1.3644, "lon": 103.9915}],
        },
        "STAR": {
            "02L": [{"id": "SIN", "lat": 1.3644, "lon": 103.9915}],
            "20R": [{"id": "SIN", "lat": 1.3644, "lon": 103.9915}],
        },
    },
    "VHHH": {
        "SID": {
            "07R": [{"id": "HKG", "lat": 22.3080, "lon": 113.9185}],
            "25L": [{"id": "HKG", "lat": 22.3080, "lon": 113.9185}],
        },
        "STAR": {
            "07R": [{"id": "HKG", "lat": 22.3080, "lon": 113.9185}],
            "25L": [{"id": "HKG", "lat": 22.3080, "lon": 113.9185}],
        },
    },
    "RJTT": {
        "SID": {
            "16R": [{"id": "HND", "lat": 35.5494, "lon": 139.7798}],
            "34L": [{"id": "HND", "lat": 35.5494, "lon": 139.7798}],
        },
        "STAR": {
            "16R": [{"id": "HND", "lat": 35.5494, "lon": 139.7798}],
            "34L": [{"id": "HND", "lat": 35.5494, "lon": 139.7798}],
        },
    },
    "ZBAA": {
        "SID": {
            "18L": [{"id": "PEK", "lat": 40.0801, "lon": 116.5846}],
            "36R": [{"id": "PEK", "lat": 40.0801, "lon": 116.5846}],
        },
        "STAR": {
            "18L": [{"id": "PEK", "lat": 40.0801, "lon": 116.5846}],
            "36R": [{"id": "PEK", "lat": 40.0801, "lon": 116.5846}],
        },
    },
}


def _find_runway(icao, rwy_id):
    if icao not in RUNWAYS:
        return None
    for r in RUNWAYS[icao]:
        if (r.get("id") or "").upper() == rwy_id.upper():
            return r
    return None


def _pick_default_runway(icao, target_lat, target_lon):
    if icao not in RUNWAYS or not RUNWAYS[icao]:
        return None
    apt = DB[icao]
    brg = bearing(apt["lat"], apt["lon"], target_lat, target_lon)
    best, best_diff = None, 999
    for r in RUNWAYS[icao]:
        hdg = r.get("hdg")
        if hdg is None:
            continue
        diff = abs(((hdg - brg + 180) % 360) - 180)
        if diff < best_diff:
            best_diff, best = diff, r
    return best


def generate_sid_generic(icao, rwy_id, next_lat, next_lon, step_nm=20):
    apt = DB[icao]
    brg = bearing(apt["lat"], apt["lon"], next_lat, next_lon)
    pts = []
    for i, d in enumerate([step_nm, step_nm * 2], 1):
        la, lo = move_point(apt["lat"], apt["lon"], brg, d)
        pts.append({"id": f"SID{i:02d}", "lat": round(la, 4), "lon": round(lo, 4)})
    return pts


def generate_star_generic(icao, rwy_id, prev_lat, prev_lon, step_nm=20):
    apt = DB[icao]
    brg = bearing(prev_lat, prev_lon, apt["lat"], apt["lon"])
    pts = []
    for i, d in enumerate([step_nm * 2, step_nm], 1):
        la, lo = move_point(apt["lat"], apt["lon"], (brg + 180) % 360, d)
        pts.append({"id": f"STAR{i:02d}", "lat": round(la, 4), "lon": round(lo, 4)})
    pts.reverse()
    return pts


def build_sid(icao, rwy_id, next_lat, next_lon):
    entry = SID_STAR.get(icao)
    if entry and rwy_id:
        pts = (entry.get("SID") or {}).get(rwy_id.upper())
        if pts:
            return [dict(p) for p in pts]
    return generate_sid_generic(icao, rwy_id, next_lat, next_lon)


def build_star(icao, rwy_id, prev_lat, prev_lon):
    entry = SID_STAR.get(icao)
    if entry and rwy_id:
        pts = (entry.get("STAR") or {}).get(rwy_id.upper())
        if pts:
            return [dict(p) for p in pts]
    return generate_star_generic(icao, rwy_id, prev_lat, prev_lon)


# ============================================================
# ВЫБОР МАРШРУТА
# ============================================================
def get_route(dep, arr, dep_rwy=None, arr_rwy=None):
    dep_rwy_used, arr_rwy_used = dep_rwy, arr_rwy
    if not dep_rwy_used:
        r = _pick_default_runway(dep, DB[arr]["lat"], DB[arr]["lon"])
        dep_rwy_used = r["id"] if r else None
    if not arr_rwy_used:
        r = _pick_default_runway(arr, DB[dep]["lat"], DB[dep]["lon"])
        arr_rwy_used = r["id"] if r else None

    key = (dep, arr)
    body = None
    source = None

    if key in MANUAL_ROUTES:
        body = [dict(p) for p in MANUAL_ROUTES[key]]
        source = "manual"
    else:
        nav = navigraph_route(dep, arr)
        if nav:
            body, source = nav, "navigraph"
        else:
            fpdb = fetch_fpdb_route(dep, arr)
            if fpdb:
                body, source = fpdb, "fpdb"
            else:
                astar_pts, used_awy = astar_graph(dep, arr)
                if astar_pts:
                    body = astar_pts
                    source = "airways" if used_awy else "astar"
                else:
                    p_dep, p_arr = DB[dep], DB[arr]
                    mid = gc_interpolate(p_dep["lat"], p_dep["lon"],
                                         p_arr["lat"], p_arr["lon"], step_nm=200)
                    body = [{"id": dep, "lat": p_dep["lat"], "lon": p_dep["lon"]}]
                    for i, (la, lo) in enumerate(mid, 1):
                        body.append({"id": f"DCT{i:02d}",
                                     "lat": round(la, 4), "lon": round(lo, 4)})
                    body.append({"id": arr, "lat": p_arr["lat"], "lon": p_arr["lon"]})
                    source = "gc"

    first_body = body[0] if body else {"lat": DB[dep]["lat"], "lon": DB[dep]["lon"]}
    last_body = body[-1] if body else {"lat": DB[arr]["lat"], "lon": DB[arr]["lon"]}

    sid_pts = build_sid(dep, dep_rwy_used, first_body["lat"], first_body["lon"])
    star_pts = build_star(arr, arr_rwy_used, last_body["lat"], last_body["lon"])

    final = []
    final.append({"id": dep, "lat": DB[dep]["lat"], "lon": DB[dep]["lon"]})
    final.extend(sid_pts)
    for pt in body:
        if pt["id"] in (dep, arr):
            continue
        final.append(pt)
    final.extend(star_pts)
    final.append({"id": arr, "lat": DB[arr]["lat"], "lon": DB[arr]["lon"]})

    return final, source, dep_rwy_used, arr_rwy_used


def find_automatic_alternate(arr_icao):
    if arr_icao not in DB:
        return arr_icao
    arr_apt = DB[arr_icao]
    best_alt, min_d = None, 999999.0
    for icao, apt in DB.items():
        if icao == arr_icao:
            continue
        d = dist(arr_apt["lat"], arr_apt["lon"], apt["lat"], apt["lon"])
        if d < min_d and d < 400:
            min_d, best_alt = d, icao
    return best_alt if best_alt else arr_icao


# ============================================================
# METAR
# ============================================================
_metar_cache = {}
_METAR_TTL = 60 * 15


def get_metar(icao):
    now = time.time()
    c = _metar_cache.get(icao)
    if c and now - c[0] < _METAR_TTL:
        return c[1]
    try:
        url = f"https://aviationweather.gov/api/data/metar?ids={icao.upper()}&format=json"
        req = urllib.request.Request(url, headers={"User-Agent": "FlyBrief/0.6"})
        with urllib.request.urlopen(req, timeout=6) as response:
            data = json.loads(response.read().decode("utf-8"))
            if isinstance(data, list) and len(data) > 0:
                raw = data[0].get("rawOb", f"{icao} NO METAR")
                _metar_cache[icao] = (now, raw)
                return raw
    except Exception as e:
        raw = f"{icao} METAR UNAVAILABLE ({type(e).__name__})"
        _metar_cache[icao] = (now, raw)
        return raw
    raw = f"{icao} NO METAR AVAILABLE"
    _metar_cache[icao] = (now, raw)
    return raw


# ============================================================
# CHARTS
# ============================================================
def get_charts(icao):
    icao = icao.upper()
    apt = DB.get(icao, {})
    country = apt.get("country", "").upper()

    links = [
        {"name": "SkyVector",         "url": f"https://skyvector.com/airport/{icao}", "embed": False},
        {"name": "AirportNavFinder",  "url": f"https://airportnavfinder.com/airport/{icao}/", "embed": False},
        {"name": "FlightAware",       "url": f"https://www.flightaware.com/resources/airport/{icao}/procedures", "embed": False},
        {"name": "ChartFox",          "url": f"https://chartfox.org/{icao}", "embed": False},
    ]

    if country == "US":
        links.insert(0, {"name": "AirNav (FAA, official)",
                         "url": f"https://www.airnav.com/airport/{icao}",
                         "embed": True, "embed_url": f"https://www.airnav.com/airport/{icao}"})
    elif country in ("RU", "KZ", "BY"):
        links.insert(0, {"name": "ЦАИ ГА (RU)", "url": "https://caica.ru/", "embed": False})
    elif country == "GB":
        links.insert(0, {"name": "NATS AIS (UK)", "url": "https://www.aurora.nats.co.uk/htmlAIP/", "embed": False})
    elif country == "DE":
        links.insert(0, {"name": "DFS AIP (DE)", "url": "https://aip.dfs.de/BasicAIP/", "embed": False})
    elif country == "FR":
        links.insert(0, {"name": "SIA France", "url": "https://www.sia.aviation-civile.gouv.fr/", "embed": False})
    elif country == "AU":
        links.insert(0, {"name": "Airservices AU", "url": "https://www.airservicesaustralia.com/aip/aip.asp", "embed": False})
    elif country == "CA":
        links.insert(0, {"name": "NAV CANADA", "url": "https://www.navcanada.ca/en/aeronautical-information.aspx", "embed": False})

    return {"icao": icao, "country": country, "name": apt.get("name", ""), "links": links}


# ============================================================
# ЭНДПОИНТЫ
# ============================================================
@app.get("/")
def read_index():
    try:
        with open("indexfly.html", "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    except Exception:
        return HTMLResponse(content="<h1>indexfly.html not found</h1>")


@app.get("/airports/stats")
def airports_stats():
    return {
        "airports": len(DB),
        "runways": len(RUNWAYS),
        "navaids": len(NAVAIDS),
        "fixes": len(FIXES),
        "airways": len(AIRWAYS),
        "graph_nodes": len(AWY_GRAPH),
        "graph_edges": sum(len(v) for v in AWY_GRAPH.values()) // 2,
        "xplane_loaded": XPLANE_LOADED,
        "navigraph_enabled": bool(NAVIGRAPH_CLIENT_ID and NAVIGRAPH_CLIENT_SECRET),
        "fpdb_enabled": bool(FLIGHTPLANDB_KEY),
        "loaded_at": DB_LOADED_AT,
    }


@app.get("/runways")
def runways(icao: str):
    icao = icao.upper().strip()
    if icao not in DB:
        raise HTTPException(404, f"Airport '{icao}' not in database")
    return {"icao": icao, "name": DB[icao].get("name", ""),
            "runways": RUNWAYS.get(icao, [])}


@app.get("/charts")
def charts(icao: str):
    icao = icao.upper().strip()
    if icao not in DB:
        raise HTTPException(404, f"Airport '{icao}' not in database")
    return get_charts(icao)


@app.get("/weather")
def weather(icao: str):
    icao = icao.upper().strip()
    if icao not in DB:
        raise HTTPException(404, f"Airport '{icao}' not in database")
    return {"icao": icao, "name": DB[icao].get("name", ""), "metar": get_metar(icao)}


@app.get("/calculate")
def calc(dep: str, arr: str, ac: str, dep_rwy: str = "", arr_rwy: str = ""):
    dep = dep.upper().strip()
    arr = arr.upper().strip()
    ac  = ac.upper().strip()
    dep_rwy = dep_rwy.strip().upper()
    arr_rwy = arr_rwy.strip().upper()

    if dep not in DB:
        raise HTTPException(400, f"Departure airport '{dep}' not in database")
    if arr not in DB:
        raise HTTPException(400, f"Arrival airport '{arr}' not in database")
    if ac not in PROFILES:
        raise HTTPException(400, f"Aircraft '{ac}' not supported. Try: {list(PROFILES.keys())}")
    if dep == arr:
        raise HTTPException(400, "Departure and Arrival must differ")

    alt = find_automatic_alternate(arr)
    path_points, route_source, used_dep_rwy, used_arr_rwy = get_route(
        dep, arr, dep_rwy or None, arr_rwy or None)

    d_main = 0.0
    for i in range(len(path_points) - 1):
        d_main += dist(path_points[i]["lat"], path_points[i]["lon"],
                       path_points[i + 1]["lat"], path_points[i + 1]["lon"])

    t_main = d_main / PROFILES[ac]["speed"]
    trip_fuel = (t_main * PROFILES[ac]["burn"]) + PROFILES[ac]["climb"]
    d_alt = dist(DB[arr]["lat"], DB[arr]["lon"], DB[alt]["lat"], DB[alt]["lon"]) * 1.05
    t_alt = d_alt / PROFILES[ac]["speed"]
    alt_fuel = (t_alt * PROFILES[ac]["burn"]) + PROFILES[ac]["climb"]
    cont_fuel = trip_fuel * PROFILES[ac]["cont"]
    hold_fuel = PROFILES[ac]["hold"]
    tof = trip_fuel + alt_fuel + cont_fuel + hold_fuel

    hours, minutes = int(t_main), int((t_main - int(t_main)) * 60)
    h_a, m_a = int(t_alt), int((t_alt - int(t_alt)) * 60)
    route_string = " DCT ".join([pt["id"] for pt in path_points])

    let_crz = "FL350 (ODD)" if DB[arr]["lon"] > DB[dep]["lon"] else "FL360 (EVEN)"
    if ac == "C172":
        let_crz = "5000 FT"

    ofp = (
        f"\n  FLYBRIEF DISPATCH SYSTEM - OPERATIONAL FLIGHT PLAN\n"
        f"  =========================================================\n\n"
        f"  {dep} ({DB[dep]['name']})  ->  {arr} ({DB[arr]['name']})\n"
        f"  AIRCRAFT: {PROFILES[ac]['name']}\n"
        f"  ROUTE SOURCE: {route_source.upper()}\n"
        f"  DEP RWY: {used_dep_rwy or '-'}    ARR RWY: {used_arr_rwy or '-'}\n\n"
        f"  CRUISE ALTITUDE: {let_crz}\n"
        f"  ROUTE LOG: {route_string}\n\n"
        f"  TRIP FUEL:    {round(trip_fuel):<8} |  BLOCK TIME:  {hours:02d}:{minutes:02d}\n"
        f"  ALTN FUEL:    {round(alt_fuel):<8} |  ALTN TIME:   {h_a:02d}:{m_a:02d}\n"
        f"  MIN TAKE-OFF: {round(tof):<8} |  DIST ROUTE:  {round(d_main):<4} NM\n"
        f"  ALTERNATE:    {alt} ({DB[alt]['name']})\n"
        f"  ---------------------------------------------------------\n"
        f"  METAR DEPARTURE: {get_metar(dep)}\n"
        f"  METAR ARRIVAL:   {get_metar(arr)}\n"
        f"  METAR ALTERNATE: {get_metar(alt)}\n"
        f"  ---------------------------------------------------------\n\n"
        f"  END OF OPERATIONAL FLIGHT PLAN\n"
    )

    path_coords = [[pt["lon"], pt["lat"]] for pt in path_points]

    return {
        "aircraft":     PROFILES[ac]["name"],
        "ofp_text":     ofp,
        "path":         path_coords,
        "waypoints":    path_points,
        "alt_lat":      DB[alt]["lat"],
        "alt_lon":      DB[alt]["lon"],
        "alt_id":       alt,
        "alternate":    alt,
        "distance_nm":  round(d_main),
        "ete":          f"{hours:02d}:{minutes:02d}",
        "fuel_kg":      round(tof),
        "route_source": route_source,
        "dep_rwy":      used_dep_rwy,
        "arr_rwy":      used_arr_rwy,
        "charts": {
            "dep": get_charts(dep),
            "arr": get_charts(arr),
            "alt": get_charts(alt),
        },
        "airports_count": len(DB),
        "navaids_count":  len(NAVAIDS),
        "fixes_count":    len(FIXES),
        "airways_count":  len(AIRWAYS),
    }


@app.get("/download_pln")
def download_pln(dep: str, arr: str, dep_rwy: str = "", arr_rwy: str = ""):
    dep = dep.upper().strip()
    arr = arr.upper().strip()
    if dep not in DB:
        raise HTTPException(400, f"Departure airport '{dep}' not in database")
    if arr not in DB:
        raise HTTPException(400, f"Arrival airport '{arr}' not in database")

    path_points, _, _, _ = get_route(dep, arr, dep_rwy or None, arr_rwy or None)

    pln = '<?xml version="1.0" encoding="UTF-8"?>\n'
    pln += '<SimBase.Document Type="FlightPlan" version="1,0">\n'
    pln += '  <FlightPlan.FlightPlan>\n'
    pln += f'    <Title>{dep} to {arr}</Title>\n'
    pln += '    <FPType>IFR</FPType>\n'
    pln += '    <RouteType>Direct</RouteType>\n'
    pln += f'    <DepartureID>{dep}</DepartureID>\n'
    pln += f'    <DestinationID>{arr}</DestinationID>\n'

    for pt in path_points:
        p_type = "Airport" if pt["id"] in (dep, arr) else "Intersection"
        pln += f'    <ATCWaypoint id="{pt["id"]}">\n'
        pln += f'      <ATCWaypointType>{p_type}</ATCWaypointType>\n'
        pln += f'      <WorldPosition>N{pt["lat"]:.6f},E{pt["lon"]:.6f},+000000.00</WorldPosition>\n'
        pln += f'    </ATCWaypoint>\n'

    pln += '  </FlightPlan.FlightPlan>\n'
    pln += '</SimBase.Document>'

    return Response(
        content=pln,
        media_type="application/xml",
        headers={"Content-Disposition": f"attachment; filename={dep}{arr}.pln"},
    )
