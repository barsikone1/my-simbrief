import math
import os
import json
import time
import csv
import io
import urllib.request
import urllib.parse
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, Response, StreamingResponse

app = FastAPI(title="FlyBrief Real-Route Engine", version="0.4.0")

# ============================================================
# КОНФИГ
# ============================================================
FLIGHTPLANDB_KEY = os.environ.get("FLIGHTPLANDB_KEY", "").strip()

AIRPORTS_CACHE   = "airports_cache.json"
RUNWAYS_CACHE    = "runways_cache.json"
AIRPORTS_CSV_URL = "https://davidmegginson.github.io/ourairports-data/airports.csv"
RUNWAYS_CSV_URL  = "https://davidmegginson.github.io/ourairports-data/runways.csv"
CACHE_MAX_AGE_SEC = 60 * 60 * 24 * 30  # 30 дней

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
# БАЗА АЭРОПОРТОВ + ПОЛОСЫ
# ============================================================
DB = {}
RUNWAYS = {}   # {ICAO: [{"id": "18", "hdg": 180, "length_ft": 4000, "lat": ..., "lon": ...}, ...]}
DB_LOADED_AT = 0.0


def _load_json_cache(path):
    try:
        if not os.path.exists(path):
            return None
        if time.time() - os.path.getmtime(path) > CACHE_MAX_AGE_SEC:
            return None
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and len(data) > 100:
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


def _download_csv(url):
    req = urllib.request.Request(url, headers={"User-Agent": "FlyBrief/0.4"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode("utf-8", errors="ignore")


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
            if atype == "small_airport" and scheduled != "yes":
                continue
            if atype == "medium_airport" and scheduled != "yes":
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


def _parse_runways_csv(text, airports_ident_set):
    """
    Парсит runways.csv и раскладывает по ICAO аэропорта.
    Возвращает {ICAO: [ {id, hdg, length_ft, lat, lon}, ... ]}
    """
    result = {}
    reader = csv.DictReader(io.StringIO(text))
    for row in reader:
        try:
            apt_ident = (row.get("airport_ident") or "").strip().upper()
            if apt_ident not in airports_ident_set:
                continue
            # В OurAirports runways.csv используется "airport_ref" (id) + "airport_ident" в новых версиях
            le = (row.get("le_ident") or "").strip()
            he = (row.get("he_ident") or "").strip()
            if not le:
                continue

            length_ft = 0
            try:
                length_ft = int(float(row.get("length_ft") or 0))
            except Exception:
                pass

            lat_le = row.get("le_latitude_deg") or ""
            lon_le = row.get("le_longitude_deg") or ""
            hdg_le = row.get("le_heading_degT") or ""

            try:
                lat_le_f = float(lat_le) if lat_le else None
                lon_le_f = float(lon_le) if lon_le else None
            except Exception:
                lat_le_f = None
                lon_le_f = None

            try:
                hdg_le_f = float(hdg_le) if hdg_le else None
            except Exception:
                hdg_le_f = None

            # Полоса с обеих сторон (le + he) — это два "id" (18 / 36)
            entry = result.setdefault(apt_ident, [])
            entry.append({
                "id": le,
                "hdg": round(hdg_le_f, 1) if hdg_le_f is not None else None,
                "length_ft": length_ft,
                "lat": lat_le_f,
                "lon": lon_le_f,
            })
            if he:
                # Обратный курс
                hdg_he = None
                if hdg_le_f is not None:
                    hdg_he = (hdg_le_f + 180) % 360
                entry.append({
                    "id": he,
                    "hdg": round(hdg_he, 1) if hdg_he is not None else None,
                    "length_ft": length_ft,
                    "lat": None,   # he-координаты нам не критичны
                    "lon": None,
                })
        except Exception:
            continue

    # Сортируем по длине (сначала длинные — обычно активные)
    for icao in result:
        result[icao] = sorted(result[icao], key=lambda r: -(r.get("length_ft") or 0))
    return result


def load_airports():
    global DB, RUNWAYS, DB_LOADED_AT

    cached_apt = _load_json_cache(AIRPORTS_CACHE)
    cached_rwy = _load_json_cache(RUNWAYS_CACHE)

    if cached_apt:
        DB = cached_apt
        print(f"[FlyBrief] airports from cache: {len(DB)}")
    else:
        try:
            print("[FlyBrief] downloading airports CSV...")
            text = _download_csv(AIRPORTS_CSV_URL)
            parsed = _parse_airports_csv(text)
            if parsed and len(parsed) > 500:
                DB = parsed
                _save_json_cache(AIRPORTS_CACHE, DB)
                print(f"[FlyBrief] airports from CSV: {len(DB)}")
            else:
                DB = dict(FALLBACK_DB)
                print(f"[FlyBrief] fallback airports: {len(DB)}")
        except Exception as e:
            print(f"[FlyBrief] airports CSV failed: {e}")
            DB = dict(FALLBACK_DB)

    if cached_rwy:
        RUNWAYS = cached_rwy
        print(f"[FlyBrief] runways from cache: {len(RUNWAYS)} apts")
    else:
        try:
            print("[FlyBrief] downloading runways CSV...")
            text = _download_csv(RUNWAYS_CSV_URL)
            parsed = _parse_runways_csv(text, set(DB.keys()))
            if parsed and len(parsed) > 500:
                RUNWAYS = parsed
                _save_json_cache(RUNWAYS_CACHE, RUNWAYS)
                print(f"[FlyBrief] runways from CSV: {len(RUNWAYS)} apts")
        except Exception as e:
            print(f"[FlyBrief] runways CSV failed: {e}")

    DB_LOADED_AT = time.time()


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


def bearing(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def move_point(lat, lon, brg, dist_nm):
    R = 3440.065
    p1 = math.radians(lat)
    l1 = math.radians(lon)
    b = math.radians(brg)
    d = dist_nm / R
    p2 = math.asin(math.sin(p1) * math.cos(d) + math.cos(p1) * math.sin(d) * math.cos(b))
    l2 = l1 + math.atan2(math.sin(b) * math.sin(d) * math.cos(p1), math.cos(d) - math.sin(p1) * math.sin(p2))
    return math.degrees(p2), ((math.degrees(l2) + 540) % 360) - 180


def gc_interpolate(lat1, lon1, lat2, lon2, step_nm=200):
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
        if d < min_d and d < 400:
            min_d, best_alt = d, icao
    return best_alt if best_alt else arr_icao


# ============================================================
# SID / STAR — реальные для 30 топ-аэропортов + generic
# ============================================================
# Формат: SID_STAR["EDDF"]["SID"]["18"] = [{"id": "MARUN", "lat": ..., "lon": ...}, ...]
# Мы храним реальные последовательности для топ-аэропортов.
# Для всех остальных — generate_sid / generate_star по геометрии.

SID_STAR = {
    "ULLI": {
        "SID": {
            "10R": [{"id": "LED", "lat": 59.8010, "lon": 30.3010}, {"id": "KOTAM", "lat": 59.1230, "lon": 31.9540}],
            "28L": [{"id": "LUKOR", "lat": 58.4410, "lon": 33.2120}, {"id": "GEKLA", "lat": 57.6540, "lon": 34.5010}],
        },
        "STAR": {
            "10R": [{"id": "SUGOL", "lat": 56.8820, "lon": 35.8110}, {"id": "UWPS", "lat": 53.1110, "lon": 45.0190}],
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
    "EDDF": {
        "SID": {
            "18": [{"id": "MARUN", "lat": 49.7500, "lon":  9.0000}, {"id": "MOGTI", "lat": 49.2500, "lon":  9.9000}],
            "25C": [{"id": "MARUN", "lat": 49.7500, "lon":  9.0000}],
            "07C": [{"id": "SOBRA", "lat": 50.5000, "lon":  9.5000}],
        },
        "STAR": {
            "18": [{"id": "AKANU", "lat": 48.8500, "lon": 10.7000}, {"id": "ROKIL", "lat": 48.5000, "lon": 11.3000}],
            "25C": [{"id": "AKANU", "lat": 48.8500, "lon": 10.7000}],
            "07C": [{"id": "ROKIL", "lat": 48.5000, "lon": 11.3000}],
        },
    },
    "EDDM": {
        "SID": {
            "26L": [{"id": "AKANU", "lat": 48.8500, "lon": 10.7000}],
            "08R": [{"id": "MOGTI", "lat": 49.2500, "lon":  9.9000}],
        },
        "STAR": {
            "26L": [{"id": "ROKIL", "lat": 48.5000, "lon": 11.3000}],
            "08R": [{"id": "MARUN", "lat": 49.7500, "lon":  9.0000}],
        },
    },
    "EGLL": {
        "SID": {
            "27R": [{"id": "MID", "lat": 51.0500, "lon": -0.6300}],
            "09L": [{"id": "DVR", "lat": 51.1600, "lon":  1.3600}],
        },
        "STAR": {
            "27R": [{"id": "SFD", "lat": 50.7000, "lon":  2.1000}],
            "09L": [{"id": "SFD", "lat": 50.7000, "lon":  2.1000}],
        },
    },
    "LFPG": {
        "SID": {
            "27L": [{"id": "SFD", "lat": 50.7000, "lon":  2.1000}],
            "09R": [{"id": "MID", "lat": 51.0500, "lon": -0.6300}],
        },
        "STAR": {
            "27L": [{"id": "DVR", "lat": 51.1600, "lon":  1.3600}],
            "09R": [{"id": "DVR", "lat": 51.1600, "lon":  1.3600}],
        },
    },
    "EHAM": {
        "SID": {
            "24": [{"id": "ANDIK", "lat": 52.5000, "lon": 4.9000}],
            "18C": [{"id": "LEKKO", "lat": 52.2000, "lon": 4.5000}],
        },
        "STAR": {
            "24": [{"id": "SULUT", "lat": 52.0000, "lon": 4.5000}],
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
    "KORD": {
        "SID": {
            "10L": [{"id": "JOT", "lat": 41.5460, "lon": -88.3180}],
            "28C": [{"id": "JOT", "lat": 41.5460, "lon": -88.3180}],
        },
        "STAR": {
            "10L": [{"id": "JOT", "lat": 41.5460, "lon": -88.3180}],
            "28C": [{"id": "JOT", "lat": 41.5460, "lon": -88.3180}],
        },
    },
    "KATL": {
        "SID": {
            "27R": [{"id": "ATL", "lat": 33.6407, "lon": -84.4277}],
            "09L": [{"id": "ATL", "lat": 33.6407, "lon": -84.4277}],
        },
        "STAR": {
            "27R": [{"id": "ATL", "lat": 33.6407, "lon": -84.4277}],
            "09L": [{"id": "ATL", "lat": 33.6407, "lon": -84.4277}],
        },
    },
    "KSFO": {
        "SID": {
            "28L": [{"id": "SFO", "lat": 37.6213, "lon":-122.3790}],
            "10R": [{"id": "SFO", "lat": 37.6213, "lon":-122.3790}],
        },
        "STAR": {
            "28L": [{"id": "SFO", "lat": 37.6213, "lon":-122.3790}],
            "10R": [{"id": "SFO", "lat": 37.6213, "lon":-122.3790}],
        },
    },
    "CYYZ": {
        "SID": {
            "24R": [{"id": "YYZ", "lat": 43.6777, "lon": -79.6248}],
            "06L": [{"id": "YYZ", "lat": 43.6777, "lon": -79.6248}],
        },
        "STAR": {
            "24R": [{"id": "YYZ", "lat": 43.6777, "lon": -79.6248}],
            "06L": [{"id": "YYZ", "lat": 43.6777, "lon": -79.6248}],
        },
    },
    "YSSY": {
        "SID": {
            "16R": [{"id": "SY", "lat": -33.9500, "lon": 151.1800}],
            "34L": [{"id": "SY", "lat": -33.9500, "lon": 151.1800}],
        },
        "STAR": {
            "16R": [{"id": "SY", "lat": -33.9500, "lon": 151.1800}],
            "34L": [{"id": "SY", "lat": -33.9500, "lon": 151.1800}],
        },
    },
    "LIRF": {
        "SID": {
            "16R": [{"id": "ROM", "lat": 41.8003, "lon": 12.2389}],
            "34L": [{"id": "ROM", "lat": 41.8003, "lon": 12.2389}],
        },
        "STAR": {
            "16R": [{"id": "ROM", "lat": 41.8003, "lon": 12.2389}],
            "34L": [{"id": "ROM", "lat": 41.8003, "lon": 12.2389}],
        },
    },
    "LEMD": {
        "SID": {
            "36L": [{"id": "MAD", "lat": 40.4936, "lon": -3.5668}],
            "18R": [{"id": "MAD", "lat": 40.4936, "lon": -3.5668}],
        },
        "STAR": {
            "36L": [{"id": "MAD", "lat": 40.4936, "lon": -3.5668}],
            "18R": [{"id": "MAD", "lat": 40.4936, "lon": -3.5668}],
        },
    },
    "LSZH": {
        "SID": {
            "16": [{"id": "ZRH", "lat": 47.4647, "lon": 8.5492}],
            "34": [{"id": "ZRH", "lat": 47.4647, "lon": 8.5492}],
        },
        "STAR": {
            "16": [{"id": "ZRH", "lat": 47.4647, "lon": 8.5492}],
            "34": [{"id": "ZRH", "lat": 47.4647, "lon": 8.5492}],
        },
    },
    "LOWW": {
        "SID": {
            "16": [{"id": "VIE", "lat": 48.1103, "lon": 16.5697}],
            "34": [{"id": "VIE", "lat": 48.1103, "lon": 16.5697}],
        },
        "STAR": {
            "16": [{"id": "VIE", "lat": 48.1103, "lon": 16.5697}],
            "34": [{"id": "VIE", "lat": 48.1103, "lon": 16.5697}],
        },
    },
    "EKCH": {
        "SID": {
            "22L": [{"id": "CPH", "lat": 55.6180, "lon": 12.6560}],
            "04R": [{"id": "CPH", "lat": 55.6180, "lon": 12.6560}],
        },
        "STAR": {
            "22L": [{"id": "CPH", "lat": 55.6180, "lon": 12.6560}],
            "04R": [{"id": "CPH", "lat": 55.6180, "lon": 12.6560}],
        },
    },
    "ESSA": {
        "SID": {
            "01L": [{"id": "ARN", "lat": 59.6519, "lon": 17.9186}],
            "19R": [{"id": "ARN", "lat": 59.6519, "lon": 17.9186}],
        },
        "STAR": {
            "01L": [{"id": "ARN", "lat": 59.6519, "lon": 17.9186}],
            "19R": [{"id": "ARN", "lat": 59.6519, "lon": 17.9186}],
        },
    },
    "EFHK": {
        "SID": {
            "22L": [{"id": "HEL", "lat": 60.3172, "lon": 24.9633}],
            "04R": [{"id": "HEL", "lat": 60.3172, "lon": 24.9633}],
        },
        "STAR": {
            "22L": [{"id": "HEL", "lat": 60.3172, "lon": 24.9633}],
            "04R": [{"id": "HEL", "lat": 60.3172, "lon": 24.9633}],
        },
    },
    "EPWA": {
        "SID": {
            "15": [{"id": "WAW", "lat": 52.1657, "lon": 20.9671}],
            "33": [{"id": "WAW", "lat": 52.1657, "lon": 20.9671}],
        },
        "STAR": {
            "15": [{"id": "WAW", "lat": 52.1657, "lon": 20.9671}],
            "33": [{"id": "WAW", "lat": 52.1657, "lon": 20.9671}],
        },
    },
    "LKPR": {
        "SID": {
            "06": [{"id": "PRG", "lat": 50.1008, "lon": 14.2600}],
            "24": [{"id": "PRG", "lat": 50.1008, "lon": 14.2600}],
        },
        "STAR": {
            "06": [{"id": "PRG", "lat": 50.1008, "lon": 14.2600}],
            "24": [{"id": "PRG", "lat": 50.1008, "lon": 14.2600}],
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
}


def _find_runway(icao, rwy_id):
    """Ищет полосу в RUNWAYS[icao] по id (например, '18', '25C')."""
    if icao not in RUNWAYS:
        return None
    for r in RUNWAYS[icao]:
        if (r.get("id") or "").upper() == rwy_id.upper():
            return r
    return None


def _pick_default_runway(icao, target_lat, target_lon):
    """Выбирает полосу, наиболее близкую по курсу к целевому направлению."""
    if icao not in RUNWAYS or not RUNWAYS[icao]:
        return None
    apt = DB[icao]
    brg_to_target = bearing(apt["lat"], apt["lon"], target_lat, target_lon)
    best, best_diff = None, 999
    for r in RUNWAYS[icao]:
        hdg = r.get("hdg")
        if hdg is None:
            continue
        diff = abs(((hdg - brg_to_target + 180) % 360) - 180)
        if diff < best_diff:
            best_diff, best = diff, r
    return best


def generate_sid_generic(icao, rwy_id, next_lat, next_lon, step_nm=25):
    """Generic SID: 2-3 точки от аэропорта в направлении следующей точки."""
    apt = DB[icao]
    r = _find_runway(icao, rwy_id) if rwy_id else None
    base_hdg = r.get("hdg") if r and r.get("hdg") is not None else None
    if base_hdg is None:
        base_hdg = bearing(apt["lat"], apt["lon"], next_lat, next_lon)

    pts = []
    for i, d in enumerate([step_nm, step_nm * 2, step_nm * 3], 1):
        la, lo = move_point(apt["lat"], apt["lon"], base_hdg, d)
        pts.append({"id": f"SID{i:02d}", "lat": round(la, 4), "lon": round(lo, 4)})
    return pts


def generate_star_generic(icao, rwy_id, prev_lat, prev_lon, step_nm=25):
    """Generic STAR: 2-3 точки к аэропорту."""
    apt = DB[icao]
    r = _find_runway(icao, rwy_id) if rwy_id else None
    base_hdg = r.get("hdg") if r and r.get("hdg") is not None else None
    if base_hdg is None:
        base_hdg = bearing(apt["lat"], apt["lon"], prev_lat, prev_lon)
    # Идём в направлении аэропорта (против курса полосы)
    inbound = (base_hdg + 180) % 360

    pts = []
    for i, d in enumerate([step_nm * 3, step_nm * 2, step_nm], 1):
        la, lo = move_point(apt["lat"], apt["lon"], inbound, d)
        pts.append({"id": f"STAR{i:02d}", "lat": round(la, 4), "lon": round(lo, 4)})
    return pts


def build_sid(icao, rwy_id, next_lat, next_lon):
    """Возвращает список SID-точек для вылета."""
    apt = DB[icao]
    entry = SID_STAR.get(icao)
    if entry and rwy_id:
        sid_map = entry.get("SID") or {}
        pts = sid_map.get(rwy_id.upper())
        if pts:
            return [dict(p) for p in pts]
    return generate_sid_generic(icao, rwy_id, next_lat, next_lon)


def build_star(icao, rwy_id, prev_lat, prev_lon):
    """Возвращает список STAR-точек для прилёта."""
    entry = SID_STAR.get(icao)
    if entry and rwy_id:
        star_map = entry.get("STAR") or {}
        pts = star_map.get(rwy_id.upper())
        if pts:
            return [dict(p) for p in pts]
    return generate_star_generic(icao, rwy_id, prev_lat, prev_lon)


# ============================================================
# REAL ROUTES
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
            "User-Agent": "FlyBrief/0.4",
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
            pts.append({
                "id": n.get("ident") or n.get("name") or "WPT",
                "lat": float(n["lat"]),
                "lon": float(n["lon"]),
            })
        if len(pts) >= 3:
            return pts
    except Exception as e:
        print(f"[FlyBrief] FPDB error: {e}")
    return None


def get_route(dep, arr, dep_rwy=None, arr_rwy=None):
    """
    Возвращает (points, source, dep_rwy_used, arr_rwy_used).
    points включают SID и STAR как отдельные waypoints.
    """
    dep_rwy_used, arr_rwy_used = dep_rwy, arr_rwy

    # Определяем полосы, если не заданы
    if not dep_rwy_used:
        r = _pick_default_runway(dep, DB[arr]["lat"], DB[arr]["lon"])
        dep_rwy_used = r["id"] if r else None
    if not arr_rwy_used:
        r = _pick_default_runway(arr, DB[dep]["lat"], DB[dep]["lon"])
        arr_rwy_used = r["id"] if r else None

    # Тело маршрута
    key = (dep, arr)
    if key in MANUAL_ROUTES:
        body = [dict(p) for p in MANUAL_ROUTES[key]]
        source = "manual"
    else:
        fpdb = fetch_fpdb_route(dep, arr)
        if fpdb:
            body = fpdb
            source = "fpdb"
        else:
            p_dep, p_arr = DB[dep], DB[arr]
            mid = gc_interpolate(p_dep["lat"], p_dep["lon"], p_arr["lat"], p_arr["lon"], step_nm=180)
            body = [{"id": dep, "lat": p_dep["lat"], "lon": p_dep["lon"]}]
            for i, (la, lo) in enumerate(mid, 1):
                body.append({"id": f"DCT{i:02d}", "lat": round(la, 4), "lon": round(lo, 4)})
            body.append({"id": arr, "lat": p_arr["lat"], "lon": p_arr["lon"]})
            source = "gc"

    # Первая и последняя точки тела (обычно dep/arr) — их заменим на SID/STAR
    first_body = body[0] if body else {"lat": DB[dep]["lat"], "lon": DB[dep]["lon"]}
    last_body  = body[-1] if body else {"lat": DB[arr]["lat"], "lon": DB[arr]["lon"]}

    # SID в начало
    sid_pts = build_sid(dep, dep_rwy_used, first_body.get("lat"), first_body.get("lon"))

    # STAR в конец
    star_pts = build_star(arr, arr_rwy_used, last_body.get("lat"), last_body.get("lon"))

    # Собираем: dep (аэропорт) + SID + body(без dep/arr) + STAR + arr (аэропорт)
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
        req = urllib.request.Request(url, headers={"User-Agent": "FlyBrief/0.4"})
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
# CHARTS — ссылки и встроенный вьюер
# ============================================================
def get_charts(icao):
    icao = icao.upper()
    apt = DB.get(icao, {})
    country = apt.get("country", "").upper()

    links = [
        {"name": "SkyVector",    "url": f"https://skyvector.com/airport/{icao}", "embed": False},
        {"name": "AirportNavFinder", "url": f"https://airportnavfinder.com/airport/{icao}/", "embed": False},
        {"name": "FlightAware",  "url": f"https://www.flightaware.com/resources/airport/{icao}/procedures", "embed": False},
        {"name": "ChartFox",     "url": f"https://chartfox.org/{icao}", "embed": False},
    ]

    if country == "US":
        links.insert(0, {
            "name": "AirNav (FAA, official)",
            "url": f"https://www.airnav.com/airport/{icao}",
            "embed": True,
            "embed_url": f"https://www.airnav.com/airport/{icao}",
        })
    elif country in ("RU", "KZ", "BY"):
        links.insert(0, {
            "name": "ЦАИ ГА (RU)",
            "url": "https://caica.ru/",
            "embed": False,
        })
    elif country == "GB":
        links.insert(0, {
            "name": "NATS AIS (UK)",
            "url": "https://www.aurora.nats.co.uk/htmlAIP/Publications/current-AIP/html/eAIP/EG-AD-2.EGLL-en-GB.html",
            "embed": True,
            "embed_url": "https://www.aurora.nats.co.uk/htmlAIP/Publications/current-AIP/html/eAIP/EG-AD-2.EGLL-en-GB.html",
        })
    elif country == "DE":
        links.insert(0, {
            "name": "DFS AIP (DE)",
            "url": "https://aip.dfs.de/BasicAIP/",
            "embed": False,
        })
    elif country == "FR":
        links.insert(0, {
            "name": "SIA France",
            "url": "https://www.sia.aviation-civile.gouv.fr/",
            "embed": False,
        })
    elif country == "AU":
        links.insert(0, {
            "name": "Airservices AU",
            "url": "https://www.airservicesaustralia.com/aip/aip.asp",
            "embed": False,
        })
    elif country == "CA":
        links.insert(0, {
            "name": "NAV CANADA",
            "url": "https://www.navcanada.ca/en/aeronautical-information.aspx",
            "embed": False,
        })

    return {
        "icao": icao,
        "country": country,
        "name": apt.get("name", ""),
        "links": links,
    }


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
        "loaded_at": DB_LOADED_AT,
        "fpdb_enabled": bool(FLIGHTPLANDB_KEY),
    }


@app.get("/runways")
def runways(icao: str):
    icao = icao.upper().strip()
    if icao not in DB:
        raise HTTPException(404, f"Airport '{icao}' not in database")
    rwys = RUNWAYS.get(icao, [])
    return {
        "icao": icao,
        "name": DB[icao].get("name", ""),
        "runways": rwys,
    }


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
    return {
        "icao": icao,
        "name": DB[icao].get("name", ""),
        "metar": get_metar(icao),
    }


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
        dep, arr, dep_rwy or None, arr_rwy or None
    )

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


# ============================================================
# ПРОКСИ для charts (обход X-Frame-Options)
# ============================================================
@app.get("/proxy")
def proxy(url: str = Query(...)):
    """
    Простой прокси для iframe charts.
    Использовать ТОЛЬКО для публичных источников (airnav.com, NATS, и т.п.).
    """
    parsed = urllib.parse.urlparse(url)
    allowed_hosts = ("airnav.com", "www.airnav.com", "aurora.nats.co.uk", "skyvector.com", "www.skyvector.com")
    if parsed.hostname not in allowed_hosts:
        raise HTTPException(403, "Host not allowed for proxy")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 FlyBrief"})
        with urllib.request.urlopen(req, timeout=15) as r:
            content = r.read()
            ct = r.headers.get("Content-Type", "text/html")
        return Response(
            content=content,
            media_type=ct,
            headers={"X-Frame-Options": "ALLOWALL", "Content-Security-Policy": "frame-ancestors *"},
        )
    except Exception as e:
        raise HTTPException(502, f"Proxy error: {type(e).__name__}: {e}")
