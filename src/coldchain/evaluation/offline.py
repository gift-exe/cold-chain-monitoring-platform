"""E7 offline recovery (Table 3.10): delayed, out-of-order and duplicated uploads, with real receipt times.

Each replication registers new loggers at a Kano health facility. Each logger has been offline for G hours
(G = 1, 8, 24, 72) and reconnects now, uploading everything it recorded while offline:

- in random order, in batches, with about 30% of the readings sent a second time in other messages, and then
  the whole upload repeated once (as a device does when it never received the broker's acknowledgement);
- with a heat excursion during the gap that ended well before the upload (expected: a *retrospective*
  episode) and a second one still under way when it reconnects (expected: an *operational* episode). Both
  belong to one incident (episodes within 24 hours, Section 3.4.2), which must be operational because one of
  its episodes is live;
- for G = 72, the buffer reaches 4 hours further back, past the 72-hour horizon, with a third excursion there:
  those readings must be stored and flagged late, and must not raise an incident (FR2.3).

Event times are anchored to the current time, so receipt delays are real (the evaluation stack must be in
live mode, not replay). Metrics: readings lost, duplicate readings stored, duplicate incidents, and the
proportion of episodes correctly classified (retrospective, operational, or not detected because late) and of
incidents in the right mode.
"""
import json
import random
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg

from coldchain.config import get_settings
from coldchain.incidents import store
from coldchain.simulator.basic import SimDevice, connect, topic

GAPS_H = (1, 8, 24, 72)
LATE_TAIL_H = 4
DUPLICATE_SHARE = 0.3
BATCH = 500
OUT = Path(__file__).resolve().parents[3] / "runs" / "eval" / "e7"


def _url(role: str) -> str:
    return get_settings().db_url(role).replace("postgresql+psycopg://", "postgresql://")


def plan(gap_h: int) -> tuple[int, list[dict]]:
    """Buffer length (minutes) and the scripted excursions, as (first, last) minute indexes into the buffer,
    with the expected classification."""
    n = gap_h * 60 + (LATE_TAIL_H * 60 if gap_h == 72 else 0)
    # an hour of normal readings separates the two, well beyond the 10-minute hysteresis (FR3.3)
    events = [{"name": "ended", "first": n - 140, "last": n - 100, "expected": "retrospective"},
              {"name": "ongoing", "first": n - 40, "last": n, "expected": "operational"}]
    if gap_h == 72:
        events.append({"name": "beyond_72h", "first": 30, "last": 90, "expected": "none"})
    if gap_h == 1:   # a one-hour buffer has room for one excursion only: it ends 20 minutes before upload
        events = [{"name": "ended", "first": 5, "last": 40, "expected": "retrospective"}]
    return n, events


def run(seeds: range, facility: str) -> dict:
    client = connect(f"e7-{uuid.uuid4().hex[:8]}")
    devices = []
    with psycopg.connect(_url("owner"), autocommit=True) as conn:
        for seed in seeds:
            for gap in GAPS_H:
                serial = f"E7-{seed}-{gap:02d}H-{uuid.uuid4().hex[:6]}"
                dev_id = conn.execute("""INSERT INTO device (serial_no, facility_id, type, equipment_type)
                                         VALUES (%s, %s, 'fixed', 'ice_lined_refrigerator') RETURNING id::text""",
                                      (serial, facility)).fetchone()[0]
                devices.append({"seed": seed, "gap_h": gap, "serial": serial, "id": dev_id})
    time.sleep(2)   # the bridge reloads its device registry

    upload_at = datetime.now(UTC).replace(second=0, microsecond=0)
    for d in devices:
        rng = random.Random(d["seed"] * 1000 + d["gap_h"])
        n, events = plan(d["gap_h"])
        start = upload_at - timedelta(minutes=n)
        hot = {k for e in events for k in range(e["first"], e["last"])}
        sim = SimDevice(d["serial"])
        readings = list(sim.readings(start, n, 60, rng, temp=lambda k, hot=hot: 12.0 if k in hot else None))
        d.update(sent_unique=len(readings), events=[{**e, "start": (start + timedelta(minutes=e["first"])).isoformat(),
                                                    "end": (start + timedelta(minutes=e["last"])).isoformat()}
                                                   for e in events])
        upload = readings + rng.sample(readings, int(DUPLICATE_SHARE * len(readings)))
        sent = 0
        for _ in range(2):                                   # the whole upload, then again
            rng.shuffle(upload)
            for i in range(0, len(upload), BATCH):
                msg = {"device_id": d["serial"], "readings": upload[i:i + BATCH]}
                client.publish(topic(d["serial"]), json.dumps(msg), qos=1).wait_for_publish()
                sent += len(msg["readings"])
        d["sent_total"] = sent
    client.loop_stop()
    client.disconnect()
    return {"upload_at": upload_at.isoformat(), "devices": devices}


def check(state: dict, settle_s: int = 120) -> dict:
    time.sleep(settle_s)   # storage, detection and incident creation
    upload_at = datetime.fromisoformat(state["upload_at"])
    rows, totals = [], {"readings_lost": 0, "duplicate_readings": 0, "duplicate_incidents": 0, "events": 0,
                        "correctly_classified": 0, "late_flag_errors": 0, "episodes_without_incident": 0,
                        "incidents": 0, "incident_mode_correct": 0}
    with psycopg.connect(_url("owner")) as conn:
        for d in state["devices"]:
            stored, distinct, late, expected_late = conn.execute("""
                SELECT count(*), count(DISTINCT seq_no), count(*) FILTER (WHERE late_flag),
                       count(*) FILTER (WHERE received_at - event_time > interval '72 hours')
                FROM reading WHERE device_id = %s""", (d["id"],)).fetchone()
            episodes = conn.execute("""
                SELECT e.event_start, e.event_end, e.detected_at, e.incident_id::text, i.mode
                FROM integrity_event e LEFT JOIN incident i ON i.id = e.incident_id
                WHERE e.device_id = %s AND e.type = 'heat_excursion'""", (d["id"],)).fetchall()
            row = {"seed": d["seed"], "gap_h": d["gap_h"], "sent_unique": d["sent_unique"],
                   "sent_total": d["sent_total"], "stored": stored, "lost": d["sent_unique"] - distinct,
                   "duplicates_stored": stored - distinct, "late_flagged": late, "late_expected": expected_late,
                   "events": [], "incidents": {}}
            for e in d["events"]:
                s, t_end = datetime.fromisoformat(e["start"]), datetime.fromisoformat(e["end"])
                found = [ep for ep in episodes if s - timedelta(minutes=10) <= ep[0] < t_end]
                if not found:
                    got = "none"
                elif len(found) > 1:
                    got = "duplicate"
                else:   # the episode's own classification (Section 3.4.4), from its end and detection time
                    got = store.mode_for(found[0][1], found[0][2])
                row["events"].append({"name": e["name"], "expected": e["expected"], "got": got,
                                      "integrity_events": len(found),
                                      "incident": found[0][3] if found else None})
                for ep in found:
                    if ep[3]:
                        inc = row["incidents"].setdefault(ep[3], {"mode": ep[4], "expected": "retrospective"})
                        if e["expected"] == "operational":
                            inc["expected"] = "operational"   # any live episode makes the incident operational
                totals["events"] += 1
                totals["correctly_classified"] += got == e["expected"]
                totals["duplicate_incidents"] += max(0, len({ep[3] for ep in found if ep[3]}) - 1)
                totals["episodes_without_incident"] += sum(1 for ep in found if not ep[3])
            for inc in row["incidents"].values():
                totals["incidents"] += 1
                totals["incident_mode_correct"] += inc["mode"] == inc["expected"]
            totals["readings_lost"] += row["lost"]
            totals["duplicate_readings"] += row["duplicates_stored"]
            totals["late_flag_errors"] += abs(late - expected_late)
            rows.append(row)
    totals["classification"] = round(totals["correctly_classified"] / totals["events"], 4)
    totals["criteria"] = {"no loss": totals["readings_lost"] == 0, "no duplicate readings": totals["duplicate_readings"] == 0,
                          "no duplicate incidents": totals["duplicate_incidents"] == 0,
                          "100% classification": totals["correctly_classified"] == totals["events"]
                          and totals["incident_mode_correct"] == totals["incidents"]
                          and totals["episodes_without_incident"] == 0,
                          "late flags correct": totals["late_flag_errors"] == 0}
    return {"upload_at": upload_at.isoformat(), "totals": totals, "devices": rows}
