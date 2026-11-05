"""Calibration checks (Table 3.13) for the network, equipment, power, climate and failure-injector models.

Each check states its criterion and its result. The equipment checks run the engine's own temperature
update; the others run the full simulation over the calibration seeds (21–30, Table 3.14), with failure
injection switched on so that injected counts can be checked against the configuration.

    uv run python -m coldchain.sim calibrate            # writes runs/calibration/report.md and report.json
"""
import csv
import json
import math
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
from scipy import stats

from coldchain.sim import config as config_mod
from coldchain.sim.engine import Simulation, step
from coldchain.sim.network import build
from coldchain.sim.params import EQUIPMENT, GRID_BANDS, PQS_LIMIT, PQS_TEST_AMBIENT, SEED_SETS
from coldchain.sim.sources import CLIMATE, FACILITIES

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "runs" / "calibration"

# Failure rates used for the injector check: high enough to give counts worth testing
CHECK_RATES = {"heat_excursion": 20, "freeze": 20, "power_loss": 20, "door_open": 20, "device_silence": 20,
               "connectivity_gap": 20, "sensor_drift": 5}


def holdover_minutes(eq) -> int:
    """Minutes for the compartment to reach +10 °C at +43 °C with no power, from its start temperature."""
    T, k = np.array([eq.start_temp]), np.array([eq.k])
    zero = np.zeros(1)
    for minute in range(1, 60 * 24 * 30):
        T = step(T, k, np.array([PQS_TEST_AMBIENT]), zero, zero, zero)
        if T[0] >= PQS_LIMIT:
            return minute
    return math.inf


def pull_down_minutes(eq) -> int:
    T, k, c = np.array([PQS_TEST_AMBIENT]), np.array([eq.k]), np.array([eq.c])
    on, target = np.ones(1), np.array([eq.cooling_target])
    for minute in range(1, 60 * 24 * 7):
        T = step(T, k, np.array([PQS_TEST_AMBIENT]), c, on, target)
        if T[0] <= eq.cut_out:
            return minute
    return math.inf


def equipment_checks() -> list[dict]:
    out = []
    for eq in EQUIPMENT.values():
        sim = holdover_minutes(eq) / 60
        out.append({"model": "Equipment", "check": f"Hold-over, {eq.description}", "source": eq.source,
                    "criterion": f"within ±20% of {eq.holdover_h:.1f} h",
                    "result": f"{sim:.1f} h ({(sim / eq.holdover_h - 1) * 100:+.1f}%)",
                    "passed": abs(sim / eq.holdover_h - 1) <= 0.20})
        if eq.powered:
            pd = pull_down_minutes(eq) / 60
            out.append({"model": "Equipment", "check": f"Pull-down to {eq.cut_out} °C, {eq.description}",
                        "source": "ASSUMPTION (pull-down time not in the PQS record)",
                        "criterion": f"within ±20% of {eq.pull_down_h:.1f} h",
                        "result": f"{pd:.1f} h", "passed": abs(pd / eq.pull_down_h - 1) <= 0.20})
    return out


def network_checks(cfg) -> list[dict]:
    net = build(cfg["network"], cfg["network"]["seed"])
    nc = cfg["network"]
    with FACILITIES.open() as f:
        rows = [r for r in csv.DictReader(f)]
    source_holders = sum(r["level_option"] in nc["vaccine_levels"] and r["ownership"] in nc["ownership"] for r in rows)
    source_lgas = len({r["lga"] for r in rows})
    counts = net.counts()["sites"]
    mix = defaultdict(int)
    for s in net.sites:
        if s.tier == "health_facility":
            mix[s.power_source] += 1
    n = sum(mix.values())
    worst = max(abs(mix[k] / n - v) for k, v in nc["power_mix"].items())
    return [
        {"model": "Network", "check": "Vaccine-holding health facilities", "source": "GRID3 v2.0 (Kano)",
         "criterion": f"equal to the source count ({source_holders})", "result": str(counts.get("health_facility", 0)),
         "passed": counts.get("health_facility", 0) == source_holders},
        {"model": "Network", "check": "LGA stores", "source": "one per LGA",
         "criterion": f"equal to the number of LGAs ({source_lgas})", "result": str(counts.get("lga_store", 0)),
         "passed": counts.get("lga_store", 0) == source_lgas},
        {"model": "Power (source mix)", "check": "Facilities by power source",
         "source": "Ilegogie (2025) grid share; rest ASSUMPTION",
         "criterion": "each share within ±0.5 percentage points of the configuration",
         "result": ", ".join(f"{k} {mix[k] / n:.1%}" for k in nc["power_mix"]), "passed": worst <= 0.005},
    ], net


def run_seeds(cfg_doc: dict, seeds, net) -> list[Simulation]:
    sims = []
    for seed in seeds:
        cfg = config_mod.from_dict({**cfg_doc, "seed": seed})
        sims.append(Simulation(cfg, net).run())
        print(f"  seed {seed}: {sims[-1].summary()['injections']}", flush=True)
    return sims


def grid_checks(sims) -> list[dict]:
    hours = defaultdict(list)
    for sim in sims:
        days = sim.cfg["days"]
        for u in np.flatnonzero(sim.has_grid):
            hours[sim.sites[u].grid_band].append(sim.mains_minutes[u] / 60 / days)
    out = []
    for band, (lo, hi) in GRID_BANDS.items():
        if not hours[band]:
            continue
        mean = float(np.mean(hours[band]))
        out.append({"model": "Power (grid)", "check": f"Mean daily supply, Band {band} ({len(hours[band])} unit-runs)",
                    "source": "NERC (n.d.); outage durations ASSUMPTION", "criterion": f"{lo:g}–{hi:g} h per day",
                    "result": f"{mean:.1f} h (units: {np.min(hours[band]):.1f}–{np.max(hours[band]):.1f})",
                    "passed": lo <= mean <= hi})
    return out


def climate_checks(sims) -> list[dict]:
    with CLIMATE.open() as f:
        source = {(float(r["cell_lat"]), float(r["cell_lon"]), r["date"]): r for r in csv.DictReader(f)}
    worst, n, irr_mismatch = 0.0, 0, 0
    sim = sims[0]
    start = sim.cfg.start
    for d in range(sim.cfg["days"]):
        date = start + timedelta(days=d)
        key_date = f"2024-{date.month:02d}-{date.day:02d}"
        sim_means = sim.ambient_sum[d] / 1440
        for c, (lat, lon) in enumerate(sim.net.cells):
            r = source[(lat, lon, key_date)]
            worst = max(worst, abs(sim_means[c] - float(r["t2m"])))
            irr_mismatch += abs(sim.climate.irradiance[sim.climate.day(date)][c] - float(r["irradiance_mj"])) > 1e-9
            n += 1
    return [
        {"model": "Climate", "check": f"Simulated daily mean air temperature ({n} cell-days)",
         "source": "NASA POWER daily T2M, 2024", "criterion": "within ±1 °C of the source on every cell-day",
         "result": f"largest difference {worst:.3f} °C", "passed": worst <= 1.0},
        {"model": "Power (solar)", "check": f"Daily irradiance used ({n} cell-days)",
         "source": "NASA POWER daily ALLSKY_SFC_SW_DWN, 2024", "criterion": "identical to the source series",
         "result": f"{irr_mismatch} differences", "passed": irr_mismatch == 0},
    ]


def injector_checks(sims) -> list[dict]:
    out = []
    for kind in CHECK_RATES:
        observed = sum(s.summary()["injections"].get(kind, 0) for s in sims)
        expected = sum(s.summary()["injections_expected"][kind] for s in sims)
        alpha = 0.05 / len(CHECK_RATES)    # Bonferroni: the family of checks holds at 95%
        lo, hi = stats.poisson.ppf(alpha / 2, expected), stats.poisson.ppf(1 - alpha / 2, expected)
        out.append({"model": "Failure injector", "check": f"Injected {kind.replace('_', ' ')} events",
                    "source": "run configuration", "criterion": f"within the Poisson range of {expected:.0f} ({lo:.0f}–{hi:.0f}; 95% family-wise)",
                    "result": str(observed), "passed": bool(lo <= observed <= hi)})
    labelled = all(all(r[k] not in ("", None) for k in ("id", "type", "serial", "start", "end", "expected_owner"))
                   for s in sims for r in s.ground_truth())
    out.append({"model": "Failure injector", "check": "Ground-truth labels complete",
                "source": "Section 3.3.5", "criterion": "every injection has type, unit, start, end and expected owner",
                "result": f"{sum(len(s.injections) for s in sims)} labels", "passed": labelled})
    return out


def main(days: int = 30, seeds=SEED_SETS["calibration"]) -> bool:
    doc = {"name": "calibration", "purpose": "calibration", "seed": seeds[0], "days": days,
           "failures": {k: {"rate": v} for k, v in CHECK_RATES.items()}}
    cfg = config_mod.from_dict(doc)
    print("equipment…", flush=True)
    results = equipment_checks()
    net_results, net = network_checks(cfg)
    results += net_results
    print(f"network: {net.counts()}; running {len(seeds)} seeds × {days} days…", flush=True)
    sims = run_seeds(doc, seeds, net)
    results += grid_checks(sims) + climate_checks(sims) + injector_checks(sims)
    tir = [s.summary()["time_in_range"] for s in sims]
    info = {"generated": datetime.now(UTC).isoformat(timespec="seconds"), "seeds": list(seeds), "days": days,
            "network": net.counts(), "time_in_range_by_seed": tir,
            "note": "Time in range is reported for information; its check against published figures is in I7b."}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "report.json").write_text(json.dumps({"info": info, "checks": results}, indent=2, default=lambda o: o.item()))
    lines = ["# Simulator calibration (I7a)", "",
             (f"Generated {info['generated']}; calibration seeds {seeds[0]}–{seeds[-1]}, {days} simulated days "
              f"each; network {net.counts()}."), "",
             "| Model | Check | Source | Criterion | Result | Passed |", "|---|---|---|---|---|---|"]
    lines += [f"| {r['model']} | {r['check']} | {r['source']} | {r['criterion']} | {r['result']} | "
              f"{'yes' if r['passed'] else '**no**'} |" for r in results]
    lines += ["", (f"Time in range (true temperature, 2–8 °C), by seed: {', '.join(f'{x:.1%}' for x in tir)}. "
                   "Reported for information; the check against published figures is part of I7b."), ""]
    (OUT / "report.md").write_text("\n".join(lines))
    failed = [r for r in results if not r["passed"]]
    print(f"{len(results) - len(failed)}/{len(results)} checks passed; report in {OUT.relative_to(ROOT)}/report.md")
    for r in failed:
        print(f"  FAILED {r['model']}: {r['check']}: {r['result']} (criterion {r['criterion']})")
    return not failed
