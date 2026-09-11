# FlyBrief main.py v0.7.1
# X-Plane 12 navdata (airways/fixes/navaids) + A* по airway-графу + OFP
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
from fastapi.responses import HTMLResponse, PlainTextResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# =====================================================================
# КОНФИГ
# =====================================================================

APP_VERSION = "0.7.1"

REPO_RAW = "https://raw.githubusercontent.com/barsikone1/my-simbrief/main"
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

AIRPORTS: Dict[str, Dict[str, Any]] = {}
RUNWAYS:  Dict[str, List[Dict[str, Any]]] = {}
NAVAIDS:  List[Dict[str, Any]] = []
NAVAID_INDEX: Dict[str, List[int]] = {}
NAVAID_GRAPH: Dict[int, List[Tuple[int, float]]] = {}

FIX_COORDS: Dict[str, Tuple[float, float]] = {}
FIX_INDEX:  Dict[str, List[int]] = {}
AWY_GRAPH:  Dict[str, List[Dict[str, Any]]] = {}
AWY_EDGE_COUNT = 0

XPLANE_LOADED = False
LOAD_STATE = {
    "airports": False, "runways": False, "navaids": False, "xplane": False,
    "started_at": None, "finished_at": None, "error": None,
}

# =====================================================================
# FASTAPI
# =====================================================================

app = FastAPI(title="FlyBrief", version=APP_VERSION)
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])

# =====================================================================
# УТИЛИТЫ
# =====================================================================

def log(*a):
    print("[FlyBrief]", *a, flush=True)

EARTH_R_NM = 3440.065

def haversine_nm(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return 2 * EARTH_R_NM * math.asin(min(1.0, math.sqrt(a)))

def is_cache_fresh(path: str) -> bool:
    if not os.path.exists(path):
        return False
    return (time.time() - os.path.getmtime(path)) < CACHE_TTL_DAYS * 86400

def http_get_text(url: str, timeout: int = 60) -> Optional[str]:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "FlyBrief/0.7"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = r.read()
        return data.decode("utf-8", errors="replace")
    except Exception as e:
        log(f"http_get_text FAIL {url}: {e}")
        return None

# =====================================================================
# OurAirports CSV
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
            "ident": ident, "name": row.get("name", ""),
            "lat": lat, "lon": lon, "type": row.get("type", ""),
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
        # левый конец
        try:
            le_lat = float(row["le_latitude_deg"]); le_lon = float(row["le_longitude_deg"])
        except Exception:
            le_lat = le_lon = None
        # правый конец
        try:
            he_lat = float(row["he_latitude_deg"]); he_lon = float(row["he_longitude_deg"])
        except Exception:
            he_lat = he_lon = None
        try:
            hdg = float(row.get("le_heading_degT") or 0)
        except Exception:
            hdg = 0.0
        try:
            length = float(row.get("length_ft") or 0)
        except Exception:
            length = 0.0
        le_ident = (row.get("le_ident") or "").strip()
        he_ident = (row.get("he_ident") or "").strip()

        # Каждая физическая полоса имеет два конца — даём обе стороны
        if le_ident:
            result.setdefault(icao, []).append({
                "ident": le_ident,
                "lat": le_lat, "lon": le_lon,
                "hdg": hdg, "len_ft": length,
                "pair": he_ident,
            })
        if he_ident:
            # heading правого конца: +180 от левого (нормализуем 0-360)
            he_hdg = (hdg + 180.0) % 360.0
            result.setdefault(icao, []).append({
                "ident": he_ident,
                "lat": he_lat, "lon": he_lon,
                "hdg": he_hdg, "len_ft": length,
                "pair": le_ident,
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
            "type": row.get("type", ""), "name": row.get("name", ""),
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

def parse_fix_line(line: str):
    parts = line.split()
    if len(parts) < 3:
        return None
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
    n = 0
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if len(line) < 5 and line[0] in ("I", "A", "N"):
            continue
        if line.startswith(("1100 ", "Copyright", "Metadata", "#")):
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

def parse_awy_line(line: str):
    parts = line.split()
    if len(parts) < 11:
        return None
    fix_a = parts[0].upper()
    fix_b = parts[3].upper()
    try:
        dirn = int(parts[7]); base = int(parts[8]); top = int(parts[9])
    except ValueError:
        return None
    awy = parts[10].upper()
    if not fix_a or not fix_b or not awy:
        return None
    return {"a": fix_a, "b": fix_b, "dir": dirn, "base": base, "top": top, "awy": awy}

def load_xplane_awy(text: str) -> int:
    global AWY_EDGE_COUNT
    n = 0
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith(("1100 ", "Copyright", "Metadata", "#")):
            continue
        rec = parse_awy_line(line)
        if not rec:
            continue
        a, b = rec["a"], rec["b"]
        awy, dirn = rec["awy"], rec["dir"]
        base, top = rec["base"], rec["top"]
        if dirn in (1, 3):
            AWY_GRAPH.setdefault(a, []).append(
                {"to": b, "awy": awy, "dir": dirn, "base": base, "top": top})
            AWY_EDGE_COUNT += 1
        if dirn in (2, 3):
            AWY_GRAPH.setdefault(b, []).append(
                {"to": a, "awy": awy, "dir": dirn, "base": base, "top": top})
            AWY_EDGE_COUNT += 1
        n += 1
    return n

def parse_nav_line(line: str):
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
    n = 0
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith(("1100 ", "Copyright", "Metadata", "#", "I")):
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

def try_download(urls):
    for u in urls:
        txt = http_get_text(u, timeout=180)
        if txt and len(txt) > 1000:
            log(f"downloaded {u} ({len(txt)} bytes)")
            return txt
    return None

def load_xplane_all():
    global XPLANE_LOADED
    t0 = time.time()

    if is_cache_fresh(AIRWAYS_CACHE):
        try:
            with open(AIRWAYS_CACHE, "r", encoding="utf-8") as f:
                cache = json.load(f)
            FIX_COORDS.clear(); AWY_GRAPH.clear()
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

def build_awy_neighbors(ident):
    out = []
    here = FIX_COORDS.get(ident)
    if not here:
        return out
    lat1, lon1 = here
    for e in AWY_GRAPH.get(ident, []):
        there = FIX_COORDS.get(e["to"])
        if not there:
            continue
        d = haversine_nm(lat1, lon1, there[0], there[1])
        out.append((e["to"], d, e["awy"], e["base"], e["top"]))
    return out

def find_nearest_fixes(lat, lon, limit=8, max_nm=80.0):
    out = []
    for ident, (flat, flon) in FIX_COORDS.items():
        d = haversine_nm(lat, lon, flat, flon)
        if d <= max_nm:
            out.append((ident, d))
    out.sort(key=lambda x: x[1])
    return out[:limit]

def astar_airways(start_lat, start_lon, end_lat, end_lon):
    if not AWY_GRAPH or not FIX_COORDS:
        return None
    t0 = time.time()
    s_fixes = find_nearest_fixes(start_lat, start_lon, 10, 120.0)
    e_fixes = find_nearest_fixes(end_lat, end_lon, 10, 120.0)
    if not s_fixes or not e_fixes:
        log("astar_airways: no nearby fixes")
        return None
    e_set = {ident: d for ident, d in e_fixes}

    def h(ident):
        lat, lon = FIX_COORDS[ident]
        return haversine_nm(lat, lon, end_lat, end_lon)

    openq = []
    for ident, d_start in s_fixes:
        heapq.heappush(openq, (d_start + h(ident), d_start, ident, None, None))
    came_from = {}
    g_score = {ident: d for ident, d in s_fixes}
    visited = set()
    found = None
    expansions = 0
    MAX_EXP = 200000

    while openq:
        f, g, cur, prev, awy = heapq.heappop(openq)
        if cur in visited:
            continue
        visited.add(cur)
        came_from[cur] = (prev, awy)
        expansions += 1
        if expansions > MAX_EXP:
            log("astar_airways: MAX_EXPANSIONS")
            break
        if cur in e_set:
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

    path = []
    cur = found
    while cur is not None:
        prev, awy = came_from.get(cur, (None, None))
        path.append({"ident": cur, "awy": awy})
        cur = prev
    path.reverse()

    wps = []
    for p in path:
        lat, lon = FIX_COORDS[p["ident"]]
        wps.append({"ident": p["ident"], "lat": lat, "lon": lon, "awy": p.get("awy")})

    log(f"astar_airways: OK {len(wps)} wps, {expansions} exp, {time.time()-t0:.2f}s")
    return wps

# =====================================================================
# A* ПО NAVAIDS (fallback)
# =====================================================================

def build_navaid_graph():
    global NAVAID_GRAPH
    if NAVAID_GRAPH or not NAVAIDS:
        return
    log("building navaid graph...")
    t0 = time.time()
    BUCKET = 2.0
    grid = {}
    for i, nav in enumerate(NAVAIDS):
        gx = int(nav["lon"] // BUCKET); gy = int(nav["lat"] // BUCKET)
        grid.setdefault((gx, gy), []).append(i)
    for i, nav in enumerate(NAVAIDS):
        gx = int(nav["lon"] // BUCKET); gy = int(nav["lat"] // BUCKET)
        cand = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                cand.extend(grid.get((gx+dx, gy+dy), []))
        edges = []
        for j in cand:
            if i == j: continue
            b = NAVAIDS[j]
            d = haversine_nm(nav["lat"], nav["lon"], b["lat"], b["lon"])
            if d <= 250:
                edges.append((j, d))
        edges.sort(key=lambda x: x[1])
        NAVAID_GRAPH[i] = edges[:20]
    log(f"navaid graph built: {len(NAVAID_GRAPH)} nodes, {time.time()-t0:.1f}s")

def astar_navaids(start_lat, start_lon, end_lat, end_lon):
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
        if cur in visited: continue
        visited.add(cur); came[cur] = prev
        expansions += 1
        if expansions > MAXE: break
        if cur in e_idx:
            found = cur; break
        for nxt, d in NAVAID_GRAPH.get(cur, []):
            if nxt in visited: continue
            t = g + d
            if t < gsc.get(nxt, float("inf")):
                gsc[nxt] = t
                heapq.heappush(openq, (t + h(nxt), t, nxt, cur))
    if not found: return None
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

MANUAL_ROUTES = {
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
# SID / STAR
# =====================================================================

SID_STAR = {
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

def get_sid_star(icao):
    icao = icao.upper()
    if icao in SID_STAR:
        return SID_STAR[icao]
    return {"sid": f"SID {icao}", "star": f"STAR {icao}"}

# =====================================================================
# METAR
# =====================================================================

def fetch_metar(icao):
    try:
        url = f"https://aviationweather.gov/api/data/metar?ids={icao.upper()}&format=raw&taf=false"
        req = urllib.request.Request(url, headers={"User-Agent": "FlyBrief/0.7"})
        with urllib.request.urlopen(req, timeout=20) as r:
            txt = r.read().decode("utf-8", errors="replace").strip()
        return txt or None
    except Exception as e:
        log(f"metar fail {icao}: {e}")
        return None

# =====================================================================
# РАСЧЁТ МАРШРУТА
# =====================================================================

def airport_coord(icao):
    a = AIRPORTS.get(icao.upper())
    return (a["lat"], a["lon"]) if a else None

def gc_route(dep_lat, dep_lon, arr_lat, arr_lon):
    d = haversine_nm(dep_lat, dep_lon, arr_lat, arr_lon)
    n = max(2, int(d // 300) + 2)
    pts = []
    for i in range(n):
        t = i / (n - 1)
        lat = dep_lat + (arr_lat - dep_lat) * t
        lon = dep_lon + (arr_lon - dep_lon) * t
        pts.append({"ident": f"GC{i:02d}", "lat": lat, "lon": lon, "awy": None})
    return pts

def fmt_hhmm(distance_nm, gs_kts):
    if gs_kts <= 0:
        return "--:--"
    hours = distance_nm / gs_kts
    h = int(hours); m = int(round((hours - h) * 60))
    if m == 60: h += 1; m = 0
    return f"{h:02d}:{m:02d}"

def build_ofp(dep, arr, route, ac="A320", pax=150, cargo=0.0):
    pts = route["points"]; dist = route["distance_nm"]; src = route["source"]
    gs_map = {
        "A320": 450, "A321": 450, "A319": 450, "A330": 480, "A350": 490,
        "B737": 450, "B738": 450, "B739": 450, "B747": 490, "B777": 490,
        "B787": 490, "E190": 430, "CRJ9": 430, "AT72": 270,
    }
    gs = gs_map.get(ac.upper(), 450)
    trip_fuel = dist * 12.0
    taxi_fuel = 200.0
    contingency = trip_fuel * 0.05
    alternate_fuel = 800.0
    final_reserve = 1200.0
    block_fuel = trip_fuel + taxi_fuel + contingency + alternate_fuel + final_reserve
    oew = 42000.0
    payload = pax * 84.0 + cargo
    zfw = oew + payload
    tow = zfw + block_fuel
    lw = tow - trip_fuel * 0.9
    etd = datetime.now(timezone.utc)
    ete_str = fmt_hhmm(dist, gs)
    hh, mm = map(int, ete_str.split(":"))
    eta = etd.timestamp() + hh*3600 + mm*60
    eta_dt = datetime.fromtimestamp(eta, tz=timezone.utc)
    sid_star = route.get("sid_star", {})
    dep_sid = sid_star.get("dep", {}).get("sid", "")
    arr_star = sid_star.get("arr", {}).get("star", "")

    L = []
    L.append("=" * 78)
    L.append(f"  FLYBRIEF OFP — {dep} → {arr}")
    L.append("=" * 78)
    L.append(f"  Aircraft : {ac}")
    L.append(f"  Pax      : {pax}   Cargo: {cargo:.0f} kg")
    L.append(f"  Route src: {src.upper()}")
    L.append(f"  Distance : {dist:.0f} NM")
    L.append(f"  Cruise GS: {gs} kts")
    L.append(f"  ETD (UTC): {etd.strftime('%Y-%m-%d %H:%M')}")
    L.append(f"  ETE      : {ete_str}")
    L.append(f"  ETA (UTC): {eta_dt.strftime('%Y-%m-%d %H:%M')}")
    L.append("")
    L.append("-" * 78)
    L.append("  ROUTE")
    L.append("-" * 78)
    if dep_sid:
        L.append(f"  {dep}  {dep_sid}")
    for p in pts:
        awy = p.get("awy")
        tag = f" [{awy}]" if awy else ""
        L.append(f"  {p['ident']:<8}{tag:<12}  {p['lat']:>9.4f}  {p['lon']:>10.4f}")
    if arr_star:
        L.append(f"  {arr}  {arr_star}")
    L.append("")
    L.append("-" * 78)
    L.append("  FUEL BREAKDOWN")
    L.append("-" * 78)
    L.append(f"  Taxi         : {taxi_fuel:>8.0f} kg")
    L.append(f"  Trip         : {trip_fuel:>8.0f} kg")
    L.append(f"  Contingency  : {contingency:>8.0f} kg")
    L.append(f"  Alternate    : {alternate_fuel:>8.0f} kg")
    L.append(f"  Final reserve: {final_reserve:>8.0f} kg")
    L.append(f"  BLOCK FUEL   : {block_fuel:>8.0f} kg")
    L.append("")
    L.append("-" * 78)
    L.append("  WEIGHTS")
    L.append("-" * 78)
    L.append(f"  OEW          : {oew:>8.0f} kg")
    L.append(f"  Payload      : {payload:>8.0f} kg")
    L.append(f"  ZFW          : {zfw:>8.0f} kg")
    L.append(f"  TOW          : {tow:>8.0f} kg")
    L.append(f"  LW           : {lw:>8.0f} kg")
    L.append("=" * 78)
    L.append(f"  Generated by FlyBrief v{APP_VERSION}")
    L.append("=" * 78)
    return "\n".join(L)

def calc_route(dep, arr, ac="A320", pax=150, cargo=0.0,
               dep_rwy=None, arr_rwy=None):
    dep = dep.upper(); arr = arr.upper()
    dep_c = airport_coord(dep); arr_c = airport_coord(arr)
    if not dep_c or not arr_c:
        raise HTTPException(400, f"unknown airport: {dep if not dep_c else arr}")
    dep_lat, dep_lon = dep_c
    arr_lat, arr_lon = arr_c
    result = None

    manual = MANUAL_ROUTES.get((dep, arr)) or MANUAL_ROUTES.get((arr, dep))
    if manual:
        route = list(manual) if (dep, arr) in MANUAL_ROUTES else list(reversed(manual))
        result = {"source": "manual", "dep": dep, "arr": arr, "points": route,
                  "distance_nm": sum(haversine_nm(route[i]["lat"], route[i]["lon"],
                                                  route[i+1]["lat"], route[i+1]["lon"])
                                     for i in range(len(route)-1))}

    if result is None and XPLANE_LOADED and AWY_GRAPH:
        wps = astar_airways(dep_lat, dep_lon, arr_lat, arr_lon)
        if wps and len(wps) >= 2:
            points = [{"ident": dep, "lat": dep_lat, "lon": dep_lon, "awy": None}]
            points.extend(wps)
            points.append({"ident": arr, "lat": arr_lat, "lon": arr_lon, "awy": None})
            result = {"source": "airways", "dep": dep, "arr": arr, "points": points,
                      "distance_nm": sum(haversine_nm(points[i]["lat"], points[i]["lon"],
                                                      points[i+1]["lat"], points[i+1]["lon"])
                                         for i in range(len(points)-1))}

    if result is None and NAVAIDS:
        wps = astar_navaids(dep_lat, dep_lon, arr_lat, arr_lon)
        if wps and len(wps) >= 2:
            points = [{"ident": dep, "lat": dep_lat, "lon": dep_lon, "awy": None}]
            points.extend(wps)
            points.append({"ident": arr, "lat": arr_lat, "lon": arr_lon, "awy": None})
            result = {"source": "navaids", "dep": dep, "arr": arr, "points": points,
                      "distance_nm": sum(haversine_nm(points[i]["lat"], points[i]["lon"],
                                                      points[i+1]["lat"], points[i+1]["lon"])
                                         for i in range(len(points)-1))}

    if result is None:
        points = gc_route(dep_lat, dep_lon, arr_lat, arr_lon)
        points[0]["ident"] = dep
        points[-1]["ident"] = arr
        result = {"source": "gc", "dep": dep, "arr": arr, "points": points,
                  "distance_nm": haversine_nm(dep_lat, dep_lon, arr_lat, arr_lon)}

    result["sid_star"] = {"dep": get_sid_star(dep), "arr": get_sid_star(arr)}
    result["dep_rwy"] = dep_rwy
    result["arr_rwy"] = arr_rwy
    result["ofp_text"] = build_ofp(dep, arr, result, ac=ac, pax=pax, cargo=cargo)
    return result

# =====================================================================
# МОДЕЛИ
# =====================================================================

class CalcReq(BaseModel):
    dep: str
    arr: str
    ac: Optional[str] = "A320"
    pax: Optional[int] = 150
    cargo: Optional[float] = 0
    dep_rwy: Optional[str] = None
    arr_rwy: Optional[str] = None

# =====================================================================
# ЭНДПОИНТЫ
# =====================================================================

@app.on_event("startup")
async def _startup():
    LOAD_STATE["started_at"] = datetime.now(timezone.utc).isoformat()
    log(f"FlyBrief v{APP_VERSION} starting...")
    for fn, name in ((load_airports, "airports"), (load_runways, "runways"),
                     (load_navaids, "navaids"), (load_xplane_all, "xplane")):
        try:
            fn()
        except Exception as e:
            LOAD_STATE["error"] = f"{name}: {e}"
            log(LOAD_STATE["error"])
    LOAD_STATE["finished_at"] = datetime.now(timezone.utc).isoformat()
    log("startup done")

@app.get("/", response_class=HTMLResponse)
async def index():
    html_path = os.path.join(CACHE_DIR, "indexfly.html")
    if os.path.exists(html_path):
        with open(html_path, "r", encoding="utf-8") as f:
            return HTMLResponse(f.read())
    return HTMLResponse("<h1>FlyBrief v0.7.1</h1><p>indexfly.html not found</p>")

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
    """
    Возвращает список полос с идентификаторами.
    Формат: { "icao": "UUEE", "runways": [
        {"ident":"06L","hdg":62,"length_ft":12000,"lat":...,"lon":...,"pair":"24R"}, ...]}
    """
    icao = icao.upper()
    if icao not in RUNWAYS:
        return {"icao": icao, "runways": []}
    # уже отсортировано по длине и идентификатору
    rws = sorted(RUNWAYS[icao], key=lambda r: (-r.get("len_ft", 0), r["ident"]))
    return {"icao": icao, "runways": rws}

@app.get("/charts")
async def charts(icao: str):
    icao = icao.upper()
    return {
        "icao": icao,
        "charts": [
            {"name": f"{icao} — SkyVector", "url": f"https://skyvector.com/airport/{icao}"},
            {"name": f"{icao} — AirNav",   "url": f"https://www.airnav.com/airport/{icao}"},
        ],
    }

@app.get("/weather")
async def weather(icao: str):
    m = fetch_metar(icao)
    return {"icao": icao.upper(), "metar": m}

@app.post("/calculate")
async def calculate(req: CalcReq):
    return calc_route(req.dep, req.arr,
                      ac=req.ac or "A320",
                      pax=req.pax or 150,
                      cargo=req.cargo or 0.0,
                      dep_rwy=req.dep_rwy,
                      arr_rwy=req.arr_rwy)

@app.get("/calculate")
async def calculate_get(dep: str, arr: str, ac: str = "A320",
                        pax: int = 150, cargo: float = 0,
                        dep_rwy: Optional[str] = None,
                        arr_rwy: Optional[str] = None):
    return calc_route(dep, arr, ac=ac, pax=pax, cargo=cargo,
                      dep_rwy=dep_rwy, arr_rwy=arr_rwy)

@app.get("/download_pln")
async def download_pln(dep: str, arr: str):
    r = calc_route(dep, arr)
    lines = ["; FlyBrief PLN export",
             f"; {dep} -> {arr}",
             f"; source: {r['source']}",
             "",
             "[FlightPlan]",
             f"title={dep} to {arr}",
             "type=IFR",
             f"departure_id={dep}",
             f"destination_id={arr}",
             "",
             "[Waypoints]"]
    for p in r["points"]:
        lines.append(f"{p['ident']} {p['lat']:.5f} {p['lon']:.5f}")
    return PlainTextResponse("\n".join(lines), headers={
        "Content-Disposition": f"attachment; filename={dep}-{arr}.pln"
    })

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
