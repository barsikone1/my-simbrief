import math
import urllib.request
import json
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, Response

app = FastAPI(title="FlyBrief Real-Route Engine", version="0.2.0")

# ============================================================
# БАЗА АЭРОПОРТОВ
# ============================================================
DB = {
    # --- Россия / СНГ ---
    "ULLI": {"name": "Pulkovo",             "lat": 59.8003, "lon":  30.2625},
    "UUEE": {"name": "Sheremetyevo",        "lat": 55.9726, "lon":  37.4146},
    "UUWW": {"name": "Vnukovo",             "lat": 55.5961, "lon":  37.2675},
    "UUDD": {"name": "Domodedovo",          "lat": 55.4088, "lon":  37.9061},
    "URSS": {"name": "Sochi",               "lat": 43.4499, "lon":  39.9566},
    "ULMM": {"name": "Murmansk",            "lat": 68.7817, "lon":  32.7508},
    "UNNT": {"name": "Novosibirsk",         "lat": 55.0126, "lon":  82.6507},
    "USSS": {"name": "Koltsovo",            "lat": 56.7431, "lon":  60.8027},
    "UKBB": {"name": "Boryspil",            "lat": 50.3450, "lon":  30.8947},

    # --- Европа ---
    "EDDF": {"name": "Frankfurt",           "lat": 50.0379, "lon":   8.5622},
    "EDDM": {"name": "Munich",              "lat": 48.3538, "lon":  11.7861},
    "EDDB": {"name": "Berlin Brandenburg",  "lat": 52.3667, "lon":  13.5033},
    "EGLL": {"name": "London Heathrow",     "lat": 51.4700, "lon":  -0.4543},
    "EGKK": {"name": "London Gatwick",      "lat": 51.1537, "lon":  -0.1821},
    "LFPG": {"name": "Paris CDG",           "lat": 49.0097, "lon":   2.5479},
    "LFPO": {"name": "Paris Orly",          "lat": 48.7233, "lon":   2.3794},
    "EHAM": {"name": "Amsterdam Schiphol",  "lat": 52.3105, "lon":   4.7683},
    "LIRF": {"name": "Rome Fiumicino",      "lat": 41.8003, "lon":  12.2389},
    "LIMC": {"name": "Milan Malpensa",      "lat": 45.6306, "lon":   8.7281},
    "LEMD": {"name": "Madrid Barajas",      "lat": 40.4936, "lon":  -3.5668},
    "LEBL": {"name": "Barcelona El Prat",   "lat": 41.2971, "lon":   2.0785},
    "LSZH": {"name": "Zurich",              "lat": 47.4647, "lon":   8.5492},
    "LOWW": {"name": "Vienna",              "lat": 48.1103, "lon":  16.5697},
    "EKCH": {"name": "Copenhagen",          "lat": 55.6180, "lon":  12.6560},
    "ENGM": {"name": "Oslo Gardermoen",     "lat": 60.1976, "lon":  11.1004},
    "ESSA": {"name": "Stockholm Arlanda",   "lat": 59.6519, "lon":  17.9186},
    "EFHK": {"name": "Helsinki Vantaa",     "lat": 60.3172, "lon":  24.9633},
    "EPWA": {"name": "Warsaw Chopin",       "lat": 52.1657, "lon":  20.9671},
    "LKPR": {"name": "Prague Vaclav",       "lat": 50.1008, "lon":  14.2600},
    "LHBP": {"name": "Budapest Liszt",      "lat": 47.4369, "lon":  19.2556},
    "LGAV": {"name": "Athens",              "lat": 37.9364, "lon":  23.9445},
    "LTFM": {"name": "Istanbul",            "lat": 41.2753, "lon":  28.7519},

    # --- Ближний Восток ---
    "OMDB": {"name": "Dubai Intl",          "lat": 25.2532, "lon":  55.3657},
    "OMAA": {"name": "Abu Dhabi",           "lat": 24.4330, "lon":  54.6511},
    "OTHH": {"name": "Doha Hamad",          "lat": 25.2731, "lon":  51.6081},
    "OERK": {"name": "Riyadh",              "lat": 24.9576, "lon":  46.6988},

    # --- Азия ---
    "VHHH": {"name": "Hong Kong",           "lat": 22.3080, "lon": 113.9185},
    "RJTT": {"name": "Tokyo Haneda",        "lat": 35.5494, "lon": 139.7798},
    "RJAA": {"name": "Tokyo Narita",        "lat": 35.7647, "lon": 140.3864},
    "RKSI": {"name": "Seoul Incheon",       "lat": 37.4602, "lon": 126.4407},
    "ZBAA": {"name": "Beijing Capital",     "lat": 40.0801, "lon": 116.5846},
    "ZSPD": {"name": "Shanghai Pudong",     "lat": 31.1434, "lon": 121.8052},
    "WSSS": {"name": "Singapore Changi",    "lat":  1.3644, "lon": 103.9915},
    "VTBS": {"name": "Bangkok Suvarnabhumi","lat": 13.6900, "lon": 100.7501},
    "VIDP": {"name": "Delhi",               "lat": 28.5562, "lon":  77.1000},

    # --- Северная Америка ---
    "KJFK": {"name": "New York JFK",        "lat": 40.6398, "lon": -73.7789},
    "KLAX": {"name": "Los Angeles",         "lat": 33.9416, "lon":-118.4085},
    "KORD": {"name": "Chicago O'Hare",      "lat": 41.9742, "lon": -87.9073},
    "KATL": {"name": "Atlanta",             "lat": 33.6407, "lon": -84.4277},
    "KSFO": {"name": "San Francisco",       "lat": 37.6213, "lon":-122.3790},
    "KSEA": {"name": "Seattle",             "lat": 47.4502, "lon":-122.3088},
    "KMIA": {"name": "Miami",               "lat": 25.7959, "lon": -80.2871},
    "KBOS": {"name": "Boston",              "lat": 42.3656, "lon": -71.0096},
    "KDFW": {"name": "Dallas/Fort Worth",   "lat": 32.8998, "lon": -97.0403},
    "KDEN": {"name": "Denver",              "lat": 39.8561, "lon":-104.6737},
    "CYYZ": {"name": "Toronto Pearson",     "lat": 43.6777, "lon": -79.6248},
    "CYVR": {"name": "Vancouver",           "lat": 49.1967, "lon":-123.1815},
    "MMMX": {"name": "Mexico City",         "lat": 19.4363, "lon": -99.0721},

    # --- Океания / Африка ---
    "YSSY": {"name": "Sydney",              "lat":-33.9399, "lon": 151.1753},
    "YMML": {"name": "Melbourne",           "lat":-37.6733, "lon": 144.8433},
    "NZAA": {"name": "Auckland",            "lat":-37.0082, "lon": 174.7917},
    "FAOR": {"name": "Johannesburg",        "lat":-26.1392, "lon":  28.2460},
    "HECA": {"name": "Cairo",               "lat": 30.1219, "lon":  31.4056},
    "FACT": {"name": "Cape Town",           "lat":-33.9648, "lon":  18.6017},
}

# ============================================================
# ПРОФИЛИ ВС
# ============================================================
PROFILES = {
    "B77W": {"name": "Boeing 777-300ER",     "speed": 490, "burn": 6800, "climb": 1800, "cont": 0.05, "hold": 3500},
    "B738": {"name": "Boeing 737-800",       "speed": 450, "burn": 2500, "climb":  800, "cont": 0.05, "hold": 1200},
    "A20N": {"name": "Airbus A320neo",       "speed": 440, "burn": 2200, "climb":  700, "cont": 0.05, "hold": 1100},
    "C172": {"name": "Cessna 172 Skyhawk",   "speed": 110, "burn":   35, "climb":   15, "cont": 0.05, "hold":   20},
}

# ============================================================
# УТИЛИТЫ
# ============================================================
def dist(lat1, lon1, lat2, lon2):
    R = 3440.065
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def find_automatic_alternate(arr_icao):
    arr_apt = DB[arr_icao]
    best_alt, min_d = None, 999999.0
    for icao, apt in DB.items():
        if icao == arr_icao:
            continue
        d = dist(arr_apt["lat"], arr_apt["lon"], apt["lat"], apt["lon"])
        if d < min_d:
            min_d, best_alt = d, icao
    return best_alt if best_alt else arr_icao


# ============================================================
# РЕАЛЬНЫЕ / ИНТЕРПОЛИРОВАННЫЕ МАРШРУТЫ
# ============================================================
def get_real_aviation_route(dep, arr):
    if dep == "ULLI" and arr == "UUWW":
        return [
            {"id": "ULLI",  "lat": 59.8003, "lon": 30.2625},
            {"id": "LED",   "lat": 59.8010, "lon": 30.3010},
            {"id": "KOTAM", "lat": 59.1230, "lon": 31.9540},
            {"id": "LUKOR", "lat": 58.4410, "lon": 33.2120},
            {"id": "GEKLA", "lat": 57.6540, "lon": 34.5010},
            {"id": "SUGOL", "lat": 56.8820, "lon": 35.8110},
            {"id": "UWPS",  "lat": 53.1110, "lon": 45.0190},
            {"id": "UUWW",  "lat": 55.5961, "lon": 37.2675},
        ]

    if dep == "KJFK" and arr == "OMDB":
        return [
            {"id": "KJFK",  "lat": 40.6398, "lon": -73.7789},
            {"id": "COATE", "lat": 42.4430, "lon": -71.1210},
            {"id": "ALLEX", "lat": 46.1200, "lon": -60.4000},
            {"id": "BIKF",  "lat": 63.9850, "lon": -22.6056},
            {"id": "GURLU", "lat": 56.3210, "lon":  10.1200},
            {"id": "ODILO", "lat": 48.1200, "lon":  16.3400},
            {"id": "SITAN", "lat": 34.2100, "lon":  43.1200},
            {"id": "OMDB",  "lat": 25.2532, "lon":  55.3657},
        ]

    # Универсальный интерполятор с равномерными точками
    p_dep, p_arr = DB[dep], DB[arr]
    dlat = p_arr["lat"] - p_dep["lat"]
    dlon = p_arr["lon"] - p_dep["lon"]
    return [
        {"id": dep,     "lat": p_dep["lat"],              "lon": p_dep["lon"]},
        {"id": "NAV01", "lat": p_dep["lat"] + dlat * 0.25 + 0.5, "lon": p_dep["lon"] + dlon * 0.25 - 0.5},
        {"id": "NAV02", "lat": p_dep["lat"] + dlat * 0.50,       "lon": p_dep["lon"] + dlon * 0.50},
        {"id": "NAV03", "lat": p_dep["lat"] + dlat * 0.75 - 0.5, "lon": p_dep["lon"] + dlon * 0.75 + 0.5},
        {"id": arr,     "lat": p_arr["lat"],              "lon": p_arr["lon"]},
    ]


# ============================================================
# METAR (реальный, aviationweather.gov)
# ============================================================
def get_metar(icao):
    try:
        url = f"https://aviationweather.gov/api/data/metar?ids={icao.upper()}&format=json"
        req = urllib.request.Request(url, headers={"User-Agent": "FlyBrief/0.2"})
        with urllib.request.urlopen(req, timeout=6) as response:
            data = json.loads(response.read().decode("utf-8"))
            if isinstance(data, list) and len(data) > 0:
                return data[0].get("rawOb", f"{icao} NO METAR")
    except Exception as e:
        return f"{icao} METAR UNAVAILABLE ({type(e).__name__})"
    return f"{icao} NO METAR AVAILABLE"


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
    path_points = get_real_aviation_route(dep, arr)

    # --- Дистанция и топливо ---
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
        f"  AIRCRAFT: {PROFILES[ac]['name']}\n\n"
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

    # --- path для OpenLayers: [[lon, lat], ...] ---
    path_coords = [[pt["lon"], pt["lat"]] for pt in path_points]

    return {
        "aircraft":  PROFILES[ac]["name"],
        "ofp_text":  ofp,
        "path":      path_coords,
        "waypoints": path_points,
        "alt_lat":   DB[alt]["lat"],
        "alt_lon":   DB[alt]["lon"],
        "alt_id":    alt,
        "alternate": alt,
        "distance_nm": round(d_main),
        "ete":       f"{hours:02d}:{minutes:02d}",
        "fuel_kg":   round(tof),
    }


@app.get("/download_pln")
def download_pln(dep: str, arr: str):
    dep = dep.upper().strip()
    arr = arr.upper().strip()

    if dep not in DB:
        raise HTTPException(400, f"Departure airport '{dep}' not in database")
    if arr not in DB:
        raise HTTPException(400, f"Arrival airport '{arr}' not in database")

    path_points = get_real_aviation_route(dep, arr)

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
