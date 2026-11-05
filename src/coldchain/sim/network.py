"""Network model (Section 3.3.5): the state's stores, health facilities and outreach teams, placed at real
locations from the GRID3 facility extract, with each unit's equipment, power source, tariff band, climate
cell and condition.

Facilities and their locations come from the source data; which facilities hold vaccines, where each LGA
store sits, and the power and equipment assignment follow the configuration (assumptions in params.py).
The network depends only on the configuration's network settings and its own seed, so runs with different
seeds share the same network.
"""
import csv
import itertools
import math
import uuid
from collections import defaultdict
from dataclasses import asdict, dataclass, fields

import numpy as np

from coldchain.sim.sources import CLIMATE, FACILITIES

NAMESPACE = uuid.UUID("6f1d2a8e-3c4b-4e5f-9a0b-1c2d3e4f5a6b")
STATE_ID = "NG-KN"
STATE_STORE_ID = "8a3c1f52-2b7e-4c1d-9d6a-0f4e5b6c7d10"     # the reference data's Kano State Cold Store
KNOWN_LGA_CODES = {"Gwale": "GWL", "Dala": "DAL", "Nassarawa": "NAS"}   # already in the reference data


@dataclass
class Site:
    id: str
    name: str
    tier: str                   # state_store | lga_store | health_facility
    lga: str | None
    jurisdiction_id: str
    parent_id: str | None
    lat: float
    lon: float
    power_source: str
    grid_band: str | None
    source: str                 # where the site and its location come from


@dataclass
class Unit:
    serial: str
    site_id: str
    mobile: bool
    equipment: str
    has_power_signal: bool
    has_door_sensor: bool
    interval_s: int
    cell: int                   # index into the climate cells
    degraded: bool


@dataclass
class Network:
    sites: list[Site]
    units: list[Unit]
    lga_codes: dict[str, str]
    cells: list[tuple[float, float]]

    def site(self, site_id: str) -> Site:
        return self._by_id[site_id]

    def __post_init__(self):
        self._by_id = {s.id: s for s in self.sites}

    def counts(self) -> dict:
        tiers = defaultdict(int)
        for s in self.sites:
            tiers[s.tier] += 1
        return {"sites": dict(tiers), "fixed_units": sum(not u.mobile for u in self.units),
                "mobile_units": sum(u.mobile for u in self.units), "lgas": len(self.lga_codes)}

    def write(self, sites_path, units_path) -> None:
        for path, rows, cls in ((sites_path, self.sites, Site), (units_path, self.units, Unit)):
            with open(path, "w", newline="") as f:
                w = csv.DictWriter(f, [x.name for x in fields(cls)])
                w.writeheader()
                w.writerows(asdict(r) for r in rows)


def lga_codes(names: list[str]) -> dict[str, str]:
    """Three-letter LGA suffixes: the reference data's codes, else the first three letters that are free."""
    codes = {n: KNOWN_LGA_CODES[n] for n in names if n in KNOWN_LGA_CODES}
    used = set(codes.values())
    for name in sorted(n for n in names if n not in codes):
        letters = [c for c in name.upper() if c.isalpha()]
        for combo in itertools.combinations(range(1, len(letters)), 2):
            code = letters[0] + letters[combo[0]] + letters[combo[1]]
            if code not in used:
                break
        codes[name] = code
        used.add(code)
    return codes


def climate_cells() -> list[tuple[float, float]]:
    with CLIMATE.open() as f:
        return sorted({(float(r["cell_lat"]), float(r["cell_lon"])) for r in csv.DictReader(f)})


def _nearest(cells, lat, lon) -> int:
    return min(range(len(cells)), key=lambda i: (cells[i][0] - lat) ** 2 + (cells[i][1] - lon) ** 2)


def _quota(rng: np.random.Generator, shares: dict[str, float], n: int) -> list[str]:
    """n labels in the configured proportions (largest remainder), in random order."""
    exact = {k: v * n for k, v in shares.items()}
    counts = {k: int(v) for k, v in exact.items()}
    for k in sorted(exact, key=lambda k: exact[k] - counts[k], reverse=True)[: n - sum(counts.values())]:
        counts[k] += 1
    labels = [k for k, c in counts.items() for _ in range(c)]
    return [labels[i] for i in rng.permutation(n)]


def build(net: dict, seed: int = 0) -> Network:
    rng = np.random.default_rng(seed)
    with FACILITIES.open() as f:
        rows = list(csv.DictReader(f))
    chosen = set(net["lgas"]) or {r["lga"] for r in rows}
    unknown = chosen - {r["lga"] for r in rows}
    if unknown:
        raise ValueError(f"unknown LGAs: {sorted(unknown)}")
    holders = [r for r in rows if r["lga"] in chosen and r["level_option"] in net["vaccine_levels"]
               and r["ownership"] in net["ownership"]]
    codes = lga_codes(sorted(chosen))
    cells = climate_cells()

    # Power source and tariff band by quota, so the network matches the configured proportions exactly
    power = dict(zip((r["grid3_id"] for r in holders), _quota(rng, net["power_mix"], len(holders)), strict=True))
    on_grid = [g for g, p in power.items() if p in ("grid", "grid_generator")]
    band = dict(zip(on_grid, _quota(rng, net["grid_bands"], len(on_grid)), strict=True))
    store_band = iter(_quota(rng, net["grid_bands"], len(chosen)))
    ss = net["state_store"]
    sites = [Site(STATE_STORE_ID, ss["name"], "state_store", None, STATE_ID, None, ss["lat"], ss["lon"],
                  "grid_generator", "C", "reference data; location approximate")]
    by_lga = defaultdict(list)
    for r in holders:
        by_lga[r["lga"]].append(r)
    for lga in sorted(by_lga):
        members = by_lga[lga]
        # ASSUMPTION: the LGA store sits at the vaccine-holding facility nearest the centre of the LGA's facilities
        clat = sum(float(r["lat"]) for r in members) / len(members)
        clon = sum(float(r["lon"]) for r in members) / len(members)
        host = min(members, key=lambda r: math.hypot(float(r["lat"]) - clat, float(r["lon"]) - clon))
        store_id = str(uuid.uuid5(NAMESPACE, f"lga-store:{lga}"))
        sites.append(Site(store_id, f"{lga} LGA Cold Store (sim)", "lga_store", lga, f"{STATE_ID}-{codes[lga]}",
                          STATE_STORE_ID, float(host["lat"]), float(host["lon"]), "grid_generator",
                          next(store_band), f"placed at GRID3 {host['grid3_id']} (assumption)"))
        for r in members:
            g = r["grid3_id"]
            sites.append(Site(str(uuid.uuid5(NAMESPACE, g)), r["name"], "health_facility", lga,
                              f"{STATE_ID}-{codes[lga]}", store_id, float(r["lat"]), float(r["lon"]), power[g],
                              band.get(g), f"GRID3 {g}"))

    units, n_by_lga = [], defaultdict(int)
    for s in sites:
        code = codes[s.lga] if s.lga else "KN"
        n_by_lga[code] += 1
        equipment = ("walk_in_cold_room" if s.tier != "health_facility"
                     else "solar_direct_drive" if s.power_source == "solar" else "ice_lined_refrigerator")
        units.append(Unit(f"SIM-{code}-{n_by_lga[code]:04d}", s.id, False, equipment, s.power_source != "solar",
                          True, net["interval_s"], _nearest(cells, s.lat, s.lon),
                          bool(rng.random() < net["degraded_share"])))
        if s.tier == "lga_store":
            for t in range(net["outreach_teams_per_lga"]):
                units.append(Unit(f"SIM-{code}-M{t + 1:03d}", s.id, True, "vaccine_carrier", False, False,
                                  net["interval_s"], _nearest(cells, s.lat, s.lon), False))
    return Network(sites, units, codes, cells)
