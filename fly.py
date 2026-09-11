import math
import urllib.request
import json
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, Response

app = FastAPI(title="FlyBrief EDMS Engine")

# База данных аэропортов
DB = {
    "ULLI": {"name": "Pulkovo", "lat": 59.8003, "lon": 30.2625},
    "UUEE": {"name": "Sheremetyevo", "lat": 55.9726, "lon": 37.4146},
    "UUWW": {"name": "Vnukovo", "lat": 55.5961, "lon": 37.2675},
    "UUDD": {"name": "Domodedovo", "lat": 55.4088, "lon": 37.9061},
    "URSS": {"name": "Sochi", "lat": 43.4499, "lon": 39.9566},
    "OMDB": {"name": "Dubai Intl", "lat": 25.2532, "lon": 55.3657},
    "KJFK": {"name": "New York JFK", "lat": 40.6398, "lon": -73.7789}
}

# Обновленная база навигационных трасс
ROUTES = {
    "ULLI-UUWW": [{"id": "ULLI", "lat": 59.8003, "lon": 30.2625}, {"id": "LED", "lat": 59.8010, "lon": 30.3010}, {"id": "KOTAM", "lat": 59.1230, "lon": 31.9540}, {"id": "LUKOR", "lat": 58.4410, "lon": 33.2120}, {"id": "GEKLA", "lat": 57.6540, "lon": 34.5010}, {"id": "SUGOL", "lat": 56.8820, "lon": 35.8110}, {"id": "UWPS", "lat": 53.1110, "lon": 45.0190}, {"id": "UUWW", "lat": 55.5961, "lon": 37.2675}]
}

# Расширенные профили ВС (Добавлены B777 и C172)
PROFILES = {
    "B738": {"name": "Boeing 737-800", "speed": 450, "burn": 2500, "climb": 800, "cont": 0.05, "hold": 1200},
    "A20N": {"name": "Airbus A320neo", "speed": 440, "burn": 2200, "climb": 700, "cont": 0.05, "hold": 1100},
    "B77W": {"name": "Boeing 777-300ER", "speed": 490, "burn": 6800, "climb": 1800, "cont": 0.05, "hold": 3500},
    "C172": {"name": "Cessna 172 Skyhawk", "speed": 110, "burn": 35, "climb": 15, "cont": 0.05, "hold": 20}
}

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
            if data and len(data) > 0: return data[0].get("rawOb", f"{icao} NO METAR")
    except: pass
    return f"{icao} METAR CURRENTLY UNINTERRUPTED"

@app.get("/")
def read_index():
    try:
        with open("indexfly.html", "r", encoding="utf-8") as f: return HTMLResponse(content=f.read())
    except: return HTMLResponse(content="<h1>Файл indexfly.html не найден</h1>")

@app.get("/calculate")
def calc(dep: str, arr: str, alt: str, ac: str):
    dep, arr, alt, ac = dep.upper().strip(), arr.upper().strip(), alt.upper().strip(), ac.upper().strip()
    if dep not in DB or arr not in DB or alt not in DB or ac not in PROFILES:
        raise HTTPException(status_code=400, detail="Airport or Aircraft not found")
    
    route_key = f"{dep}-{arr}"
    path_points = ROUTES.get(route_key, [{"id": dep, "lat": DB[dep]["lat"], "lon": DB[dep]["lon"]}, {"id": arr, "lat": DB[arr]["lat"], "lon": DB[arr]["lon"]}])
    
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
    
    ofp = f"\n  FLYBRIEF DISPATCH SYSTEM - OPERATIONAL FLIGHT PLAN\n  =========================================================\n\n  ROUTE LOG: {route_string}\n\n  TRIP FUEL:    {round(trip_fuel):<8} |  BLOCK TIME:  {hours:02d}:{minutes:02d}\n  ALTN FUEL:    {round(alt_fuel):<8} |  ALTN TIME:   {h_a:02d}:{m_a:02d}\n  MIN TAKE-OFF: {round(tof):<8} |  DIST ROUTE:  {round(d_main):<4} NM\n  ---------------------------------------------------------\n  METAR DEPARTURE: {get_metar(dep)}\n  METAR ARRIVAL:   {get_metar(arr)}\n  ---------------------------------------------------------\n\n  END OF OPERATIONAL FLIGHT PLAN\n"
    return {"aircraft": PROFILES[ac]["name"], "ofp_text": ofp, "path": path_points, "alt_lat": DB[alt]["lat"], "alt_lon": DB[alt]["lon"], "alt_id": alt}

@app.get("/download_pln")
def download_pln(dep: str, arr: str):
    dep, arr = dep.upper().strip(), arr.upper().strip()
    route_key = f"{dep}-{arr}"
    path_points = ROUTES.get(route_key, [{"id": dep, "lat": DB[dep]["lat"], "lon": DB[dep]["lon"]}, {"id": arr, "lat": DB[arr]["lat"], "lon": DB[arr]["lon"]}])
    
    # Генерация официальной XML-структуры .pln для Microsoft Flight Simulator
    pln = '<?xml version="1.0" encoding="UTF-8"?>\n<SimBase.Document Type="FlightPlan" version="1,0">\n'
    pln += f'  <FlightPlan.FlightPlan>\n    <Title>{dep} to {arr}</Title>\n    <FPType>IFR</FPType>\n    <RouteType>Direct</RouteType>\n'
    pln += f'    <DepartureID>{dep}</DepartureID>\n    <DestinationID>{arr}</DestinationID>\n'
    
    for pt in path_points:
        p_type = "Airport" if pt["id"] in [dep, arr] else "VOR" if len(pt["id"]) == 3 else "Intersection"
        pln += f'    <ATCWaypoint id="{pt["id"]}">\n      <ATCWaypointType>{p_type}</ATCWaypointType>\n'
        pln += f'      <WorldPosition>N{pt["lat"]:.6f},E{pt["lon"]:.6f},+000000.00</WorldPosition>\n    </ATCWaypoint>\n'
        
    pln += '  </FlightPlan.FlightPlan>\n</SimBase.Document>'
    
    return Response(content=pln, media_type="application/xml", headers={"Content-Disposition": f"attachment; filename={dep}{arr}.pln"})
