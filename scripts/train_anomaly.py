"""Train the anomaly model on simulated normal operation (Section 3.4.4).

Training and calibration data come from separate random seeds (Table 3.14): the forest is fitted on the
training seeds, and the warning threshold is set on the calibration seeds so that normal operation produces
no more than one false warning per 100 device-days (2,400 device-hours).

Until the calibrated simulator exists (I7), normal operation is the basic simulator's compressor cycle,
and one model, default.joblib, serves all equipment types.

Run: uv run python scripts/train_anomaly.py
"""
import random
from datetime import UTC, datetime, timedelta
from pathlib import Path

import joblib
import numpy as np
from sklearn.ensemble import IsolationForest

from coldchain.config import get_settings
from coldchain.detection.anomaly import HOUR, Model, features_by_hour
from coldchain.detection.rules import Reading
from coldchain.detection.store import FALLBACK
from coldchain.simulator.basic import SimDevice

TRAINING_SEEDS = range(1, 21)        # Table 3.14
CALIBRATION_SEEDS = range(21, 31)
DAYS = 10
DEVICES_PER_SEED = 3
FALSE_WARNINGS_PER_HOUR = 1 / 2400   # one per 100 device-days
INTERVAL = timedelta(minutes=1)


def normal_features(seed: int) -> np.ndarray:
    rng = random.Random(seed)
    start = datetime(2026, 1, 1, tzinfo=UTC)
    rows = []
    for d in range(DEVICES_PER_SEED):
        dev = SimDevice(f"train-{seed}-{d}", cycle_minutes=rng.uniform(35, 55), phase=rng.random())
        readings = [Reading(datetime.fromisoformat(r["event_time"]), r["temp"], FALLBACK, r["power_ok"])
                    for r in dev.readings(start, DAYS * 24 * 60, 60, rng)]
        hours = [start + k * HOUR for k in range(DAYS * 24)]
        rows += list(features_by_hour(readings, INTERVAL, hours).values())
    return np.array(rows)


def main() -> None:
    train = np.vstack([normal_features(s) for s in TRAINING_SEEDS])
    calib = np.vstack([normal_features(s) for s in CALIBRATION_SEEDS])
    # max_samples=1024 rather than the default 256: with 256, an hour with the compressor stuck (flat
    # temperature, no cycles) scored just below the threshold, because the noisy rate-of-change feature
    # diluted the splits. Larger samples per tree separated both test faults with a clear margin.
    forest = IsolationForest(n_estimators=300, max_samples=1024, random_state=0).fit(train)

    calib_scores = -forest.score_samples(calib)
    threshold = float(np.quantile(calib_scores, 1 - FALSE_WARNINGS_PER_HOUR))
    median = np.median(train, axis=0)
    scale = 1.4826 * np.median(np.abs(train - median), axis=0)
    scale = np.where(scale > 0, scale, train.std(axis=0) + 1e-6)

    out = Path(get_settings().model_dir)
    out.mkdir(exist_ok=True)
    joblib.dump(Model(forest, threshold, median, scale), out / "default.joblib")
    rate = float((calib_scores > threshold).mean())
    print(f"trained on {len(train)} hours, calibrated on {len(calib)} hours; threshold {threshold:.4f}; "
          f"calibration false-warning rate {rate * 2400:.2f} per 100 device-days -> {out / 'default.joblib'}")


if __name__ == "__main__":
    main()
