"""E11 fault tolerance (Table 3.10): are readings, incidents and escalations preserved, and escalations kept to
time, when each service is restarted during active escalations and under load? Live, in real time.

Set-up, on the evaluation stack in live mode with the Kano network streaming in real time (as in E4):

- *Active escalations.* N scripted loggers, each at a different Kano health facility, go into a freeze (-2 °C;
  qualifies after 5 minutes) a few minutes into the test, staggered over several minutes, and stay there.
  Nobody acknowledges, so each operational critical incident must escalate to the LGA 15 minutes after it was
  notified and to the state at 60 minutes (Table 3.15).
- *Load.* L further loggers upload buffered readings from the previous days throughout the test, at a steady
  rate (75 readings/s from 100 loggers, each with 60 hours of history), numbered so that any lost reading shows as a gap.
- *Faults.* Each service of the prototype is restarted in turn (Table 5.x): first the data path while incidents
  are being created, then the incident service, the workflow engine and the notification service while the LGA
  escalations fall due, then the event log and the database, and the incident service and workflow engine again
  while the state escalations fall due.

Like a real logger, the test's publishers keep any reading the broker has not acknowledged and send it again
when the broker is back.

Metrics: readings lost (expected sequence numbers missing), incidents lost or duplicated, escalations lost
(due before the end of the test but never applied), and escalations applied more than one minute after they
fell due. The SMS dispatch of each escalation is reported alongside. Criterion: zero lost; all escalations
within one minute of schedule.
"""
import json
import random
import subprocess
import threading
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import paho.mqtt.client as mqtt
import psycopg

from coldchain.config import get_settings
from coldchain.simulator.basic import SimDevice, connect, topic

OUT = Path(__file__).resolve().parents[3] / "runs" / "eval" / "e11"
FREEZE = -2.0
LIMITS = {"lga": timedelta(minutes=15), "state": timedelta(minutes=60)}
# (minute of the test, service): the data path while incidents are created (minutes 4-12), then the services
# that apply the LGA escalations as they fall due (about minutes 22-29), then the infrastructure, then the
# escalation path again as the state escalations fall due (about minutes 67-74)
RESTARTS = [(5, "detection"), (8, "ingestion-bridge"), (11, "storage-writer"), (14, "mosquitto"),
            (23, "incident-service"), (25, "temporal"), (27, "notification-service"),
            (36, "kafka"), (46, "postgres"), (56, "api"),
            (68, "incident-service"), (70, "temporal"), (72, "notification-service")]
DURATION = 80


def _url(role: str) -> str:
    return get_settings().db_url(role).replace("postgresql+psycopg://", "postgresql://")


def _publish(client: mqtt.Client, serial: str, payload: dict) -> int:
    """Publish with QoS 1 and keep trying until the broker has it, as a logger with a buffer does."""
    attempts = 0
    while True:
        attempts += 1
        try:
            info = client.publish(topic(serial), json.dumps(payload), qos=1)
            if info.rc == mqtt.MQTT_ERR_SUCCESS:
                info.wait_for_publish(timeout=20)
                if info.is_published():
                    return attempts
        except (RuntimeError, ValueError):
            pass
        time.sleep(1)


def _register(conn, prefix: str, facilities: list[str]) -> list[dict]:
    out = []
    for i, facility in enumerate(facilities):
        serial = f"{prefix}-{i:03d}-{uuid.uuid4().hex[:6]}"
        dev = conn.execute("""INSERT INTO device (serial_no, facility_id, type, equipment_type)
                              VALUES (%s, %s, 'fixed', 'ice_lined_refrigerator') RETURNING id::text""",
                           (serial, facility)).fetchone()[0]
        out.append({"serial": serial, "id": dev, "facility": facility})
    return out


def _load(devices: list[dict], began: datetime, per_s: float, stop: threading.Event, log: dict) -> None:
    """Buffered uploads of the previous days' readings (normal temperatures), at about per_s readings/s."""
    client = connect(f"e11-load-{uuid.uuid4().hex[:6]}")
    rng = random.Random(11)
    batch = 50
    history = timedelta(hours=60)                     # well inside the 72-hour horizon: none flagged late
    sims = {d["serial"]: SimDevice(d["serial"]) for d in devices}
    cursor = {d["serial"]: began - history for d in devices}
    retries = 0
    while not stop.is_set():
        t0 = time.monotonic()
        for d in devices:
            if cursor[d["serial"]] + timedelta(minutes=batch) > began - timedelta(minutes=10):
                continue                                  # this logger's buffer is exhausted
            sim = sims[d["serial"]]
            readings = list(sim.readings(cursor[d["serial"]], batch, 60, rng))
            cursor[d["serial"]] += timedelta(minutes=batch)
            retries += _publish(client, d["serial"], {"device_id": d["serial"], "readings": readings}) - 1
            d["sent"] = sim.seq_no
        time.sleep(max(0.0, len(devices) * batch / per_s - (time.monotonic() - t0)))
    log["load_publish_retries"] = retries
    client.loop_stop()
    client.disconnect()


def _restarts(began: datetime, stop: threading.Event, log: list) -> None:
    for minute, service in RESTARTS:
        at = began + timedelta(minutes=minute)
        while (wait := (at - datetime.now(UTC)).total_seconds()) > 0:
            if stop.wait(min(wait, 1)):
                return
        t0 = datetime.now(UTC)
        p = subprocess.run(["podman", "restart", f"coldchain-eval-{service}-1"], check=False, capture_output=True,
                           text=True)
        log.append({"minute": minute, "service": service, "restarted_at": t0.isoformat(),
                    "seconds": round((datetime.now(UTC) - t0).total_seconds(), 1), "ok": p.returncode == 0,
                    "error": p.stderr[-300:] if p.returncode else None})
        print(f"  restarted {service} at minute {minute} ({log[-1]['seconds']} s)", flush=True)


def run(facilities: list[str], n: int = 40, load_devices: int = 100, load_per_s: float = 75) -> dict:
    with psycopg.connect(_url("owner"), autocommit=True) as conn:
        loggers = _register(conn, "E11", facilities[:n])
        load = _register(conn, "E11-LOAD", facilities[n:n + load_devices])
    time.sleep(3)
    rng = random.Random(1101)
    for d in loggers:
        d["freeze_from"] = 2 + rng.randrange(6)

    began = datetime.now(UTC).replace(second=0, microsecond=0) + timedelta(minutes=1)
    stop, log, restarts = threading.Event(), {}, []
    threads = [threading.Thread(target=_load, args=(load, began, load_per_s, stop, log), daemon=True),
               threading.Thread(target=_restarts, args=(began, stop, restarts), daemon=True)]
    for t in threads:
        t.start()
    client = connect(f"e11-{uuid.uuid4().hex[:8]}")
    sims = {d["serial"]: SimDevice(d["serial"]) for d in loggers}
    retries = 0
    print(f"E11: {n} escalating incidents, {load_devices} load loggers, {DURATION} minutes from {began.isoformat()}",
          flush=True)
    for k in range(DURATION):
        t = began + timedelta(minutes=k)
        while (wait := (t - datetime.now(UTC)).total_seconds()) > 0:
            time.sleep(min(wait, 1))
        for d in loggers:
            r = next(sims[d["serial"]].readings(t, 1, 60, rng,
                                                temp=lambda _k, k=k, d=d: FREEZE if k >= d["freeze_from"] else None))
            retries += _publish(client, d["serial"], {"device_id": d["serial"], **r}) - 1
            d["sent"] = sims[d["serial"]].seq_no
        if k % 10 == 0:
            print(f"  minute {k}/{DURATION}", flush=True)
    stop.set()
    for t in threads:
        t.join(timeout=120)
    client.loop_stop()
    client.disconnect()
    return {"began": began.isoformat(), "ended": datetime.now(UTC).isoformat(), "loggers": loggers, "load": load,
            "restarts": restarts, "logger_publish_retries": retries, **log}


def check(state: dict, settle_s: int = 180) -> dict:
    time.sleep(settle_s)
    ended = datetime.fromisoformat(state["ended"])
    out = {"began": state["began"], "ended": state["ended"], "restarts": state["restarts"],
           "publish_retries": {"loggers": state["logger_publish_retries"],
                               "load": state.get("load_publish_retries")}}
    with psycopg.connect(_url("owner")) as conn:
        sent = stored = lost = dup = 0
        for d in state["loggers"] + state["load"]:
            n_stored, distinct = conn.execute("SELECT count(*), count(DISTINCT seq_no) FROM reading WHERE device_id = %s",
                                              (d["id"],)).fetchone()
            missing = conn.execute("SELECT count(*) FROM generate_series(1, %s) s WHERE s NOT IN "
                                   "(SELECT seq_no FROM reading WHERE device_id = %s)", (d["sent"], d["id"])).fetchone()[0]
            sent += d["sent"]
            stored += distinct
            lost += missing
            dup += n_stored - distinct
        out["readings"] = {"sent": sent, "stored": stored, "lost": lost, "duplicates": dup}

        incidents, esc = {"expected": len(state["loggers"]), "found": 0, "lost": 0, "duplicated": 0}, []
        for d in state["loggers"]:
            rows = conn.execute("SELECT id::text, mode, severity, ack_due_from FROM incident WHERE device_id = %s",
                                (d["id"],)).fetchall()
            incidents["found"] += len(rows) > 0
            incidents["lost"] += len(rows) == 0
            incidents["duplicated"] += max(0, len(rows) - 1)
            for inc, mode, severity, ack_from in rows[:1]:
                steps = {r[0]: r for r in conn.execute("""
                    SELECT details->>'tier', (details->>'due_at')::timestamptz, (details->>'applied_at')::timestamptz
                    FROM incident_action WHERE incident_id = %s AND action = 'escalated'""", (inc,)).fetchall()}
                for tier, limit in LIMITS.items():
                    due = ack_from + limit
                    if due > ended - timedelta(minutes=1):
                        continue                                  # not yet due when the test ended
                    s = steps.get(tier)
                    sms = conn.execute("""SELECT min(first_sent_at) FROM notification
                                          WHERE incident_id = %s AND kind = 'escalation' AND tier = %s
                                                AND channel = 'sms'""", (inc, tier)).fetchone()[0]
                    esc.append({"incident": inc, "tier": tier, "mode": mode, "severity": severity,
                                "due": due.isoformat(), "recorded_due": s[1].isoformat() if s else None,
                                "applied": s[2].isoformat() if s else None,
                                "delay_s": round((s[2] - due).total_seconds(), 1) if s else None,
                                "sms_dispatched_s": round((sms - due).total_seconds(), 1) if sms else None})
    out["incidents"] = incidents
    applied = [e for e in esc if e["applied"]]
    delays = sorted(e["delay_s"] for e in applied)
    sms = sorted(e["sms_dispatched_s"] for e in esc if e["sms_dispatched_s"] is not None)
    out["escalations"] = {"due": len(esc), "applied": len(applied), "lost": len(esc) - len(applied),
                          "late_over_60s": sum(x > 60 for x in delays),
                          "delay_s": {"median": delays[len(delays) // 2], "max": delays[-1]} if delays else None,
                          "sms_after_due_s": {"median": sms[len(sms) // 2], "max": sms[-1], "n": len(sms)} if sms else None,
                          "by_tier": {t: sum(1 for e in applied if e["tier"] == t) for t in LIMITS}}
    out["escalation_rows"] = esc
    out["criteria"] = {"no readings lost": lost == 0,
                       "no incidents lost or duplicated": incidents["lost"] == 0 and incidents["duplicated"] == 0,
                       "no escalations lost": out["escalations"]["lost"] == 0,
                       "all escalations within 1 min": out["escalations"]["late_over_60s"] == 0,
                       "all restarts succeeded": all(r["ok"] for r in state["restarts"])}
    return out
