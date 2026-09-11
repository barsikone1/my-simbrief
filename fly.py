import math
import os
import json
import time
import csv
import io
import urllib.request
import urllib.parse
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, Response

app = FastAPI(title="FlyBrief Real-Route Engine", version="0.3.0")

# ============================================================
# КОНФИГ
# ============================================================
FLIGHTPLANDB_KEY = os.environ.get("FLIGHTPLANDB_KEY", "").strip()
AIRPORTS_CACHE = "airports_cache.json"
AIRPORTS_CSV_URL = "https://davidmegginson.github.io/ourairports-data/airports.csv"
CACHE_MAX_AGE_SEC = 60 * 60 * 24 * 30  # 30 дней

# Профили ВС
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

# Базовый набор на случай, если OurAirports недоступен
FALLBACK_DB = {
    "ULLI": {"name": "Pulkovo",             "lat": 59.8003, "lon":  30.2625, "country": "RU"},
    "UUEE": {"name": "Sheremetyevo",        "lat": 55.9726, "lon":  37.4146, "country": "RU"},
    "UUWW": {"name": "Vnukovo",             "lat": 55.5961, "lon":  37.2675, "country": "RU"},
    "UUDD": {"name": "Domodedovo",          "lat": 55.4088, "lon":  37.9061, "country": "RU"},
    "URSS": {"name": "Sochi",               "lat": 43.4499, "lon":  39.9566, "country": "RU"},
    "EDDF": {"name": "Frankfurt",           "lat": 50.0379, "lon":   8.5622, "country": "DE"},
    "EDDM": {"name": "Munich",              "lat": 48.3538, "lon":  11.7861, "country": "DE"},
    "EGLL": {"name": "London Heathrow",     "lat": 51.4700, "lon":  -0.4543, "country": "GB"},
    "LFPG": {"name": "Paris CDG",           "lat": 49.0097, "lon":   2.5479, "country": "FR"},
    "EHAM": {"name": "Amsterdam Schiphol",  "lat": 52.3105, "lon":   4.7683, "country": "NL"},
    "OMDB": {"name": "Dubai Intl",          "lat": 25.2532, "lon":  55.3657, "country": "AE"},
    "KJFK": {"name": "New York JFK",        "lat": 40.6398, "lon": -73.7789, "country": "US"},
    "KLAX": {"name": "Los Angeles",         "lat": 33.9416, "lon":-118.4085, "country": "US"},
}

# ============================================================
# ЗАГРУЗКА ВСЕХ АЭРОПОРТОВ МИРА (OurAirports)
# ============================================================
DB = {}
DB_LOADED_AT = 0.0


def _load_cache_from_disk():
    try:
        if not os.path.exists(AIRPORTS_CACHE):
            return None
        if time.time() - os.path.getmtime(AIRPORTS_CACHE) > CACHE_MAX_AGE_SEC:
            return None
        with open(AIRPORTS_CACHE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and len(data) > 100:
            return data
    except Exception:
        pass
    return None


def _save_cache_to_disk(data):
    try:
        with open(AIRPORTS_CACHE, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception:
        pass


def _download_airports_csv():
    req = urllib.request.Request(AIRPORTS_CSV_URL, headers={"User-Agent": "FlyBrief/0.3"})
    with urllib.request.urlopen(req, timeout=45) as r:
        return r.read().decode("utf-8", errors="ignore")


def _parse_airports_csv(text):
    """
    Парсит CSV от OurAirports, оставляет:
      - type in (large_airport, medium_airport, small_airport)
      - scheduled_service == yes OR type == large_airport
      - есть ident (ICAO)
      - есть lat/lon
    Возвращает dict {ICAO: {name, lat, lon, country, type, iata}}
    """
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
            if atype == "small_airport" and scheduled != "yes":
                continue
            if atype == "medium_airport" and scheduled != "yes":
                # медиум без регулярки оставляем только если это единственный вариант
                # (по факту — пропускаем, чтобы не раздувать базу)
                continue

            lat_s = row.get("latitude_deg") or ""
            lon_s = row.get("longitude_deg") or ""
            if not lat_s or not lon_s:
                continue
            lat = float(lat_s)
            lon = float(lon_s)
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                continue

            name = (row.get("name") or ident).strip()
            country = (row.get("iso_country") or "").strip().upper()
            iata = (row.get("iata_code") or "").strip().upper()

            result[ident] = {
                "name": name,
                "lat": round(lat, 6),
                "lon": round(lon, 6),
                "country": country,
                "type": atype,
                "iata": iata,
            }
        except Exception:
            continue
    return result


def load_airports():
    """Загружает базу аэропортов: сначала кеш, потом CSV, потом fallback."""
    global DB, DB_LOADED_AT

    cached = _load_cache_from_disk()
    if cached:
        DB = cached
        DB_LOADED_AT = time.time()
        print(f"[FlyBrief] airports loaded from cache: {len(DB)}")
        return

    try:
        print("[FlyBrief] downloading OurAirports CSV...")
        text = _download_airports_csv()
        parsed = _parse_airports_csv(text)
        if parsed and len(parsed) > 500:
            DB = parsed
            DB_LOADED_AT = time.time()
            _save_cache_to_disk(DB)
            print(f"[FlyBrief] airports loaded from CSV: {len(DB)}")
            return
    except Exception as e:
        print(f"[FlyBrief] CSV download failed: {e}")

    DB = dict(FALLBACK_DB)
    DB_LOADED_AT = time.time()
    print(f"[FlyBrief] fallback DB used: {len(DB)}")


# Загружаем сразу при импорте модуля (Render-friendly)
load_airports()


# ============================================================
# ГЕОМЕТРИЯ
# ============================================================
def dist(lat1, lon1, lat2, lon2):
    R = 3440.065
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def gc_interpolate(lat1, lon1, lat2, lon2, step_nm=200):
    """Сферическая интерполяция (slerp) — точки каждые step_nm морских миль."""
    p1, l1 = math.radians(lat1), math.radians(lon1)
    p2, l2 = math.radians(lat2), math.radians(lon2)

    d = 2 * math.asin(math.sqrt(
        math.sin((p2 - p1) / 2) ** 2 +
        math.cos(p1) * math.cos(p2) * math.sin((l2 - l1) / 2) ** 2
    ))
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


def find_automatic_alternate(arr_icao):
    if arr_icao not in DB:
        return arr_icao
    arr_apt = DB[arr_icao]
    best_alt, min_d = None, 999999.0
    for icao, apt in DB.items():
        if icao == arr_icao:
            continue
        d = dist(arr_apt["lat"], arr_apt["lon"], apt["lat"], apt["lon"])
        if d < min_d and d < 400:  # не дальше 400 NM
            min_d, best_alt = d, icao
    return best_alt if best_alt else arr_icao


# ============================================================
# REAL ROUTES: FlightPlanDatabase
# ============================================================
def fetch_fpdb_route(dep, arr):
    if not FLIGHTPLANDB_KEY:
        return None
    try:
        url = f"https://api.flightplandatabase.com/search/plan?fromICAO={dep}&toICAO={arr}&limit=1"
        req = urllib.request.Request(url, headers={
            "User-Agent": "FlyBrief/0.3",
            "X-API-Key": FLIGHTPLANDB_KEY,
            "Accept": "application/json",
        })
        with urllib.request.urlopen(req, timeout=6) as r:
            data = json.loads(r.read().decode("utf-8"))
        if not isinstance(data, list) or not data:
            return None
        plan = data[0]
        nodes = plan.get("route", {}).get("nodes") or []
        pts = []
        for n in nodes:
            lat = n.get("lat")
            lon = n.get("lon")
            ident = n.get("ident") or n.get("name") or "WPT"
            if lat is None or lon is None:
                continue
            pts.append({"id": ident, "lat": float(lat), "lon": float(lon)})
        if len(pts) >= 3:
            return pts
    except Exception as e:
        print(f"[FlyBrief] FPDB error: {e}")
    return None


# ============================================================
# REAL ROUTES: manual AIRAC overrides (самые популярные)
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


# ============================================================
# ОСНОВНОЙ РЕЗОЛВЕР МАРШРУТА
# ============================================================
def get_route(dep, arr):
    """
    Возвращает (points, source).
    points: [{id, lat, lon}, ...]
    source: "manual" | "fpdb" | "gc"
    """
    key = (dep, arr)
    if key in MANUAL_ROUTES:
        return MANUAL_ROUTES[key], "manual"

    fpdb = fetch_fpdb_route(dep, arr)
    if fpdb:
        return fpdb, "fpdb"

    # Great-circle с промежуточными точками
    p_dep, p_arr = DB[dep], DB[arr]
    mid = gc_interpolate(p_dep["lat"], p_dep["lon"], p_arr["lat"], p_arr["lon"], step_nm=180)
    pts = [{"id": dep, "lat": p_dep["lat"], "lon": p_dep["lon"]}]
    for i, (la, lo) in enumerate(mid, 1):
        pts.append({"id": f"DCT{i:02d}", "lat": round(la, 4), "lon": round(lo, 4)})
    pts.append({"id": arr, "lat": p_arr["lat"], "lon": p_arr["lon"]})
    return pts, "gc"


# ============================================================
# METAR
# ============================================================
_metar_cache = {}
_METAR_TTL = 60 * 15  # 15 минут


def get_metar(icao):
    now = time.time()
    c = _metar_cache.get(icao)
    if c and now - c[0] < _METAR_TTL:
        return c[1]
    try:
        url = f"https://aviationweather.gov/api/data/metar?ids={icao.upper()}&format=json"
        req = urllib.request.Request(url, headers={"User-Agent": "FlyBrief/0.3"})
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
# CHARTS (ссылки на официальные источники)
# ============================================================
def get_charts(icao):
    icao = icao.upper()
    apt = DB.get(icao, {})
    country = apt.get("country", "").upper()

    links = [
        {"name": "SkyVector (enroute + IAP)", "url": f"https://skyvector.com/airport/{icao}"},
        {"name": "AirportNavFinder",          "url": f"https://airportnavfinder.com/airport/{icao}/"},
        {"name": "FlightAware",               "url": f"https://www.flightaware.com/resources/airport/{icao}/procedures"},
    ]

    if country == "US":
        links.insert(0, {"name": "AirNav (FAA, official)", "url": f"https://www.airnav.com/airport/{icao}"})
    elif country in ("RU", "KZ", "BY"):
        links.insert(0, {"name": "ЦАИ ГА (official, RU)",  "url": f"https://caica.ru/ru/avia/airport/{icao}"})
    elif country == "GB":
        links.insert(0, {"name": "NATS AIS (official, UK)", "url": f"https://nats-uk.ead-it.com/cms-nats/opencms/en/Publications/AIP/"})
    elif country == "DE":
        links.insert(0, {"name": "DFS Germany (official)", "url": f"https://aip.dfs.de/BasicAIP/Charts/{icao}"})
    elif country == "FR":
        links.insert(0, {"name": "SIA France (official)",  "url": f"https://www.sia.aviation-civile.gouv.fr/"})
    elif country == "AU":
        links.insert(0, {"name": "Airservices AU (official)", "url": f"https://www.airservicesaustralia.com/aip/aip.asp"})
    elif country == "CA":
        links.insert(0, {"name": "NAV CANADA", "url": f"https://www.navcanada.ca/en/aeronautical-information.aspx"})

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
        "count": len(DB),
        "loaded_at": DB_LOADED_AT,
        "source": "cache" if _load_cache_from_disk() else "runtime",
        "fpdb_enabled": bool(FLIGHTPLANDB_KEY),
    }


@app.get("/charts")
def charts(icao: str):
    icao = icao.upper().strip()
    if icao not in DB:
        raise HTTPException(404, f"Airport '{icao}' not in database")
    return get_charts(icao)


@app.get("/calculate")
def calc(dep: str, arr: str, ac: str):
    dep = dep.upper().strip()
    arr = arr.upper().strip()
    ac  = ac.upper().strip()

    if dep not in DB:
        raise HTTPException(400, f"Departure airport '{dep}' not in database")
    if arr not in DB:
        raise HTTPException(400, f"Arrival airport '{arr}' not in database")
    if ac not in PROFILES:
        raise HTTPException(400, f"Aircraft '{ac}' not supported. Try: {list(PROFILES.keys())}")
    if dep == arr:
        raise HTTPException(400, "Departure and Arrival must differ")

    alt = find_automatic_alternate(arr)
    path_points, route_source = get_route(dep, arr)

    d_main = 0.0
    for i in range(len(path_points) - 1):
        d_main += dist(
            path_points[i]["lat"], path_points[i]["lon"],
            path_points[i + 1]["lat"], path_points[i + 1]["lon"],
        )

    t_main     = d_main / PROFILES[ac]["speed"]
    trip_fuel  = (t_main * PROFILES[ac]["burn"]) + PROFILES[ac]["climb"]
    d_alt      = dist(DB[arr]["lat"], DB[arr]["lon"], DB[alt]["lat"], DB[alt]["lon"]) * 1.05
    t_alt      = d_alt / PROFILES[ac]["speed"]
    alt_fuel   = (t_alt * PROFILES[ac]["burn"]) + PROFILES[ac]["climb"]
    cont_fuel  = trip_fuel * PROFILES[ac]["cont"]
    hold_fuel  = PROFILES[ac]["hold"]
    tof        = trip_fuel + alt_fuel + cont_fuel + hold_fuel

    hours, minutes = int(t_main), int((t_main - int(t_main)) * 60)
    h_a, m_a       = int(t_alt),  int((t_alt  - int(t_alt))  * 60)
    route_string   = " DCT ".join([pt["id"] for pt in path_points])

    let_crz = "FL350 (ODD)" if DB[arr]["lon"] > DB[dep]["lon"] else "FL360 (EVEN)"
    if ac == "C172":
        let_crz = "5000 FT"

    ofp = (
        f"\n  FLYBRIEF DISPATCH SYSTEM - OPERATIONAL FLIGHT PLAN\n"
        f"  =========================================================\n\n"
        f"  {dep} ({DB[dep]['name']})  ->  {arr} ({DB[arr]['name']})\n"
        f"  AIRCRAFT: {PROFILES[ac]['name']}\n"
        f"  ROUTE SOURCE: {route_source.upper()}\n\n"
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
        "aircraft":    PROFILES[ac]["name"],
        "ofp_text":    ofp,
        "path":        path_coords,
        "waypoints":   path_points,
        "alt_lat":     DB[alt]["lat"],
        "alt_lon":     DB[alt]["lon"],
        "alt_id":      alt,
        "alternate":   alt,
        "distance_nm": round(d_main),
        "ete":         f"{hours:02d}:{minutes:02d}",
        "fuel_kg":     round(tof),
        "route_source": route_source,
        "charts": {
            "dep": get_charts(dep),
            "arr": get_charts(arr),
            "alt": get_charts(alt),
        },
        "airports_count": len(DB),
    }


@app.get("/download_pln")
def download_pln(dep: str, arr: str):
    dep = dep.upper().strip()
    arr = arr.upper().strip()

    if dep not in DB:
        raise HTTPException(400, f"Departure airport '{dep}' not in database")
    if arr not in DB:
        raise HTTPException(400, f"Arrival airport '{arr}' not in database")

    path_points, _ = get_route(dep, arr)

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
