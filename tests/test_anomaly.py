"""Anomaly warnings (FR4): features, scoring, explanations and the z-score baseline. No services needed."""
import random
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest
from sklearn.ensemble import IsolationForest

from coldchain.detection.anomaly import HOUR, Model, explain, features_by_hour, hourly_means, score, zscore
from coldchain.detection.rules import Reading
from coldchain.detection.store import FALLBACK
from coldchain.simulator.basic import SimDevice, between

START = datetime(2026, 3, 1, tzinfo=UTC)
MIN = timedelta(minutes=1)


def simulate(seed, hours, **overrides):
    rng = random.Random(seed)
    dev = SimDevice(f"t{seed}", cycle_minutes=rng.uniform(35, 55), phase=rng.random())
    return [Reading(datetime.fromisoformat(r["event_time"]), r["temp"], FALLBACK, r["power_ok"])
            for r in dev.readings(START, hours * 60, 60, rng, **overrides)]


def hours(n, skip=0):
    return [START + k * HOUR for k in range(skip, n)]


@pytest.fixture(scope="module")
def model():
    """A small model trained the same way as scripts/train_anomaly.py, on seeds not used below."""
    train = np.vstack([np.array(list(features_by_hour(simulate(s, 120), MIN, hours(120)).values()))
                       for s in range(1, 9)])
    forest = IsolationForest(n_estimators=300, max_samples=min(1024, len(train)), random_state=0).fit(train)
    calib = np.vstack([np.array(list(features_by_hour(simulate(s, 120), MIN, hours(120)).values()))
                       for s in range(9, 13)])
    threshold = float(np.quantile(-forest.score_samples(calib), 0.999))
    med = np.median(train, axis=0)
    return Model(forest, threshold, med, 1.4826 * np.median(np.abs(train - med), axis=0) + 1e-6)


def test_normal_operation_rarely_warns(model):
    feats = features_by_hour(simulate(100, 72), MIN, hours(72, skip=24))
    flagged = sum(score(model, x) > model.threshold for x in feats.values())
    assert flagged <= 1


def test_steady_drift_within_range_is_flagged_and_explained(model):
    # A failing thermostat: temperature climbs 2 °C per hour from hour 30, still inside 2-8 °C at first.
    # Normal cycling already moves temperature by about ±3 °C per hour, so the rate of change is not what
    # stands out; the higher average and the loss of the usual cycle are.
    drift = lambda k: (5.0 + 2.0 * (k - 1800) / 60) if 1800 <= k < 1920 else None
    readings = simulate(101, 33, temp=drift)
    x = features_by_hour(readings, MIN, [START + 30 * HOUR])[START + 30 * HOUR]
    assert score(model, x) > model.threshold
    text = explain(model, x)
    assert text.startswith("Unusual pattern:") and text.count(";") == 1     # the two most unusual features
    assert "average temperature 6.0°C (usually 4.6 to 5.4°C)" in text


def test_compressor_stuck_off_cycles_flagged(model):
    # No cooling cycles: temperature flat at 6 °C for an hour (abnormal cycling, Table 3.14)
    readings = simulate(102, 32, temp=between(30 * 60, 31 * 60, 6.0))
    x = features_by_hour(readings, MIN, [START + 30 * HOUR])[START + 30 * HOUR]
    assert x[5] == 0                         # no cooling cycles
    assert score(model, x) > model.threshold


def test_hour_with_too_few_readings_not_scored():
    readings = [r for r in simulate(103, 30) if not (START + 28 * HOUR <= r.t < START + 28 * HOUR + 20 * MIN)]
    assert START + 28 * HOUR not in features_by_hour(readings, MIN, [START + 28 * HOUR])  # 40 of 60 readings


def test_no_score_without_baseline_history():
    assert features_by_hour(simulate(104, 6), MIN, [START + 5 * HOUR]) == {}


def test_zscore_baseline_flags_step_change():
    readings = simulate(105, 30, temp=between(29 * 60, 30 * 60, 7.8))
    z = zscore(hourly_means(readings, MIN), START + 29 * HOUR)
    assert z is not None and z > 3
