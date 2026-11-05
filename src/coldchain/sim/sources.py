"""Derived extracts of the simulator's source data (Table 3.13).

The raw sources sit in data/ and are not committed (they are large and licensed separately); this module
reduces them to the small files the simulator reads, in data/derived/, which are committed so that every
run can be reproduced:

- kano_facilities.csv: Kano State health facilities from the GRID3 Nigeria health facilities dataset (v2.0).
- kano_climate_2024.csv: daily mean, minimum and maximum air temperature at 2 m and all-sky surface
  shortwave irradiance for each NASA POWER grid cell that contains a Kano facility, for 2024.

    uv run python -m coldchain.sim sources
"""
import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
RAW = ROOT / "data"
DERIVED = RAW / "derived"
FACILITIES = DERIVED / "kano_facilities.csv"
CLIMATE = DERIVED / "kano_climate_2024.csv"
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


def main() -> None:
    print(f"{facilities()} facilities -> {FACILITIES.relative_to(ROOT)}")
    print(f"{climate()} cell-days -> {CLIMATE.relative_to(ROOT)}")
