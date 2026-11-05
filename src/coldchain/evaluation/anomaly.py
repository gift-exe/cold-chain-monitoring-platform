"""E3 anomaly comparison (Table 3.10): the isolation forest against the rolling z-score baseline, on the same
hours of the same replayed run.

Both detectors are applied to every complete hour of every fixed unit with an anomaly model, using the
readings the prototype stored, so the forest is exactly the deployed one (detection/anomaly.py) and the
baseline is the one Section 3.4.4 defines (an hour whose mean is more than three standard deviations from
the unit's previous 24 hourly means). An hour is a *fault hour* if an injected sensor drift was active for at
least half of it. (Abnormal compressor cycling, also named in Table 3.14, is not used: in the single-node
equipment model calibrated to PQS holdover times a compressor cycle lasts hours, so short-cycling leaves no
signature in one-minute readings beyond sensor noise.)

Reported for each detector: fault recall (faults with at least one flagged hour), hour recall, precision
(flagged fault hours / flagged hours), and the flagged hours outside any fault, split into those during
another injected failure, those during a natural condition (power cut, door opening, excursion) and the
rest, per 100 device-days (the design target for false warnings is at most one per 100 device-days).
"""
import csv
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg

from coldchain.detection import anomaly, store

FAULTS = ("sensor_drift",)   # abnormal cycling is not observable in the single-node equipment model (I8)
HALF = timedelta(minutes=30)
DETECTORS = ("forest", "zscore")
EVER = (datetime(2000, 1, 1, tzinfo=UTC), datetime(2100, 1, 1, tzinfo=UTC))   # a run's whole history


def _t(s: str) -> datetime:
    return datetime.fromisoformat(s)


def _read(path: Path) -> list[dict]:
    with path.open() as f:
        return list(csv.DictReader(f))


def _overlap(h: datetime, start: datetime, end: datetime) -> timedelta:
    return max(timedelta(0), min(end, h + anomaly.HOUR) - max(start, h))


def score(run_dir: Path, conn: psycopg.Connection) -> dict:
    models = anomaly.load_models()
    units = {u["serial"]: u for u in _read(run_dir / "units.csv") if u["mobile"] == "False"}
    units = {s: u for s, u in units.items() if u["equipment"] in models}
    truth = [g for g in _read(run_dir / "ground_truth.csv") if g["serial"] in units]
    conditions = defaultdict(list)
    for c in _read(run_dir / "conditions.csv"):
        if c["serial"] in units:
            conditions[c["serial"]].append((_t(c["start"]), _t(c["end"])))
    by_unit = defaultdict(list)
    for g in truth:
        by_unit[g["serial"]].append(g)
    ids = dict(conn.execute("SELECT serial_no, id::text FROM device WHERE serial_no = ANY(%s)",
                            (list(units),)).fetchall())

    hours = Counter()                      # (detector, class) -> flagged hours; ("all", class) -> hours
    fault_hit = defaultdict(set)           # detector -> fault ids with a flagged hour
    device_hours = 0
    for serial, u in units.items():
        model = models[u["equipment"]]
        interval = timedelta(seconds=int(u["interval_s"]))
        readings = store.load_readings(conn, ids[serial], EVER[0], EVER[1], store.FALLBACK)
        if not readings:
            continue
        first = readings[0].t.replace(minute=0, second=0, microsecond=0)
        hs = []
        h = first
        while h + anomaly.HOUR <= readings[-1].t + interval:
            hs.append(h)
            h += anomaly.HOUR
        feats = anomaly.features_by_hour(readings, interval, hs)
        means = anomaly.hourly_means(readings, interval)
        for h, x in feats.items():
            device_hours += 1
            faults = [g for g in by_unit[serial] if g["type"] in FAULTS
                      and _overlap(h, _t(g["start"]), _t(g["end"])) >= HALF]
            if faults:
                cls = "fault"
            elif any(_overlap(h, _t(g["start"]), _t(g["end"])) > timedelta(0) for g in by_unit[serial]):
                cls = "other_injected"
            elif any(_overlap(h, s, e) > timedelta(0) for s, e in conditions[serial]):
                cls = "natural_condition"
            else:
                cls = "none"
            hours[("all", cls)] += 1
            z = anomaly.zscore(means, h)
            flags = {"forest": anomaly.score(model, x) > model.threshold,
                     "zscore": z is not None and abs(z) > anomaly.ZSCORE_LIMIT}
            for d, flagged in flags.items():
                if flagged:
                    hours[(d, cls)] += 1
                    fault_hit[d].update(g["id"] for g in faults)

    injected = {t: [g["id"] for g in truth if g["type"] == t] for t in FAULTS}
    device_days = device_hours / 24
    out = {"run": run_dir.name, "units": len(units), "device_days": round(device_days, 1),
           "hours": {cls: hours[("all", cls)] for cls in ("fault", "other_injected", "natural_condition", "none")},
           "faults": {t: len(v) for t, v in injected.items()}, "detectors": {}}
    for d in DETECTORS:
        flagged = {cls: hours[(d, cls)] for cls in ("fault", "other_injected", "natural_condition", "none")}
        out["detectors"][d] = {
            "flagged_hours": flagged,
            "faults_detected": {t: sum(1 for i in v if i in fault_hit[d]) for t, v in injected.items()},
        }
    return out


def aggregate(results: list[dict]) -> dict:
    hours, faults, device_days = Counter(), Counter(), 0.0
    det = {d: {"flagged": Counter(), "detected": Counter()} for d in DETECTORS}
    for r in results:
        hours.update(r["hours"])
        faults.update(r["faults"])
        device_days += r["device_days"]
        for d in DETECTORS:
            det[d]["flagged"].update(r["detectors"][d]["flagged_hours"])
            det[d]["detected"].update(r["detectors"][d]["faults_detected"])
    out = {"replications": len(results), "device_days": round(device_days, 1), "hours": dict(hours),
           "faults": dict(faults), "detectors": {}}
    for d in DETECTORS:
        f, k = det[d]["flagged"], det[d]["detected"]
        total = sum(f.values())
        out["detectors"][d] = {
            "fault_recall": {t: round(k[t] / faults[t], 4) if faults[t] else None for t in FAULTS},
            "faults_detected": dict(k),
            "hour_recall": round(f["fault"] / hours["fault"], 4) if hours["fault"] else None,
            "precision": round(f["fault"] / total, 4) if total else None,
            "flagged_hours": dict(f),
            "unexplained_per_100_device_days": round(100 * f["none"] / device_days, 3) if device_days else None,
            "outside_faults_per_100_device_days": round(100 * (total - f["fault"]) / device_days, 3)
            if device_days else None,
        }
    return out
