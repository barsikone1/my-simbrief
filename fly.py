import math
import urllib.request
import json
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, Response

app = FastAPI(title="FlyBrief Pro Engine")

# Глобальная база данных аэропортов FlyBrief
DB = {
    "ULLI": {"name": "Pulkovo", "lat": 59.8003, "lon": 30.2625},
    "UUEE": {"name": "Sheremetyevo", "lat": 55.9726, "lon": 37.4146},
    "UUWW": {"name": "Vnukovo", "lat": 55.5961, "lon": 37.2675},
    "UUDD": {"name": "Domodedovo", "lat": 55.4088, "lon": 37.9061},
    "URSS": {"name": "Sochi", "lat": 43.4499, "lon": 39.9566},
    "OMDB": {"name": "Dubai Intl", "lat": 25.2532, "lon": 55.3657},
    "KJFK": {"name": "New York JFK", "lat": 40.6398, "lon": -73.7789}
}

PROFILES = {
    "B738": {"name": "Boeing 737-800", "speed": 450, "burn": 2500, "climb": 800, "cont": 0.05, "hold": 1200},
    "A20N": {"name": "Airbus A320neo", "speed": 440, "burn": 2200, "climb": 700, "cont": 0.05, "hold": 1100},
    "B77W": {"name": "Boeing 777-300ER", "speed": 490, "burn": 6800, "climb": 1800, "cont": 0.05, "hold": 3500},
    "C172": {"name": "Cessna 172 Skyhawk", "speed": 110, "burn": 35, "climb": 15, "cont": 0.05, "hold": 20}
}

# Генератор фейковых навигационных точек (Имена реальных пятибуквенников ICAO)
FIX_NAMES = ["SUGOL", "KOTAM", "LUKOR", "GEKLA", "BANUT", "OKUDI", "NIDOR", "RUGEL", "ABESI", "PITOK", "DITON"]

def dist(lat1, lon1, lat2, lon2):
    R = 3440.065
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2-lat1), math.radians(lon2-lon1)
    a = math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))

def get_metar(icao):
    try:
        url = f"https://aviationweather.gov{icao.lower()}&format=json"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read().decode("utf-8"))
            if data and len(data) > 0: return data.get("rawOb", f"{icao} NO METAR")
    except: pass
    return f"{icao} METAR CURRENTLY UNINTERRUPTED"

# УМНЫЙ АЛГОРИТМ: Автоматический поиск ближайшего запасного порта
def find_automatic_alternate(arr_icao):
    arr_apt = DB[arr_icao]
    best_alt = None
    min_d = 999999.0
    
    for icao, apt in DB.items():
        if icao == arr_icao: continue
        d = dist(arr_apt["lat"], arr_apt["lon"], apt["lat"], apt["lon"])
        if d < min_d:
            min_d = d
            best_alt = icao
    return best_alt if best_alt else arr_icao

# УМНЫЙ АЛГОРИТМ: Генератор ломаной трассы из 4 промежуточных точек
def generate_smart_route(dep, arr):
    p_dep = DB[dep]
    p_arr = DB[arr]
    
    path = [{"id": dep, "lat": p_dep["lat"], "lon": p_dep["lon"]}]
    steps = 4 # Создаем 4 промежуточные точки (зигзаг трассы)
    
    for i in range(1, steps):
        pct = i / steps
        # Линейная интерполяция между депом и арривалом
        interp_lat = p_dep["lat"] + (p_arr["lat"] - p_dep["lat"]) * pct
        interp_lon = p_dep["lon"] + (p_arr["lon"] - p_dep["lon"]) * pct
        
        # Добавляем боковое смещение (зигзаг трассы), зависящее от индекса
        offset_multiplier = 0.8 if i % 2 == 0 else -0.8
        # Смещаем навигационную точку в сторону для имитации реальной воздушной трассы
        lat_fix = interp_lat + (offset_multiplier * 0.4)
        lon_fix = interp_lon + (offset_multiplier * -0.4)
        
        fix_name = FIX_NAMES[(i + ord(dep[0]) + ord(arr[0])) % len(FIX_NAMES)]
        path.append({"id": f"{fix_name}{i}", "lat": round(lat_fix, 4), "lon": round(lon_fix, 4)})
        
    path.append({"id": arr, "lat": p_arr["lat"], "lon": p_arr["lon"]})
    return path

@app.get("/")
def read_index():
    try:
        with open("indexfly.html", "r", encoding="utf-8") as f: return HTMLResponse(content=f.read())
    except: return HTMLResponse(content="<h1>Файл indexfly.html не найден</h1>")

@app.get("/calculate")
def calc(dep: str, arr: str, ac: str):
    dep, arr, ac = dep.upper().strip(), arr.upper().strip(), ac.upper().strip()
    if dep not in DB or arr not in DB or ac not in PROFILES:
        raise HTTPException(status_code=400, detail="Airport or Aircraft not found")
    
    # 1. Автоматическая генерация запасного
    alt = find_automatic_alternate(arr)
    
    # 2. Динамическая генерация ломаной авиатрассы
    path_points = generate_smart_route(dep, arr)
    
    d_main = 0.0
    for i in range(len(path_points)-1):
        d_main += dist(path_points[i]["lat"], path_points[i]["lon"], path_points[i+1]["lat"], path_points[i+1]["lon"])
        
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
    
    # Вычисление эшелона
    let_crz = "FL360 (EVEN)"
    if ac == "C172": let_crz = "5000 FT"
    else:
        is_east = DB[arr]["lon"] > DB[dep]["lon"]
        let_crz = "FL350 (ODD)" if is_east else "FL360 (EVEN)"

    ofp = f"\n  FLYBRIEF DISPATCH SYSTEM - OPERATIONAL FLIGHT PLAN\n  =========================================================\n\n  CRUISE ALTITUDE: {let_crz}\n  ROUTE LOG: {route_string}\n\n  TRIP FUEL:    {round(trip_fuel):<8} |  BLOCK TIME:  {hours:02d}:{minutes:02d}\n  ALTN FUEL:    {round(alt_fuel):<8} |  ALTN TIME:   {h_a:02d}:{m_a:02d}\n  MIN TAKE-OFF: {round(tof):<8} |  DIST ROUTE:  {round(d_main):<4} NM\n  ---------------------------------------------------------\n  METAR DEPARTURE: {get_metar(dep)}\n  METAR ARRIVAL:   {get_metar(arr)}\n  METAR ALTERNATE: {get_metar(alt)}\n  ---------------------------------------------------------\n\n  END OF OPERATIONAL FLIGHT PLAN\n"
    return {"aircraft": PROFILES[ac]["name"], "ofp_text": ofp, "path": path_points, "alt_lat": DB[alt]["lat"], "alt_lon": DB[alt]["lon"], "alt_id": alt}

@app.get("/download_pln")
def download_pln(dep: str, arr: str):
    dep, arr = dep.upper().strip(), arr.upper().strip()
    path_points = generate_smart_route(dep, arr)
    pln = '<?xml version="1.0" encoding="UTF-8"?>\n<SimBase.Document Type="FlightPlan" version="1,0">\n  <FlightPlan.FlightPlan>\n'
    pln += f'    <Title>{dep} to {arr}</Title>\n    <FPType>IFR</FPType>\n    <RouteType>Direct</RouteType>\n    <DepartureID>{dep}</DepartureID>\n    <DestinationID>{arr}</DestinationID>\n'
    for pt in path_points:
        p_type = "Airport" if pt["id"] in [dep, arr] else "Intersection"
        pln += f'    <ATCWaypoint id="{pt["id"]}">\n      <ATCWaypointType>{p_type}</ATCWaypointType>\n      <WorldPosition>N{pt["lat"]:.6f},E{pt["lon"]:.6f},+000000.00</WorldPosition>\n    </ATCWaypoint>\n'
    pln += '  </FlightPlan.FlightPlan>\n</SimBase.Document>'
    return Response(content=pln, media_type="application/xml", headers={"Content-Disposition": f"attachment; filename={dep}{arr}.pln"})
