"""Anomaly warnings (FR4, Section 3.4.4).

Each complete hour of a unit's readings is described by six features and scored by an isolation forest
trained for the unit's equipment type on normal-operation data only. Hours scoring above a threshold
become *warnings* (never critical), each with a plain-language explanation built from the two features
furthest from normal. A rolling z-score on the hourly mean is computed alongside as the simple baseline
the isolation forest is compared with (evaluation test E3).

Models are trained by scripts/train_anomaly.py and stored in settings.model_dir as <equipment_type>.joblib,
with default.joblib used for equipment types without their own model.
"""
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import joblib
import numpy as np
import psycopg
from psycopg.rows import dict_row

from coldchain.config import get_settings
from coldchain.detection.rules import Reading

log = logging.getLogger("detection.anomaly")

HOUR = timedelta(hours=1)
MIN_COVERAGE = 0.75          # an hour with fewer than 75% of its expected readings is not scored (FR3.2)
BASELINE_HOURS = 24          # the unit's own recent behaviour
MIN_BASELINE_HOURS = 12
CYCLE_BAND = 0.2             # °C either side of the hourly mean, to count compressor cycles
ZSCORE_LIMIT = 3.0
FEATURES = ["mean_temp", "rate_of_change", "temp_range", "deviation_from_baseline", "powered_fraction",
            "cooling_cycles"]


@dataclass
class Model:
    """A trained detector for one equipment type."""
    forest: object               # sklearn IsolationForest
    threshold: float             # anomaly score above which an hour is a warning
    median: np.ndarray           # per-feature median of the training data, for explanations
    scale: np.ndarray            # per-feature robust spread (1.4826 x MAD)


# Features ---------------------------------------------------------------------

def _cycles(temps: np.ndarray) -> int:
    """Upward crossings of the hourly mean, ignoring movements within CYCLE_BAND of it."""
    m, state, n = temps.mean(), None, 0
    for t in temps:
        if t > m + CYCLE_BAND:
            if state == "low":
                n += 1
            state = "high"
        elif t < m - CYCLE_BAND:
            state = "low"
    return n


def hour_features(readings: list[Reading], hour_start: datetime, interval: timedelta,
                  baseline_mean: float | None) -> np.ndarray | None:
    """Feature vector for one hour, or None if the hour has too few readings or no baseline."""
    hour = [r for r in readings if hour_start <= r.t < hour_start + HOUR]
    return _features(hour, hour_start, interval, baseline_mean)


def _features(hour: list[Reading], hour_start: datetime, interval: timedelta,
              baseline_mean: float | None) -> np.ndarray | None:
    if len(hour) < MIN_COVERAGE * (HOUR / interval) or baseline_mean is None:
        return None
    temps = np.array([r.temp for r in hour])
    hours = np.array([(r.t - hour_start).total_seconds() / 3600 for r in hour])
    slope = float(np.polyfit(hours, temps, 1)[0]) if len(set(hours)) > 1 else 0.0
    power = [r.power_ok for r in hour if r.power_ok is not None]
    return np.array([temps.mean(), slope, temps.max() - temps.min(), temps.mean() - baseline_mean,
                     (sum(power) / len(power)) if power else 1.0, _cycles(temps)])


def hourly_means(readings: list[Reading], interval: timedelta) -> dict[datetime, float]:
    """Mean temperature of each sufficiently covered hour."""
    buckets: dict[datetime, list[float]] = {}
    for r in readings:
        buckets.setdefault(r.t.replace(minute=0, second=0, microsecond=0), []).append(r.temp)
    need = MIN_COVERAGE * (HOUR / interval)
    return {h: float(np.mean(v)) for h, v in buckets.items() if len(v) >= need}


def baseline_for(means: dict[datetime, float], hour_start: datetime) -> float | None:
    prior = [v for h, v in means.items() if hour_start - BASELINE_HOURS * HOUR <= h < hour_start]
    return float(np.mean(prior)) if len(prior) >= MIN_BASELINE_HOURS else None


def zscore(means: dict[datetime, float], hour_start: datetime) -> float | None:
    """Baseline detector: how far this hour's mean is from the previous 24 hourly means, in SDs."""
    prior = [v for h, v in means.items() if hour_start - BASELINE_HOURS * HOUR <= h < hour_start]
    if len(prior) < MIN_BASELINE_HOURS or hour_start not in means:
        return None
    sd = float(np.std(prior)) or 1e-6
    return (means[hour_start] - float(np.mean(prior))) / sd


def features_by_hour(readings: list[Reading], interval: timedelta,
                     hours: list[datetime]) -> dict[datetime, np.ndarray]:
    means = hourly_means(readings, interval)
    buckets: dict[datetime, list[Reading]] = {}   # readings grouped by hour once, not rescanned per hour
    for r in readings:
        buckets.setdefault(r.t.replace(minute=0, second=0, microsecond=0), []).append(r)
    out = {}
    for h in hours:
        f = _features(buckets.get(h, []), h, interval, baseline_for(means, h))
        if f is not None:
            out[h] = f
    return out


# Scoring and explanation -------------------------------------------------------

def score(model: Model, x: np.ndarray) -> float:
    """Higher means more anomalous."""
    return float(-model.forest.score_samples(x.reshape(1, -1))[0])


def explain(model: Model, x: np.ndarray, n: int = 2) -> str:
    """Plain-language description of the n features furthest from normal for this equipment type."""
    z = (x - model.median) / np.maximum(model.scale, 1e-6)
    parts = []
    for i in np.argsort(-np.abs(z))[:n]:
        lo, hi = model.median[i] - 2 * model.scale[i], model.median[i] + 2 * model.scale[i]
        v = x[i]
        match FEATURES[i]:
            case "mean_temp":
                parts.append(f"average temperature {v:.1f}°C (usually {lo:.1f} to {hi:.1f}°C)")
            case "rate_of_change":
                direction = "rising" if v > 0 else "falling"
                parts.append(f"temperature {direction} {abs(v):.1f}°C per hour (usually within "
                             f"{max(abs(lo), abs(hi)):.1f}°C per hour)")
            case "temp_range":
                parts.append(f"temperature varied by {v:.1f}°C within the hour (usually {max(lo, 0):.1f} to {hi:.1f}°C)")
            case "deviation_from_baseline":
                parts.append(f"{abs(v):.1f}°C {'warmer' if v > 0 else 'colder'} than this unit's last 24 hours")
            case "powered_fraction":
                parts.append(f"powered {v:.0%} of the hour")
            case "cooling_cycles":
                parts.append(f"{int(v)} cooling cycles in the hour (usually {max(lo, 0):.0f} to {hi:.0f})")
    return "Unusual pattern: " + "; ".join(parts) + "."


# Models on disk ------------------------------------------------------------------

def load_models() -> dict[str, Model]:
    models = {}
    for f in Path(get_settings().model_dir).glob("*.joblib"):
        models[f.stem] = joblib.load(f)
    if not models:
        log.warning("no anomaly models in %s: anomaly warnings disabled (run make train)", get_settings().model_dir)
    return models


# Service integration ----------------------------------------------------------------

def evaluate(conn: psycopg.Connection, models: dict[str, Model], dev, t_from: datetime, t_to: datetime) -> list[dict]:
    """Score the complete hours touched by new readings and store any anomaly warnings (idempotently:
    one event per device and hour, updated if rescored)."""
    from coldchain.detection import store  # local import: store imports rules only

    model = models.get(dev.equipment_type) or models.get("default")
    if model is None:
        return []
    first = t_from.replace(minute=0, second=0, microsecond=0)
    readings = store.load_readings(conn, dev.id, first - BASELINE_HOURS * HOUR, t_to + HOUR, store.FALLBACK)
    if not readings:
        return []
    latest = readings[-1].t
    hours, h = [], first
    while h + HOUR <= latest + dev.interval:      # only hours whose last reading has arrived
        if h <= t_to:
            hours.append(h)
        h += HOUR
    feats = features_by_hour(readings, dev.interval, hours)
    means = hourly_means(readings, dev.interval)

    cur = conn.cursor(row_factory=dict_row)
    out = []
    for h, x in feats.items():
        s = score(model, x)
        if s <= model.threshold:
            continue
        z = zscore(means, h)
        details = {"score": round(s, 4), "threshold": round(model.threshold, 4), "explanation": explain(model, x),
                   "features": {f: round(float(v), 3) for f, v in zip(FEATURES, x, strict=True)},
                   "zscore": None if z is None else round(z, 2), "zscore_flag": z is not None and abs(z) > ZSCORE_LIMIT}
        existing = cur.execute("SELECT id FROM integrity_event WHERE device_id = %s AND type = 'anomaly' "
                               "AND event_start = %s", (dev.id, h)).fetchone()
        returning = ("RETURNING id::text, device_id::text, type, severity, event_start, event_end, peak_value, "
                     "borderline, details, detected_at, updated_at")
        if existing:
            row = cur.execute("UPDATE integrity_event SET details = %s, updated_at = now() WHERE id = %s " + returning,
                              (json.dumps(details), existing["id"])).fetchone()
            out.append({**row, "change": "updated"})
        else:
            row = cur.execute(
                "INSERT INTO integrity_event (device_id, type, severity, event_start, event_end, peak_value, details) "
                "VALUES (%s, 'anomaly', 'warning', %s, %s, %s, %s) " + returning,
                (dev.id, h, h + HOUR, round(float(x[0]), 2), json.dumps(details))).fetchone()
            out.append({**row, "change": "created"})
    return out
