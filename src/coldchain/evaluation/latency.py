"""E4 notification latency (Table 3.10): how quickly the owner of an operational critical incident is sent its SMS
once the data that trigger it have arrived. Live, with real receipt times.

Background load: the Kano scenario network (674 units) streams in real time from the simulator (`coldchain.sim
emulate --now --speed 1`), started by the runner, so the measurement is taken with the whole network reporting.

Scripted incidents: N new loggers, each at a different Kano health facility (so each has its own officer-in-
charge with a telephone number), report one reading a minute in real time. Half go into a heat excursion
(12 °C; qualifies after 30 minutes, FR3.1), half into a freeze (-2 °C; qualifies after 5 minutes), with start
times spread over the first minutes of the test so that alerts arrive one at a time and in bursts. The
excursions continue until the end of the test, so every incident is operational.

Metric: for each SMS alert to an incident's owner, first dispatch to the gateway (notification.first_sent_at)
minus receipt of the triggering reading (incident.received_at: the latest receipt among the readings the
detector had when it detected the event). Reported: median, p95 and maximum, with the stages in between
(receipt -> detection -> incident -> dispatch) and event-to-receipt separately. Operational critical incidents
that arise in the background network during the test are measured the same way and reported alongside.
Criterion: p95 <= 30 s. Every SMS body sent during the test is also checked against the one-SMS limit
(160 GSM-7 characters); the templates' worst cases are checked by tests/test_templates.py.
"""
import json
import random
import statistics
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg

from coldchain.config import get_settings
from coldchain.notify import templates
from coldchain.simulator.basic import SimDevice, connect, topic

OUT = Path(__file__).resolve().parents[3] / "runs" / "eval" / "e4"
HEAT, FREEZE = 12.0, -2.0
QUALIFY_MIN = {"heat_excursion": 30, "freeze": 5}


def _url(role: str) -> str:
    return get_settings().db_url(role).replace("postgresql+psycopg://", "postgresql://")


def _plan(n: int, spread_min: int, seed: int) -> list[dict]:
    """Excursion type and start minute for each logger; qualifying times spread over the test."""
    rng = random.Random(seed)
    out = []
    for i in range(n):
        kind = "heat_excursion" if i % 2 == 0 else "freeze"
        # heat qualifies 30 minutes after it starts, freeze 5: start freezes later so both spread evenly
        start = 2 + rng.randrange(spread_min) + (25 if kind == "freeze" else 0)
        out.append({"kind": kind, "start_min": start})
    return out


def run(facilities: list[str], n: int = 100, spread_min: int = 15, seed: int = 101) -> dict:
    plan = _plan(n, spread_min, seed)
    duration = max(p["start_min"] + QUALIFY_MIN[p["kind"]] for p in plan) + 3
    loggers = []
    with psycopg.connect(_url("owner"), autocommit=True) as conn:
        for i, (p, facility) in enumerate(zip(plan, facilities, strict=False)):
            serial = f"E4-{i:03d}-{uuid.uuid4().hex[:6]}"
            dev = conn.execute("""INSERT INTO device (serial_no, facility_id, type, equipment_type)
                                  VALUES (%s, %s, 'fixed', 'ice_lined_refrigerator') RETURNING id::text""",
                               (serial, facility)).fetchone()[0]
            loggers.append({**p, "serial": serial, "id": dev, "facility": facility})
    time.sleep(3)   # the bridge reloads its device registry

    began = datetime.now(UTC).replace(second=0, microsecond=0) + timedelta(minutes=1)
    rng = random.Random(seed)
    devices = {d["serial"]: SimDevice(d["serial"]) for d in loggers}
    client = connect(f"e4-{uuid.uuid4().hex[:8]}")
    print(f"E4: {len(loggers)} loggers, {duration} minutes from {began.isoformat()}", flush=True)
    for k in range(duration):
        t = began + timedelta(minutes=k)
        while (wait := (t - datetime.now(UTC)).total_seconds()) > 0:
            time.sleep(min(wait, 1))
        for d in loggers:
            hot = HEAT if d["kind"] == "heat_excursion" else FREEZE
            dev = devices[d["serial"]]
            r = next(dev.readings(t, 1, 60, rng, temp=lambda _k, k=k, d=d, hot=hot: hot if k >= d["start_min"] else None))
            client.publish(topic(d["serial"]), json.dumps({"device_id": d["serial"], **r}), qos=1)
        if k % 5 == 0:
            print(f"  minute {k}/{duration}", flush=True)
    client.loop_stop()
    client.disconnect()
    return {"began": began.isoformat(), "duration_min": duration, "loggers": loggers}


def _pct(xs: list[float], q: float) -> float:
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, max(0, int(-(-q * len(xs) // 1)) - 1))], 3)   # nearest-rank


def _summary(xs: list[float]) -> dict:
    if not xs:
        return {"n": 0}
    return {"n": len(xs), "median": round(statistics.median(xs), 3), "p95": _pct(xs, 0.95), "max": round(max(xs), 3),
            "min": round(min(xs), 3)}


def check(state: dict, settle_s: int = 90) -> dict:
    time.sleep(settle_s)
    began = datetime.fromisoformat(state["began"])
    scripted = {d["id"] for d in state["loggers"]}
    with psycopg.connect(_url("owner")) as conn:
        rows = conn.execute("""
            SELECT i.id::text, i.device_id::text, i.type, i.severity, i.mode, i.received_at, i.detected_at,
                   i.created_at, n.created_at, n.first_sent_at, n.delivered_at, n.status, e.event_start
            FROM incident i
            JOIN integrity_event e ON e.incident_id = i.id
                 AND e.detected_at = (SELECT min(detected_at) FROM integrity_event WHERE incident_id = i.id)
            LEFT JOIN notification n ON n.incident_id = i.id AND n.kind = 'alert' AND n.channel = 'sms'
            WHERE i.created_at >= %s AND i.severity = 'critical' AND i.mode = 'operational'""", (began,)).fetchall()
        bodies = [b for (b,) in conn.execute("SELECT body FROM notification WHERE channel = 'sms' AND created_at >= %s",
                                             (began,))]
    groups = {"scripted": [], "background": []}
    missing = []
    for (inc, dev, typ, _sev, _mode, received, detected, created, queued, sent, delivered, status,
         start) in rows:
        g = "scripted" if dev in scripted else "background"
        if sent is None or received is None:
            missing.append({"incident": inc, "group": g, "type": typ, "status": status})
            continue
        groups[g].append({"incident": inc, "type": typ,
                          "receipt_to_dispatch": (sent - received).total_seconds(),
                          "receipt_to_detection": (detected - received).total_seconds(),
                          "detection_to_incident": (created - detected).total_seconds(),
                          "incident_to_dispatch": (sent - created).total_seconds(),
                          "dispatch_to_delivery": (delivered - sent).total_seconds() if delivered else None,
                          "event_to_receipt": (received - (start + timedelta(minutes=QUALIFY_MIN.get(typ, 0))))
                          .total_seconds()})
    over = [b for b in bodies if not templates.fits(b)]
    out = {"began": state["began"], "duration_min": state["duration_min"], "loggers": len(state["loggers"]),
           "scripted_incidents_expected": len(state["loggers"]), "missing_dispatch": missing,
           "sms_bodies_checked": len(bodies), "sms_bodies_over_limit": over,
           "longest_sms": max((len(b) for b in bodies), default=0)}
    for g, xs in groups.items():
        out[g] = {k: _summary([x[k] for x in xs if x[k] is not None])
                  for k in ("receipt_to_dispatch", "receipt_to_detection", "detection_to_incident",
                            "incident_to_dispatch", "dispatch_to_delivery", "event_to_receipt")}
        out[g]["by_type"] = {t: _summary([x["receipt_to_dispatch"] for x in xs if x["type"] == t])
                             for t in sorted({x["type"] for x in xs})}
        out[g + "_rows"] = xs
    allx = [x["receipt_to_dispatch"] for xs in groups.values() for x in xs]
    out["all"] = _summary(allx)
    scripted_found = len(groups["scripted"])
    out["criteria"] = {"p95 <= 30 s": bool(allx) and _pct(allx, 0.95) <= 30,
                       "every scripted incident alerted": scripted_found == len(state["loggers"]),
                       "all SMS <= 160": not over}
    return out

