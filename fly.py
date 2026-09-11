# FlyBrief main.py v0.7.0
# X-Plane 12 navdata (airways/fixes/navaids) + A* по airway-графу
# Один файл. Зависимости: fastapi, uvicorn, стандартная библиотека.

import os
import io
import csv
import json
import math
import time
import heapq
import asyncio
import urllib.request
import urllib.parse
from typing import Optional, List, Dict, Tuple, Any
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, PlainTextResponse, JSONResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# =====================================================================
# КОНФИГ
# =====================================================================

APP_VERSION = "0.7.0"

REPO_RAW = "https://raw.githubusercontent.com/barsikone1/my-simbrief/main"
# Пробуем оба имени: .txt (как у тебя в репо) и .dat (на случай переименования)
NAVDATA_FILES = {
    "awy":  [f"{REPO_RAW}/earth_awy.txt",  f"{REPO_RAW}/earth_awy.dat"],
    "fix":  [f"{REPO_RAW}/earth_fix.txt",  f"{REPO_RAW}/earth_fix.dat"],
    "nav":  [f"{REPO_RAW}/earth_nav.txt",  f"{REPO_RAW}/earth_nav.dat"],
}

CACHE_DIR = os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() else "."
AIRPORTS_CACHE = os.path.join(CACHE_DIR, "airports_cache.json")
RUNWAYS_CACHE  = os.path.join(CACHE_DIR, "runways_cache.json")
NAVAIDS_CACHE  = os.path.join(CACHE_DIR, "navaids_cache.json")
AIRWAYS_CACHE  = os.path.join(CACHE_DIR, "airways_cache.json")

CACHE_TTL_DAYS = 30

# =====================================================================
# GLOBAL STORAGE
# =====================================================================

AIRPORTS: Dict[str, Dict[str, Any]] = {}      # ICAO -> {lat, lon, name, ...}
RUNWAYS:  Dict[str, List[Dict[str, Any]]] = {} # ICAO -> [ {ident, lat, lon, hdg, len} ]
NAVAIDS:  List[Dict[str, Any]] = []            # [{ident, lat, lon, type}]
NAVAID_INDEX: Dict[str, List[int]] = {}        # ident -> indices in NAVAIDS
NAVAID_GRAPH: Dict[int, List[Tuple[int, float]]] = {}  # idx -> [(idx, dist_nm)]

FIX_COORDS: Dict[str, Tuple[float, float]] = {}   # IDENT -> (lat, lon)
FIX_INDEX:  Dict[str, List[int]] = {}             # IDENT -> indices (если дубликаты)
AWY_GRAPH:  Dict[str, List[Dict[str, Any]]] = {}  # IDENT -> [ {to, awy, d, b, t, dir} ]
AWY_EDGE_COUNT = 0

XPLANE_LOADED = False
LOAD_STATE = {
    "airports": False,
    "runways": False,
    "navaids": False,
    "xplane": False,
    "started_at": None,
    "finished_at": None,
    "error": None,
}

# =====================================================================
# FASTAPI
# =====================================================================

app = FastAPI(title="FlyBrief", version=APP_VERSION)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# =====================================================================
# УТИЛИТЫ
# =====================================================================

def log(*a):
    print("[FlyBrief]", *a, flush=True)

EARTH_R_NM = 3440.065  # радиус Земли в морских милях

def haversine_nm(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return 2 * EARTH_R_NM * math.asin(min(1.0, math.sqrt(a)))

def is_cache_fresh(path: str) -> bool:
    if not os.path.exists(path):
        return False
    age = time.time() - os.path.getmtime(path)
    return age < CACHE_TTL_DAYS * 86400

def http_get_text(url: str, timeout: int = 60) -> Optional[str]:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "FlyBrief/0.7"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = r.read()
        try:
            return data.decode("utf-8", errors="replace")
        except Exception:
            return data.decode("latin-1", errors="replace")
    except Exception as e:
        log(f"http_get_text FAIL {url}: {e}")
        return None

# =====================================================================
# AEROPORTS / RUNWAYS (OurAirports) — как было
# =====================================================================

AIRPORTS_URL = "https://davidmegginson.github.io/ourairports-data/airports.csv"
RUNWAYS_URL  = "https://davidmegginson.github.io/ourairports-data/runways.csv"
NAVAIDS_URL  = "https://davidmegginson.github.io/ourairports-data/navaids.csv"

def load_airports():
    global AIRPORTS
    if is_cache_fresh(AIRPORTS_CACHE):
        try:
            with open(AIRPORTS_CACHE, "r", encoding="utf-8") as f:
                AIRPORTS = json.load(f)
            log(f"airports cache loaded: {len(AIRPORTS)}")
            LOAD_STATE["airports"] = True
            return
        except Exception as e:
            log(f"airports cache read fail: {e}")

    log("downloading airports.csv...")
    text = http_get_text(AIRPORTS_URL, timeout=120)
    if not text:
        log("airports download FAILED")
        return
    reader = csv.DictReader(io.StringIO(text))
    result = {}
    for row in reader:
        ident = (row.get("ident") or "").strip().upper()
        if not ident:
            continue
        try:
            lat = float(row["latitude_deg"]); lon = float(row["longitude_deg"])
        except Exception:
            continue
        result[ident] = {
            "ident": ident,
            "name": row.get("name", ""),
            "lat": lat, "lon": lon,
            "type": row.get("type", ""),
            "iata": (row.get("iata_code") or "").strip().upper(),
            "iso": (row.get("iso_country") or "").strip().upper(),
            "municipality": row.get("municipality", ""),
        }
    AIRPORTS = result
    try:
        with open(AIRPORTS_CACHE, "w", encoding="utf-8") as f:
            json.dump(AIRPORTS, f)
    except Exception as e:
        log(f"airports cache write fail: {e}")
    log(f"airports loaded: {len(AIRPORTS)}")
    LOAD_STATE["airports"] = True

def load_runways():
    global RUNWAYS
    if is_cache_fresh(RUNWAYS_CACHE):
        try:
            with open(RUNWAYS_CACHE, "r", encoding="utf-8") as f:
                RUNWAYS = json.load(f)
            log(f"runways cache loaded: {sum(len(v) for v in RUNWAYS.values())}")
            LOAD_STATE["runways"] = True
            return
        except Exception as e:
            log(f"runways cache read fail: {e}")

    log("downloading runways.csv...")
    text = http_get_text(RUNWAYS_URL, timeout=120)
    if not text:
        log("runways download FAILED")
        return
    reader = csv.DictReader(io.StringIO(text))
    result: Dict[str, List[Dict[str, Any]]] = {}
    for row in reader:
        icao = (row.get("airport_ident") or "").strip().upper()
        if not icao:
            continue
        try:
            lat = float(row["le_latitude_deg"]); lon = float(row["le_longitude_deg"])
        except Exception:
            continue
        try:
            hdg = float(row.get("le_heading_degT") or 0)
        except Exception:
            hdg = 0.0
        try:
            length = float(row.get("length_ft") or 0)
        except Exception:
            length = 0.0
        result.setdefault(icao, []).append({
            "ident": row.get("le_ident", ""),
            "lat": lat, "lon": lon,
            "hdg": hdg, "len_ft": length,
        })
    RUNWAYS = result
    try:
        with open(RUNWAYS_CACHE, "w", encoding="utf-8") as f:
            json.dump(RUNWAYS, f)
    except Exception as e:
        log(f"runways cache write fail: {e}")
    log(f"runways loaded: {sum(len(v) for v in RUNWAYS.values())}")
    LOAD_STATE["runways"] = True

def load_navaids():
    global NAVAIDS, NAVAID_INDEX
    if is_cache_fresh(NAVAIDS_CACHE):
        try:
            with open(NAVAIDS_CACHE, "r", encoding="utf-8") as f:
                NAVAIDS = json.load(f)
            NAVAID_INDEX = {}
            for i, n in enumerate(NAVAIDS):
                NAVAID_INDEX.setdefault(n["ident"], []).append(i)
            log(f"navaids cache loaded: {len(NAVAIDS)}")
            LOAD_STATE["navaids"] = True
            return
        except Exception as e:
            log(f"navaids cache read fail: {e}")

    log("downloading navaids.csv...")
    text = http_get_text(NAVAIDS_URL, timeout=120)
    if not text:
        log("navaids download FAILED")
        return
    reader = csv.DictReader(io.StringIO(text))
    result = []
    for row in reader:
        ident = (row.get("ident") or "").strip().upper()
        if not ident:
            continue
        try:
            lat = float(row["latitude_deg"]); lon = float(row["longitude_deg"])
        except Exception:
            continue
        result.append({
            "ident": ident, "lat": lat, "lon": lon,
            "type": row.get("type", ""),
            "name": row.get("name", ""),
        })
    NAVAIDS = result
    NAVAID_INDEX = {}
    for i, n in enumerate(NAVAIDS):
        NAVAID_INDEX.setdefault(n["ident"], []).append(i)
    try:
        with open(NAVAIDS_CACHE, "w", encoding="utf-8") as f:
            json.dump(NAVAIDS, f)
    except Exception as e:
        log(f"navaids cache write fail: {e}")
    log(f"navaids loaded: {len(NAVAIDS)}")
    LOAD_STATE["navaids"] = True

# =====================================================================
# X-PLANE 12 NAVDATA
# =====================================================================
#
# earth_fix.txt:  "lat lon ident"  (пробелы, ident может содержать дефис)
#                 В начале файла строки "I" / "1100 Version ..." / пустые
# earth_awy.txt:  fix_a lat_a lon_a fix_b lat_b lon_b airway dir base top
#                 Пример:
#                   07EBA DT 11 GILEX DT 11 N 1  95 245 G869
#                 где "DT 11" — тип+высота суффикс региона, игнорируем.
#                 Общая структура: 9 колонок:
#                   [fix_a, reg_a, code_a, fix_b, reg_b, code_b, type, dir, base, top, awy]
#                 Точнее: fix_a, xa, ya, fix_b, xb, yb, type(N/F), dir(1/2/3), base, top, awy
#                 НО в реальном файле после fix идёт 2-буквенный код региона и число (11=high, 2=low),
#                 поэтому колонки такие:
#                   fix_a, r_a, c_a, fix_b, r_b, c_b, type, dir, base, top, awy
# earth_nav.txt:  VOR/NDB/TACAN. Формат: code type ident name lat lon ... 
#                 Строки начинаются с числа (2=VOR, 3=NDB, 4=ILS, 12=TACAN...).
#                 Формат: "code name ident lat lon freq ..."
# =====================================================================

def parse_fix_line(line: str) -> Optional[Tuple[str, float, float]]:
    """Строка earth_fix: 'lat lon IDENT' (может быть 'lat lon IDENT' через пробелы)."""
    parts = line.split()
    if len(parts) < 3:
        return None
    # Ожидаем: [lat, lon, IDENT]
    try:
        lat = float(parts[0]); lon = float(parts[1])
    except ValueError:
        return None
    ident = parts[2].strip().upper()
    if not ident or len(ident) > 8:
        return None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    return (ident, lat, lon)

def load_xplane_fix(text: str) -> int:
    """Заполняет FIX_COORDS."""
    n = 0
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        # заголовки
        if line[0] in ("I", "A", "N") and len(line) < 5:
            continue
        if line.startswith("1100 ") or line.startswith("Copyright") or line.startswith("Metadata"):
            continue
        if line.startswith("#"):
            continue
        res = parse_fix_line(line)
        if not res:
            continue
        ident, lat, lon = res
        if ident not in FIX_COORDS:
            FIX_COORDS[ident] = (lat, lon)
            FIX_INDEX.setdefault(ident, []).append(len(FIX_COORDS) - 1)
        n += 1
    return n

def parse_awy_line(line: str) -> Optional[Dict[str, Any]]:
    """
    Парсит одну строку earth_awy.
    Ожидаемый формат (11 полей):
      fix_a, r_a, c_a, fix_b, r_b, c_b, type, dir, base, top, awy
    Реальный пример:
      07EBA DT 11 GILEX DT 11 N 1  95 245 G869
    То есть:
      [0]=fix_a [1]=DT [2]=11 [3]=fix_b [4]=DT [5]=11 [6]=N [7]=1 [8]=95 [9]=245 [10]=G869
    """
    parts = line.split()
    if len(parts) < 11:
        return None
    fix_a = parts[0].upper()
    fix_b = parts[3].upper()
    try:
        dirn = int(parts[7])
        base = int(parts[8])
        top  = int(parts[9])
    except ValueError:
        return None
    awy = parts[10].upper()
    if not fix_a or not fix_b or not awy:
        return None
    return {
        "a": fix_a, "b": fix_b,
        "dir": dirn, "base": base, "top": top, "awy": awy,
    }

def load_xplane_awy(text: str) -> int:
    """Заполняет AWY_GRAPH."""
    global AWY_EDGE_COUNT
    n = 0
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("1100 ") or line.startswith("Copyright") or line.startswith("Metadata"):
            continue
        if line.startswith("#"):
            continue
        rec = parse_awy_line(line)
        if not rec:
            continue
        a, b = rec["a"], rec["b"]
        awy, dirn, base, top = rec["awy"], rec["dir"], rec["base"], rec["top"]

        # direction: 1 = a->b, 2 = b->a, 3 = оба
        if dirn in (1, 3):
            AWY_GRAPH.setdefault(a, []).append({
                "to": b, "awy": awy, "dir": dirn, "base": base, "top": top
            })
            AWY_EDGE_COUNT += 1
        if dirn in (2, 3):
            AWY_GRAPH.setdefault(b, []).append({
                "to": a, "awy": awy, "dir": dirn, "base": base, "top": top
            })
            AWY_EDGE_COUNT += 1
        n += 1
    return n

def parse_nav_line(line: str) -> Optional[Tuple[str, float, float, str]]:
    """
    earth_nav.txt: строка вида
      2  50.0 N  30.0 E  113.10  VOR  IDENT  NAME ...
    Точнее: [code, lat, latH, lon, lonH, freq, type, ident, name...]
    """
    parts = line.split()
    if len(parts) < 8:
        return None
    try:
        code = int(parts[0])
    except ValueError:
        return None
    if code not in (2, 3, 4, 12, 13):
        return None
    try:
        lat = float(parts[1]); lon = float(parts[3])
    except ValueError:
        return None
    kind = parts[6].strip().upper()
    ident = parts[7].strip().upper()
    if not ident or len(ident) > 5:
        return None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    return (ident, lat, lon, kind)

def load_xplane_nav(text: str) -> int:
    """Добавляет navaids из earth_nav в FIX_COORDS (если ещё нет) и в FIX_INDEX."""
    n = 0
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("1100 ") or line.startswith("Copyright") or line.startswith("Metadata"):
            continue
        if line.startswith("#") or line.startswith("I"):
            continue
        res = parse_nav_line(line)
        if not res:
            continue
        ident, lat, lon, kind = res
        if ident not in FIX_COORDS:
            FIX_COORDS[ident] = (lat, lon)
            FIX_INDEX.setdefault(ident, []).append(len(FIX_COORDS) - 1)
        n += 1
    return n

def try_download(urls: List[str]) -> Optional[str]:
    for u in urls:
        txt = http_get_text(u, timeout=180)
        if txt and len(txt) > 1000:
            log(f"downloaded {u} ({len(txt)} bytes)")
            return txt
    return None

def load_xplane_all():
    """Главная функция загрузки X-Plane navdata с кешем."""
    global XPLANE_LOADED
    t0 = time.time()

    # 1) кеш
    if is_cache_fresh(AIRWAYS_CACHE):
        try:
            with open(AIRWAYS_CACHE, "r", encoding="utf-8") as f:
                cache = json.load(f)
            FIX_COORDS.clear()
            AWY_GRAPH.clear()
            for k, v in cache.get("fixes", {}).items():
                FIX_COORDS[k] = (v[0], v[1])
            for k, v in cache.get("awys", {}).items():
                AWY_GRAPH[k] = v
            XPLANE_LOADED = cache.get("xplane_loaded", True)
            LOAD_STATE["xplane"] = XPLANE_LOADED
            log(f"xplane cache loaded: {len(FIX_COORDS)} fixes, {len(AWY_GRAPH)} awy nodes")
            return
        except Exception as e:
            log(f"xplane cache read fail: {e}")

    # 2) скачиваем
    log("downloading earth_fix / earth_awy / earth_nav ...")
    txt_fix = try_download(NAVDATA_FILES["fix"])
    txt_awy = try_download(NAVDATA_FILES["awy"])
    txt_nav = try_download(NAVDATA_FILES["nav"])

    if not txt_fix or not txt_awy:
        log("xplane navdata NOT available, staying on fallback")
        return

    n_fix = load_xplane_fix(txt_fix)
    log(f"parsed fixes: {n_fix} lines, unique: {len(FIX_COORDS)}")

    n_awy = load_xplane_awy(txt_awy)
    log(f"parsed airways: {n_awy} lines, awy nodes: {len(AWY_GRAPH)}, edges: {AWY_EDGE_COUNT}")

    if txt_nav:
        n_nav = load_xplane_nav(txt_nav)
        log(f"parsed navaids from earth_nav: {n_nav} lines")

    XPLANE_LOADED = True
    LOAD_STATE["xplane"] = True

    # 3) кеш на диск
    try:
        cache = {
            "fixes": {k: [v[0], v[1]] for k, v in FIX_COORDS.items()},
            "awys": AWY_GRAPH,
            "xplane_loaded": True,
            "edges": AWY_EDGE_COUNT,
            "saved_at": datetime.now(timezone.utc).isoformat(),
        }
        log(f"writing {AIRWAYS_CACHE} ({len(FIX_COORDS)} fixes, {len(AWY_GRAPH)} awy)...")
        with open(AIRWAYS_CACHE, "w", encoding="utf-8") as f:
            json.dump(cache, f, separators=(",", ":"))
        log(f"xplane cache written in {time.time()-t0:.1f}s")
    except Exception as e:
        log(f"xplane cache write fail: {e}")

# =====================================================================
# A* ПО AIRWAY-ГРАФУ
# =====================================================================

def build_awy_neighbors(ident: str) -> List[Tuple[str, float, str, int, int]]:
    """
    Возвращает список (neighbor_ident, dist_nm, awy_name, base, top) для ident.
    Отбрасывает рёбра, для которых нет координат.
    """
    out = []
    here = FIX_COORDS.get(ident)
    if not here:
        return out
    lat1, lon1 = here
    for e in AWY_GRAPH.get(ident, []):
        to = e["to"]
        there = FIX_COORDS.get(to)
        if not there:
            continue
        lat2, lon2 = there
        d = haversine_nm(lat1, lon1, lat2, lon2)
        out.append((to, d, e["awy"], e["base"], e["top"]))
    return out

def find_nearest_fixes(lat: float, lon: float, limit: int = 8, max_nm: float = 80.0) -> List[Tuple[str, float]]:
    """Возвращает до `limit` ближайших fix'ов в радиусе max_nm."""
    out = []
    for ident, (flat, flon) in FIX_COORDS.items():
        d = haversine_nm(lat, lon, flat, flon)
        if d <= max_nm:
            out.append((ident, d))
    out.sort(key=lambda x: x[1])
    return out[:limit]

def astar_airways(start_lat, start_lon, end_lat, end_lon) -> Optional[List[Dict[str, Any]]]:
    """
    A* по airway-графу. Возвращает список waypoints (список dict с ident/lat/lon/awy)
    или None, если не удалось.
    """
    if not AWY_GRAPH or not FIX_COORDS:
        return None

    t0 = time.time()

    # ближайшие fix'ы к start и к end
    s_fixes = find_nearest_fixes(start_lat, start_lon, limit=10, max_nm=120.0)
    e_fixes = find_nearest_fixes(end_lat, end_lon, limit=10, max_nm=120.0)
    if not s_fixes or not e_fixes:
        log("astar_airways: no nearby fixes")
        return None

    e_set = {ident: d for ident, d in e_fixes}

    def h(ident: str) -> float:
        lat, lon = FIX_COORDS[ident]
        return haversine_nm(lat, lon, end_lat, end_lon)

    # priority queue: (f, g, ident, prev_ident, awy_name)
    openq = []
    for ident, d_start in s_fixes:
        g = d_start
        heapq.heappush(openq, (g + h(ident), g, ident, None, None))

    came_from: Dict[str, Tuple[Optional[str], Optional[str]]] = {}  # ident -> (prev, awy)
    g_score: Dict[str, float] = {}
    for ident, d_start in s_fixes:
        g_score[ident] = d_start

    visited = set()
    found = None
    expansions = 0
    MAX_EXPANSIONS = 200000

    while openq:
        f, g, cur, prev, awy = heapq.heappop(openq)
        if cur in visited:
            continue
        visited.add(cur)
        came_from[cur] = (prev, awy)
        expansions += 1
        if expansions > MAX_EXPANSIONS:
            log("astar_airways: MAX_EXPANSIONS reached")
            break

        if cur in e_set:
            # дошли до одного из fix'ов возле end
            found = cur
            break

        for to, d, awy_name, base, top in build_awy_neighbors(cur):
            if to in visited:
                continue
            tentative = g + d
            if tentative < g_score.get(to, float("inf")):
                g_score[to] = tentative
                heapq.heappush(openq, (tentative + h(to), tentative, to, cur, awy_name))

    if not found:
        log(f"astar_airways: no path, expansions={expansions}")
        return None

    # reconstruct
    path = []
    cur = found
    while cur is not None:
        prev, awy = came_from.get(cur, (None, None))
        path.append({"ident": cur, "awy": awy})
        cur = prev
    path.reverse()

    # превращаем в waypoints с координатами
    wps = []
    for p in path:
        ident = p["ident"]
        lat, lon = FIX_COORDS[ident]
        wps.append({
            "ident": ident,
            "lat": lat, "lon": lon,
            "awy": p.get("awy"),
        })

    log(f"astar_airways: OK, {len(wps)} wps, {expansions} expansions, {time.time()-t0:.2f}s")
    return wps

# =====================================================================
# A* ПО NAVAIDS (старый fallback) — упрощённый, как было
# =====================================================================

def build_navaid_graph():
    """Строит граф из NAVAIDS: каждый с 30 ближайшими в радиусе 200 NM."""
    global NAVAID_GRAPH
    if NAVAID_GRAPH:
        return
    if not NAVAIDS:
        return
    log("building navaid graph...")
    t0 = time.time()
    n = len(NAVAIDS)
    # простая сетка по широте/долготе для ускорения
    for i in range(n):
        a = NAVAIDS[i]
        # ищем ближайших линейно — медленно, но один раз
        pass
    # Для экономии времени: строим рёбра по сетке 2°×2°
    BUCKET = 2.0
    grid: Dict[Tuple[int, int], List[int]] = {}
    for i, nav in enumerate(NAVAIDS):
        gx = int(nav["lon"] // BUCKET)
        gy = int(nav["lat"] // BUCKET)
        grid.setdefault((gx, gy), []).append(i)
    for i, nav in enumerate(NAVAIDS):
        gx = int(nav["lon"] // BUCKET)
        gy = int(nav["lat"] // BUCKET)
        cand = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                cand.extend(grid.get((gx+dx, gy+dy), []))
        edges = []
        for j in cand:
            if i == j:
                continue
            b = NAVAIDS[j]
            d = haversine_nm(nav["lat"], nav["lon"], b["lat"], b["lon"])
            if d <= 250:
                edges.append((j, d))
        edges.sort(key=lambda x: x[1])
        NAVAID_GRAPH[i] = edges[:20]
    log(f"navaid graph built: {len(NAVAID_GRAPH)} nodes, {time.time()-t0:.1f}s")

def astar_navaids(start_lat, start_lon, end_lat, end_lon) -> Optional[List[Dict[str, Any]]]:
    if not NAVAIDS:
        return None
    build_navaid_graph()
    if not NAVAID_GRAPH:
        return None

    def nearest_nav(lat, lon, k=5):
        scored = []
        for idx, nav in enumerate(NAVAIDS):
            d = haversine_nm(lat, lon, nav["lat"], nav["lon"])
            scored.append((d, idx))
        scored.sort()
        return scored[:k]

    s = nearest_nav(start_lat, start_lon)
    e = nearest_nav(end_lat, end_lon)
    if not s or not e:
        return None
    e_idx = {idx for _, idx in e}

    def h(idx):
        nav = NAVAIDS[idx]
        return haversine_nm(nav["lat"], nav["lon"], end_lat, end_lon)

    openq = []
    for d, idx in s:
        heapq.heappush(openq, (d + h(idx), d, idx, None))
    came = {}
    gsc = {idx: d for d, idx in s}
    visited = set()
    found = None
    expansions = 0
    MAXE = 100000
    while openq:
        f, g, cur, prev = heapq.heappop(openq)
        if cur in visited:
            continue
        visited.add(cur)
        came[cur] = prev
        expansions += 1
        if expansions > MAXE:
            break
        if cur in e_idx:
            found = cur
            break
        for nxt, d in NAVAID_GRAPH.get(cur, []):
            if nxt in visited:
                continue
            t = g + d
            if t < gsc.get(nxt, float("inf")):
                gsc[nxt] = t
                heapq.heappush(openq, (t + h(nxt), t, nxt, cur))
    if not found:
        return None
    path = []
    cur = found
    while cur is not None:
        nav = NAVAIDS[cur]
        path.append({"ident": nav["ident"], "lat": nav["lat"], "lon": nav["lon"], "awy": None})
        cur = came.get(cur)
    path.reverse()
    return path

# =====================================================================
# MANUAL ROUTES
# =====================================================================

MANUAL_ROUTES: Dict[Tuple[str, str], List[Dict[str, Any]]] = {
    ("ULLI", "UUWW"): [
        {"ident": "ULLI", "lat": 59.8003, "lon": 30.2625, "awy": None},
        {"ident": "DEDUM", "lat": 59.8333, "lon": 30.5000, "awy": "W1"},
        {"ident": "BANEV", "lat": 59.5000, "lon": 31.5000, "awy": "W1"},
        {"ident": "LATLA", "lat": 58.5000, "lon": 32.5000, "awy": "W1"},
        {"ident": "RUSAL", "lat": 57.5000, "lon": 33.5000, "awy": "W1"},
        {"ident": "BETAL", "lat": 56.5000, "lon": 34.5000, "awy": "W1"},
        {"ident": "UUWW", "lat": 55.5915, "lon": 37.2615, "awy": None},
    ],
    ("KJFK", "OMDB"): [
        {"ident": "KJFK", "lat": 40.6413, "lon": -73.7781, "awy": None},
        {"ident": "BETHA", "lat": 41.0000, "lon": -71.5000, "awy": "J80"},
        {"ident": "SCUPP", "lat": 42.0000, "lon": -68.0000, "awy": "J80"},
        {"ident": "TUSKY", "lat": 44.0000, "lon": -60.0000, "awy": "J80"},
        {"ident": "NATA", "lat": 48.0000, "lon": -40.0000, "awy": "NAT"},
        {"ident": "BEXET", "lat": 51.0000, "lon": -20.0000, "awy": "NAT"},
        {"ident": "DEGOS", "lat": 53.0000, "lon": -10.0000, "awy": "NAT"},
        {"ident": "OMDB", "lat": 25.2528, "lon": 55.3644, "awy": None},
    ],
    ("EDDF", "EDDM"): [
        {"ident": "EDDF", "lat": 50.0379, "lon": 8.5622, "awy": None},
        {"ident": "MARUN", "lat": 50.0000, "lon": 9.0000, "awy": "UL603"},
        {"ident": "GED", "lat": 49.5000, "lon": 9.5000, "awy": "UL603"},
        {"ident": "EDDM", "lat": 48.3538, "lon": 11.7861, "awy": None},
    ],
    ("EGLL", "LFPG"): [
        {"ident": "EGLL", "lat": 51.4700, "lon": -0.4543, "awy": None},
        {"ident": "DVR", "lat": 51.1625, "lon": 1.3592, "awy": "UL9"},
        {"ident": "KONAN", "lat": 50.5000, "lon": 2.5000, "awy": "UL9"},
        {"ident": "LFPG", "lat": 49.0097, "lon": 2.5479, "awy": None},
    ],
    ("KJFK", "KLAX"): [
        {"ident": "KJFK", "lat": 40.6413, "lon": -73.7781, "awy": None},
        {"ident": "HAARP", "lat": 41.0000, "lon": -76.0000, "awy": "J80"},
        {"ident": "EMPTY", "lat": 41.5000, "lon": -80.0000, "awy": "J80"},
        {"ident": "JOT", "lat": 41.5000, "lon": -88.0000, "awy": "J80"},
        {"ident": "OBK", "lat": 42.0000, "lon": -95.0000, "awy": "J80"},
        {"ident": "SAYRE", "lat": 41.0000, "lon": -103.0000, "awy": "J80"},
        {"ident": "DAG", "lat": 34.8500, "lon": -116.8000, "awy": "J80"},
        {"ident": "KLAX", "lat": 33.9416, "lon": -118.4085, "awy": None},
    ],
}

# =====================================================================
# SID / STAR (как было — оставляем словарь, логика ниже)
# =====================================================================

SID_STAR: Dict[str, Dict[str, str]] = {
    "UUEE": {"sid": "SID via LIDMO", "star": "STAR via GILUK"},
    "ULLI": {"sid": "SID via DEDUM", "star": "STAR via LATLA"},
    "UUWW": {"sid": "SID via BUTER", "star": "STAR via LUKAL"},
    "UUDD": {"sid": "SID via BUTER", "star": "STAR via LUKAL"},
    "URSS": {"sid": "SID via GUKSU", "star": "STAR via REBLO"},
    "EDDF": {"sid": "SID via MARUN", "star": "STAR via GED"},
    "EDDM": {"sid": "SID via GED", "star": "STAR via MARUN"},
    "EDDB": {"sid": "SID via BRANE", "star": "STAR via LUBEN"},
    "EGLL": {"sid": "SID via DVR", "star": "STAR via OCK"},
    "EGKK": {"sid": "SID via CLN", "star": "STAR via MAY"},
    "LFPG": {"sid": "SID via KONAN", "star": "STAR via MOPIL"},
    "EHAM": {"sid": "SID via ARNEM", "star": "STAR via RIVER"},
    "LIRF": {"sid": "SID via TAQ", "star": "STAR via OST"},
    "LEMD": {"sid": "SID via BARDI", "star": "STAR via RBO"},
    "LSZH": {"sid": "SID via DEGES", "star": "STAR via GIPOL"},
    "LOWW": {"sid": "SID via STEIN", "star": "STAR via NEMAL"},
    "EKCH": {"sid": "SID via VEDAR", "star": "STAR via LANGO"},
    "ENGM": {"sid": "SID via MASEV", "star": "STAR via ROGAL"},
    "ESSA": {"sid": "SID via NILUG", "star": "STAR via ELTOK"},
    "EFHK": {"sid": "SID via VEPIN", "star": "STAR via ROKAN"},
    "EPWA": {"sid": "SID via OSMUL", "star": "STAR via LOGDA"},
    "LKPR": {"sid": "SID via BALTU", "star": "STAR via LOMKI"},
    "LHBP": {"sid": "SID via BUKTA", "star": "STAR via LITVA"},
    "LGAV": {"sid": "SID via KEA", "star": "STAR via ROPOX"},
    "OMDB": {"sid": "SID via DEDUM", "star": "STAR via DESDI"},
    "OTHH": {"sid": "SID via BUNDU", "star": "STAR via ALBAB"},
    "WSSS": {"sid": "SID via ANITO", "star": "STAR via TOPAL"},
    "VHHH": {"sid": "SID via BEKOL", "star": "STAR via SOKOE"},
    "RJTT": {"sid": "SID via XAC", "star": "STAR via OCEAN"},
    "ZBAA": {"sid": "SID via RUSDO", "star": "STAR via LADIX"},
    "KJFK": {"sid": "SID via BETHA", "star": "STAR via HAARP"},
    "KLAX": {"sid": "SID via DAG", "star": "STAR via SAYRE"},
    "KORD": {"sid": "SID via JOT", "star": "STAR via OBK"},
    "KATL": {"sid": "SID via ATL", "star": "STAR via ATL"},
    "CYYZ": {"sid": "SID via YYZ", "star": "STAR via YYZ"},
}

def get_sid_star(icao: str) -> Dict[str, str]:
    icao = icao.upper()
    if icao in SID_STAR:
        return SID_STAR[icao]
    return {"sid": f"SID {icao}", "star": f"STAR {icao}"}

# =====================================================================
# METAR
# =====================================================================

METAR_URL = "https://aviationweather.gov/api/data/metar?ids={icao}&format=raw&taf=false"

def fetch_metar(icao: str) -> Optional[str]:
    try:
        url = METAR_URL.format(icao=icao.upper())
        req = urllib.request.Request(url, headers={"User-Agent": "FlyBrief/0.7"})
        with urllib.request.urlopen(req, timeout=20) as r:
            txt = r.read().decode("utf-8", errors="replace").strip()
        return txt or None
    except Exception as e:
        log(f"metar fail {icao}: {e}")
        return None

# =====================================================================
# РАСЧЁТ МАРШРУТА — ядро
# =====================================================================

def airport_coord(icao: str) -> Optional[Tuple[float, float]]:
    icao = icao.upper()
    a = AIRPORTS.get(icao)
    if a:
        return (a["lat"], a["lon"])
    return None

def gc_route(dep_lat, dep_lon, arr_lat, arr_lon) -> List[Dict[str, Any]]:
    """Простая Great Circle: dep → (промежуточные точки) → arr."""
    d = haversine_nm(dep_lat, dep_lon, arr_lat, arr_lon)
    n = max(2, int(d // 300) + 2)
    pts = []
    for i in range(n):
        t = i / (n - 1)
        lat = dep_lat + (arr_lat - dep_lat) * t
        lon = dep_lon + (arr_lon - dep_lon) * t
        pts.append({"ident": f"GC{i:02d}", "lat": lat, "lon": lon, "awy": None})
    return pts

def calc_route(dep: str, arr: str) -> Dict[str, Any]:
    dep = dep.upper(); arr = arr.upper()

    dep_c = airport_coord(dep)
    arr_c = airport_coord(arr)
    if not dep_c or not arr_c:
        raise HTTPException(400, f"unknown airport: {dep if not dep_c else arr}")

    dep_lat, dep_lon = dep_c
    arr_lat, arr_lon = arr_c

    # 1) MANUAL
    manual = MANUAL_ROUTES.get((dep, arr)) or MANUAL_ROUTES.get((arr, dep))
    if manual:
        route = list(manual) if (dep, arr) in MANUAL_ROUTES else list(reversed(manual))
        return {
            "source": "manual",
            "dep": dep, "arr": arr,
            "points": route,
            "distance_nm": sum(
                haversine_nm(route[i]["lat"], route[i]["lon"], route[i+1]["lat"], route[i+1]["lon"])
                for i in range(len(route)-1)
            ),
            "sid_star": {"dep": get_sid_star(dep), "arr": get_sid_star(arr)},
        }

    # 2) AIRWAYS-A*
    if XPLANE_LOADED and AWY_GRAPH:
        wps = astar_airways(dep_lat, dep_lon, arr_lat, arr_lon)
        if wps and len(wps) >= 2:
            # вставляем dep/arr по краям
            points = [{"ident": dep, "lat": dep_lat, "lon": dep_lon, "awy": None}]
            points.extend(wps)
            points.append({"ident": arr, "lat": arr_lat, "lon": arr_lon, "awy": None})
            return {
                "source": "airways",
                "dep": dep, "arr": arr,
                "points": points,
                "distance_nm": sum(
                    haversine_nm(points[i]["lat"], points[i]["lon"], points[i+1]["lat"], points[i+1]["lon"])
                    for i in range(len(points)-1)
                ),
                "sid_star": {"dep": get_sid_star(dep), "arr": get_sid_star(arr)},
            }

    # 3) NAVAIDS-A*
    if NAVAIDS:
        wps = astar_navaids(dep_lat, dep_lon, arr_lat, arr_lon)
        if wps and len(wps) >= 2:
            points = [{"ident": dep, "lat": dep_lat, "lon": dep_lon, "awy": None}]
            points.extend(wps)
            points.append({"ident": arr, "lat": arr_lat, "lon": arr_lon, "awy": None})
            return {
                "source": "navaids",
                "dep": dep, "arr": arr,
                "points": points,
                "distance_nm": sum(
                    haversine_nm(points[i]["lat"], points[i]["lon"], points[i+1]["lat"], points[i+1]["lon"])
                    for i in range(len(points)-1)
                ),
                "sid_star": {"dep": get_sid_star(dep), "arr": get_sid_star(arr)},
            }

    # 4) GC fallback
    points = gc_route(dep_lat, dep_lon, arr_lat, arr_lon)
    points[0]["ident"] = dep
    points[-1]["ident"] = arr
    return {
        "source": "gc",
        "dep": dep, "arr": arr,
        "points": points,
        "distance_nm": haversine_nm(dep_lat, dep_lon, arr_lat, arr_lon),
        "sid_star": {"dep": get_sid_star(dep), "arr": get_sid_star(arr)},
    }

# =====================================================================
# МОДЕЛИ ЗАПРОСОВ
# =====================================================================

class CalcReq(BaseModel):
    dep: str
    arr: str
    ac: Optional[str] = "A320"
    pax: Optional[int] = 150
    cargo: Optional[float] = 0

# =====================================================================
# ЭНДПОИНТЫ
# =====================================================================

@app.on_event("startup")
async def _startup():
    LOAD_STATE["started_at"] = datetime.now(timezone.utc).isoformat()
    log(f"FlyBrief v{APP_VERSION} starting...")
    try:
        load_airports()
    except Exception as e:
        LOAD_STATE["error"] = f"airports: {e}"; log(LOAD_STATE["error"])
    try:
        load_runways()
    except Exception as e:
        LOAD_STATE["error"] = f"runways: {e}"; log(LOAD_STATE["error"])
    try:
        load_navaids()
    except Exception as e:
        LOAD_STATE["error"] = f"navaids: {e}"; log(LOAD_STATE["error"])
    try:
        load_xplane_all()
    except Exception as e:
        LOAD_STATE["error"] = f"xplane: {e}"; log(LOAD_STATE["error"])
    LOAD_STATE["finished_at"] = datetime.now(timezone.utc).isoformat()
    log("startup done")

@app.get("/", response_class=HTMLResponse)
async def index():
    html_path = os.path.join(CACHE_DIR, "indexfly.html")
    if os.path.exists(html_path):
        with open(html_path, "r", encoding="utf-8") as f:
            return HTMLResponse(f.read())
    return HTMLResponse("<h1>FlyBrief v0.7.0</h1><p>indexfly.html not found</p>")

@app.get("/airports/stats")
async def stats():
    return {
        "version": APP_VERSION,
        "airports": len(AIRPORTS),
        "runways": sum(len(v) for v in RUNWAYS.values()),
        "navaids": len(NAVAIDS),
        "fixes": len(FIX_COORDS),
        "airways": len(AWY_GRAPH),
        "graph_nodes": len(AWY_GRAPH) if AWY_GRAPH else len(NAVAID_GRAPH),
        "graph_edges": AWY_EDGE_COUNT if AWY_EDGE_COUNT else sum(len(v) for v in NAVAID_GRAPH.values()),
        "xplane_loaded": XPLANE_LOADED,
        "navigraph_enabled": False,
        "fpdb_enabled": False,
        "load_state": LOAD_STATE,
    }

@app.get("/airports/search")
async def search(q: str = Query(..., min_length=1)):
    q = q.upper()
    out = []
    for icao, a in AIRPORTS.items():
        if q in icao or q in a.get("iata", "") or q in a.get("name", "").upper():
            out.append({"ident": icao, "name": a["name"], "iata": a.get("iata"),
                        "lat": a["lat"], "lon": a["lon"]})
        if len(out) >= 30:
            break
    return out

@app.get("/runways")
async def runways(icao: str):
    icao = icao.upper()
    if icao not in RUNWAYS:
        return {"icao": icao, "runways": []}
    return {"icao": icao, "runways": RUNWAYS[icao]}

@app.get("/charts")
async def charts(icao: str):
    icao = icao.upper()
    # у нас нет реальных чартов, отдаём заглушку с ссылкой на SkyVector
    return {
        "icao": icao,
        "charts": [
            {"name": f"{icao} — SkyVector", "url": f"https://skyvector.com/airport/{icao}"},
            {"name": f"{icao} — AirNav", "url": f"https://www.airnav.com/airport/{icao}"},
        ],
    }

@app.get("/weather")
async def weather(icao: str):
    m = fetch_metar(icao)
    if not m:
        return {"icao": icao.upper(), "metar": None, "error": "no data"}
    return {"icao": icao.upper(), "metar": m}

@app.post("/calculate")
async def calculate(req: CalcReq):
    r = calc_route(req.dep, req.arr)
    return r

@app.get("/calculate")
async def calculate_get(dep: str, arr: str, ac: str = "A320", pax: int = 150, cargo: float = 0):
    r = calc_route(dep, arr)
    return r

@app.get("/download_pln")
async def download_pln(dep: str, arr: str):
    r = calc_route(dep, arr)
    pts = r["points"]
    lines = []
    lines.append(f"; FlyBrief PLN export")
    lines.append(f"; {dep} -> {arr}")
    lines.append(f"; source: {r['source']}")
    lines.append("")
    lines.append("[FlightPlan]")
    lines.append(f"title={dep} to {arr}")
    lines.append("type=IFR")
    lines.append(f"departure_id={dep}")
    lines.append(f"destination_id={arr}")
    lines.append("")
    lines.append("[Waypoints]")
    for p in pts:
        lines.append(f"{p['ident']} {p['lat']:.5f} {p['lon']:.5f}")
    return PlainTextResponse("\n".join(lines), headers={
        "Content-Disposition": f"attachment; filename={dep}-{arr}.pln"
    })

# =====================================================================
# ЗАПУСК (для локального теста; на Render запускается через uvicorn)
# =====================================================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
