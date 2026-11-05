"""Derived extracts of the simulator's source data (Table 3.13).

The raw sources sit in data/ and are not committed (they are large and licensed separately); this module
reduces them to the small files the simulator reads, in data/derived/, which are committed so that every
run can be reproduced:

- kano_facilities.csv: Kano State health facilities from the GRID3 Nigeria health facilities dataset (v2.0).
- kano_climate_2024.csv: daily mean, minimum and maximum air temperature at 2 m and all-sky surface
  shortwave irradiance for each NASA POWER grid cell that contains a Kano facility, for 2024.
- kano_roads.csv.gz: the Kano road network from the OpenStreetMap roads export (HOT), as a graph: one row
  per edge between junctions, with its class, length and shape.
- gts_kano_movement.json: movement parameters from the GTS tracks (gts.py); no identifiers or positions.

    uv run python -m coldchain.sim sources [--gts DIR]
"""
import csv
import gzip
import itertools
import json
import math
import sqlite3
import struct
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
RAW = ROOT / "data"
DERIVED = RAW / "derived"
FACILITIES = DERIVED / "kano_facilities.csv"
CLIMATE = DERIVED / "kano_climate_2024.csv"
ROADS = DERIVED / "kano_roads.csv.gz"
MOVEMENT = DERIVED / "gts_kano_movement.json"
STATE = "Kano"

FACILITY_FIELDS = ["grid3_id", "nhfr_code", "name", "lga", "ward", "level", "level_option", "ownership",
                   "ownership_type", "lat", "lon"]
CLIMATE_FIELDS = ["cell_lat", "cell_lon", "date", "t2m", "t2m_min", "t2m_max", "irradiance_mj"]


def _grid3_file() -> Path:
    found = sorted(RAW.glob("GRID3_NGA_health_facilities*.csv"))
    if not found:
        raise SystemExit("GRID3 health facilities CSV not found in data/")
    return found[-1]


def facilities() -> int:
    rows = []
    with _grid3_file().open(encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            if r["state"] != STATE or not r["latitude"]:
                continue
            rows.append({"grid3_id": r["globalid"], "nhfr_code": r["nhfr_facility_code"],
                         "name": r["facility_name"].strip(), "lga": r["lga"], "ward": r["ward"],
                         "level": r["facility_level"], "level_option": r["facility_level_option"].replace("\xa0", " "),
                         "ownership": r["ownership"], "ownership_type": r["ownership_type"],
                         "lat": f"{float(r['latitude']):.6f}", "lon": f"{float(r['longitude']):.6f}"})
    rows.sort(key=lambda r: (r["lga"], r["name"], r["grid3_id"]))
    DERIVED.mkdir(parents=True, exist_ok=True)
    with FACILITIES.open("w", newline="") as f:
        w = csv.DictWriter(f, FACILITY_FIELDS)
        w.writeheader()
        w.writerows(rows)
    return len(rows)


def climate() -> int:
    rows = []
    for path in sorted((RAW / "nasa_power").glob("power_*_2024.json")):
        doc = json.loads(path.read_text())
        lon, lat = doc["geometry"]["coordinates"][:2]
        p = doc["properties"]["parameter"]
        for day in sorted(p["T2M"]):
            values = [p[k][day] for k in ("T2M", "T2M_MIN", "T2M_MAX", "ALLSKY_SFC_SW_DWN")]
            if any(v == -999 for v in values):       # POWER's fill value
                raise SystemExit(f"{path.name}: missing value on {day}")
            rows.append({"cell_lat": f"{lat:.3f}", "cell_lon": f"{lon:.3f}",
                         "date": f"{day[:4]}-{day[4:6]}-{day[6:]}", "t2m": values[0], "t2m_min": values[1],
                         "t2m_max": values[2], "irradiance_mj": values[3]})
    if not rows:
        raise SystemExit("no NASA POWER files in data/nasa_power/")
    DERIVED.mkdir(parents=True, exist_ok=True)
    with CLIMATE.open("w", newline="") as f:
        w = csv.DictWriter(f, CLIMATE_FIELDS)
        w.writeheader()
        w.writerows(rows)
    return len(rows)


# Road classes a vehicle or motorcycle can use (OpenStreetMap highway=*); paths and footways are left out.
DRIVABLE = {"motorway", "motorway_link", "trunk", "trunk_link", "primary", "primary_link", "secondary",
            "secondary_link", "tertiary", "tertiary_link", "unclassified", "residential", "living_street",
            "service", "track", "road"}


def _wkb_lines(blob: bytes) -> list[list[tuple[float, float]]]:
    """LineString or MultiLineString coordinates from a GeoPackage geometry blob."""
    flags = blob[3]
    envelope = {0: 0, 1: 32, 2: 48, 3: 48, 4: 64}[(flags >> 1) & 7]
    return _wkb(memoryview(blob)[8 + envelope:])[0]


def _wkb(b: memoryview) -> tuple[list, int]:
    e = "<" if b[0] == 1 else ">"
    kind = struct.unpack_from(e + "I", b, 1)[0] % 1000
    if kind == 2:
        n = struct.unpack_from(e + "I", b, 5)[0]
        xy = struct.unpack_from(e + f"{2 * n}d", b, 9)
        return [list(zip(xy[::2], xy[1::2], strict=True))], 9 + 16 * n
    if kind == 5:
        parts, off = [], 9
        for _ in range(struct.unpack_from(e + "I", b, 5)[0]):
            lines, used = _wkb(b[off:])
            parts += lines
            off += used
        return parts, off
    return [], len(b)     # areas (highway=* polygons, e.g. squares) are not part of the road graph


def metres(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Great-circle distance between two (lon, lat) points."""
    lon1, lat1, lon2, lat2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371000 * math.asin(math.sqrt(h))


def roads() -> tuple[int, int]:
    """Kano's drivable roads as a graph. Ways are split wherever they share a vertex, so junctions become
    nodes; only the largest connected part is kept, so that every node can reach every other."""
    path = RAW / "hotosm_nga_roads_osm_gpkg" / "roads.gpkg"
    if not path.exists():
        raise SystemExit(f"{path} not found")
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    ways = []
    for geom, highway in con.execute("SELECT geom, highway FROM roads WHERE adm1_name = ?", (STATE,)):
        if highway in DRIVABLE and geom:
            for line in _wkb_lines(geom):
                pts = [(round(x, 6), round(y, 6)) for x, y in line]
                pts = [p for i, p in enumerate(pts) if i == 0 or p != pts[i - 1]]
                if len(pts) >= 2:
                    ways.append((highway, pts))
    uses = Counter(p for _, pts in ways for p in set(pts))
    ends = {p for _, pts in ways for p in (pts[0], pts[-1])}
    node_id: dict[tuple[float, float], int] = {}
    edges = []
    for highway, pts in ways:
        start = 0
        for i in range(1, len(pts)):
            if i == len(pts) - 1 or uses[pts[i]] > 1 or pts[i] in ends:
                seg = pts[start:i + 1]
                u, v = (node_id.setdefault(p, len(node_id)) for p in (seg[0], seg[-1]))
                if u != v:
                    edges.append((u, v, highway, sum(metres(a, b) for a, b in itertools.pairwise(seg)), seg))
                start = i
    # largest connected component
    adj = defaultdict(list)
    for u, v, *_ in edges:
        adj[u].append(v)
        adj[v].append(u)
    seen, best = set(), set()
    for n in adj:
        if n in seen:
            continue
        comp, stack = {n}, [n]
        while stack:
            for m in adj[stack.pop()]:
                if m not in comp:
                    comp.add(m)
                    stack.append(m)
        seen |= comp
        best = max(best, comp, key=len)
    kept = [e for e in edges if e[0] in best]
    DERIVED.mkdir(parents=True, exist_ok=True)
    with gzip.open(ROADS, "wt", newline="", compresslevel=9) as f:
        w = csv.writer(f)
        w.writerow(["u", "v", "highway", "length_m", "shape"])
        for u, v, highway, length, seg in kept:
            w.writerow([u, v, highway, round(length, 1), " ".join(f"{x:.5f},{y:.5f}" for x, y in seg)])
    return len(best), len(kept)


def main(gts_dir: str | None = None) -> None:
    print(f"{facilities()} facilities -> {FACILITIES.relative_to(ROOT)}")
    print(f"{climate()} cell-days -> {CLIMATE.relative_to(ROOT)}")
    nodes, edges = roads()
    print(f"{nodes} junctions, {edges} road edges -> {ROADS.relative_to(ROOT)}")
    if gts_dir:
        from coldchain.sim.gts import reduce
        print(f"{reduce(Path(gts_dir))} -> {MOVEMENT.relative_to(ROOT)}")
    else:
        print(f"GTS tracks not given (--gts DIR); keeping {MOVEMENT.relative_to(ROOT)}")
