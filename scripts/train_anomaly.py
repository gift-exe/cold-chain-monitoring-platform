"""Train the anomaly models on simulated normal operation (Section 3.4.4, Table 3.14).

One model per equipment type with a compressor (walk-in cold room, ice-lined refrigerator, solar direct-drive
refrigerator); vaccine carriers are passive boxes with no compressor cycle to learn, so they have no model
and raise no anomaly warnings.

A fallback model, default.joblib, is also trained on the basic simulator (coldchain.simulator.basic), for
devices of other equipment types: the development, demo and test devices, whose readings come from that
simulator. The evaluation network uses only the three types above.

Data: the calibrated simulator on the Kano scenario network with no injected failures. The forest is fitted
on the training seeds (1-20); the warning threshold is set on the calibration seeds (21-30) so that normal
operation produces no more than one false warning per 100 device-days (2,400 device-hours). Test seeds are
never used. Natural events (power cuts, door openings, degraded units) are part of normal operation and stay
in the data, as they would in the field.

Run: uv run python scripts/train_anomaly.py [--days 30] [--units 40] [--default-only]
"""
import argparse
import random
import time
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

import joblib
import numpy as np
from sklearn.ensemble import IsolationForest

from coldchain.config import get_settings
from coldchain.detection.anomaly import HOUR, Model, features_by_hour
from coldchain.detection.rules import Reading
from coldchain.detection.store import FALLBACK
from coldchain.sim import config as config_mod
from coldchain.sim.engine import Simulation
from coldchain.sim.network import build_for
from coldchain.simulator.basic import SimDevice

TRAINING_SEEDS = range(1, 21)         # Table 3.14
CALIBRATION_SEEDS = range(21, 31)
EQUIPMENT = ("walk_in_cold_room", "ice_lined_refrigerator", "solar_direct_drive")
FALSE_WARNINGS_PER_HOUR = 1 / 2400    # one per 100 device-days
WARM_UP_HOURS = 24                    # the first day has no baseline of the unit's own behaviour


class Collect:
    """Sink that keeps the readings of the chosen units."""

    def __init__(self, serials: set[str]):
        self.serials = serials
        self.readings = defaultdict(list)

    def send(self, serial, readings):
        if serial in self.serials:
            for r in readings:
                self.readings[serial].append(Reading(datetime.fromisoformat(r["event_time"]), r["temp"], FALLBACK,
                                                     r.get("power_ok")))

    def close(self):
        pass


def features(purpose: str, seed: int, days: int, per_type: int) -> dict[str, np.ndarray]:
    cfg = config_mod.from_dict({"name": f"anomaly-{purpose}-{seed}", "purpose": purpose, "seed": seed,
                                "days": days, "start": "2024-01-01T00:00:00+00:00"})
    net = build_for(cfg)
    rng = np.random.default_rng(seed)
    chosen = {}
    for eq in EQUIPMENT:
        serials = [u.serial for u in net.units if u.equipment == eq]
        for s in rng.choice(serials, min(per_type, len(serials)), replace=False):
            chosen[str(s)] = eq
    # A different month per seed, so the models see the whole year's climate
    start = datetime(2024, 1, 1, tzinfo=cfg.start.tzinfo) + timedelta(days=(seed * 37) % (366 - days))
    cfg.values["start"] = start.isoformat()
    sink = Collect(set(chosen))
    Simulation(cfg, net, sink).run()
    out = defaultdict(list)
    hours = [start + k * HOUR for k in range(WARM_UP_HOURS, days * 24)]
    for serial, readings in sink.readings.items():
        interval = timedelta(seconds=net.units[0].interval_s)
        out[chosen[serial]] += list(features_by_hour(readings, interval, hours).values())
    return {eq: np.array(rows) for eq, rows in out.items()}


def basic_features(seed: int, days: int = 10, devices: int = 3) -> np.ndarray:
    """Normal operation of the basic simulator's compressor cycle (the fallback model's data)."""
    rng = random.Random(seed)
    start = datetime(2026, 1, 1, tzinfo=UTC)
    rows = []
    for d in range(devices):
        dev = SimDevice(f"train-{seed}-{d}", cycle_minutes=rng.uniform(35, 55), phase=rng.random())
        readings = [Reading(datetime.fromisoformat(r["event_time"]), r["temp"], FALLBACK, r["power_ok"])
                    for r in dev.readings(start, days * 24 * 60, 60, rng)]
        rows += list(features_by_hour(readings, timedelta(minutes=1), [start + k * HOUR for k in range(days * 24)]).values())
    return np.array(rows)


def fit(x_train: np.ndarray, x_calib: np.ndarray) -> tuple[Model, float]:
    # max_samples=1024 rather than the default 256: with 256 the noisy rate-of-change feature diluted the
    # splits, and hours with a stuck compressor scored just below the threshold (I3).
    forest = IsolationForest(n_estimators=300, max_samples=min(1024, len(x_train)), random_state=0).fit(x_train)
    scores = -forest.score_samples(x_calib)
    threshold = float(np.quantile(scores, 1 - FALSE_WARNINGS_PER_HOUR))
    median = np.median(x_train, axis=0)
    scale = 1.4826 * np.median(np.abs(x_train - median), axis=0)
    scale = np.where(scale > 0, scale, x_train.std(axis=0) + 1e-6)
    return Model(forest, threshold, median, scale), float((scores > threshold).mean())


def train_default(out: Path) -> None:
    model, rate = fit(np.vstack([basic_features(s) for s in TRAINING_SEEDS]),
                      np.vstack([basic_features(s) for s in CALIBRATION_SEEDS]))
    joblib.dump(model, out / "default.joblib")
    print(f"default (basic simulator): threshold {model.threshold:.4f}; calibration false warnings "
          f"{rate * 2400:.2f} per 100 device-days")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--units", type=int, default=40, help="units of each equipment type per seed")
    ap.add_argument("--default-only", action="store_true", help="only the fallback model (a minute, not 35)")
    args = ap.parse_args()
    out = Path(get_settings().model_dir)
    out.mkdir(exist_ok=True)
    train_default(out)
    if args.default_only:
        return
    began = time.time()
    train, calib = defaultdict(list), defaultdict(list)
    for purpose, seeds, into in (("training", TRAINING_SEEDS, train), ("calibration", CALIBRATION_SEEDS, calib)):
        for seed in seeds:
            for eq, x in features(purpose, seed, args.days, args.units).items():
                into[eq].append(x)
            print(f"{purpose} seed {seed} done ({time.time() - began:.0f} s)", flush=True)

    for eq in EQUIPMENT:
        model, rate = fit(np.vstack(train[eq]), np.vstack(calib[eq]))
        joblib.dump(model, out / f"{eq}.joblib")
        print(f"{eq}: trained on {sum(map(len, train[eq]))} hours, calibrated on {sum(map(len, calib[eq]))} "
              f"hours; threshold {model.threshold:.4f}; calibration false warnings {rate * 2400:.2f} per 100 device-days")


if __name__ == "__main__":
    main()
