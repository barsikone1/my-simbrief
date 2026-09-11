import math
from fastapi import FastAPI, HTTPException

app = FastAPI(title="SimBrief Analog API")

# --- 1. БАЗА ДАННЫХ АЭРОПОРТОВ (AIRPORTS DATABASE) ---
AIRPORTS_DB = {
    "ULLI": {"name": "Pulkovo", "lat": 59.8003, "lon": 30.2625},
    "UUEE": {"name": "Sheremetyevo", "lat": 55.9726, "lon": 37.4146},
    "UUWW": {"name": "Vnukovo", "lat": 55.5961, "lon": 37.2675},
    "UUDD": {"name": "Domodedovo", "lat": 55.4088, "lon": 37.9061},
    "URSS": {"name": "Sochi", "lat": 43.4499, "lon": 39.9566},
    "OMDB": {"name": "Dubai", "lat": 25.2532, "lon": 55.3657},
    "KJFK": {"name": "New York JFK", "lat": 40.6398, "lon": -73.7789},
}

# --- 2. ЛЕТНО-ТЕХНИЧЕСКИЕ ХАРАКТЕРИСТИКИ ---
PROFILES = {
    "B738": {"name": "Boeing 737-800", "speed": 450, "burn": 2500, "climb": 800, "cont": 0.05, "hold": 1200},
    "A20N": {"name": "Airbus A320neo", "speed": 440, "burn": 2200, "climb": 700, "cont": 0.05, "hold": 1100}
}

# --- 3. РАСЧЕТ РАССТОЯНИЯ ---
def dist(lat1, lon1, lat2, lon2):
    R = 3440.065
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2-lat1), math.radians(lon2-lon1)
    a = math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))

# --- 4. НОВЫЙ ОПТИМИЗИРОВАННЫЙ ЭНДПОИНТ ---
@app.get("/calculate")
def calc(dep: str, arr: str, ac: str):
    # Переводим в верхний регистр на случай, если пользователь введет строчными (ulli)
    dep = dep.upper().strip()
    arr = arr.upper().strip()
    ac = ac.upper().strip()
    
    # Ищем аэропорты и самолет в нашей базе
    if dep not in AIRPORTS_DB:
        raise HTTPException(status_code=400, detail=f"Аэропорт вылета {dep} не найден в базе")
    if arr not in AIRPORTS_DB:
        raise HTTPException(status_code=400, detail=f"Аэропорт прибытия {arr} не найден в базе")
    if ac not in PROFILES:
        raise HTTPException(status_code=400, detail=f"Самолет {ac} не найден в профилях")
        
    airport_dep = AIRPORTS_DB[dep]
    airport_arr = AIRPORTS_DB[arr]
    p = PROFILES[ac]
    
    # Считаем расстояние по координатам из базы
    d = dist(airport_dep["lat"], airport_dep["lon"], airport_arr["lat"], airport_arr["lon"]) * 1.05
    t = d / p["speed"]
    trip = (t * p["burn"]) + p["climb"]
    tof = trip * (1 + p["cont"]) + p["hold"]
    
    return {
        "aircraft": p["name"],
        "origin": f"{dep} ({airport_dep['name']})",
        "destination": f"{arr} ({airport_arr['name']})",
        "distance_nm": round(d, 1),
        "flight_time": f"{int(t)}h {int((t-int(t))*60)}m",
        "min_takeoff_fuel_kg": round(tof)
    }
from fastapi.responses import HTMLResponse
import os

@app.get("/")
def read_index():
    try:
        # Пытаемся найти и прочитать файл интерактивной карты
        file_path = os.path.join(os.path.dirname(__file__), "indexfly.html")
        with open(file_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    except Exception as e:
        return HTMLResponse(content=f"<h1>Ошибка: файл indexfly.html не найден на сервере. {str(e)}</h1>")
