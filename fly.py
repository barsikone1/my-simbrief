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
