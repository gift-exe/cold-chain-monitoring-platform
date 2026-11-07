"""E10 scalability (Table 3.10, NFR1, NFR2): does the prototype sustain national-scale load? Live, real time.

The load network of Table 3.7 (14,500 fixed and 5,000 mobile devices; configs/sim/load-101.toml) is registered
on the evaluation stack, and every device reports live readings over MQTT, spread over 2,000 connections:

1. Target: 1,000 readings a second for 30 minutes (each device every 19.5 seconds), about three times the
   estimated peak, NFR1.
2. Endurance: the peak load of about 325 readings a second (each device once a minute) for 2 hours. During it,
   scripted excursions on further loggers (as in E4) measure notification latency under load, NFR2.

Each device numbers its readings, so any lost reading shows as a missing sequence number (Section 3.2.4).
Metrics: the rate at which readings are durably stored (from each reading's storage time, not the send rate);
readings lost; receipt-to-storage latency (median, p95); notification latency p95 for the scripted incidents;
event-log backlog at the end of each phase; container CPU and memory, sampled every minute (memory growth
during the endurance run). The load generator runs on the same machine as the prototype (Section 4.1.1), so
the measurements understate what the design achieves on the dedicated host of Table 3.23.
Criteria: 1,000 readings/s for 30 minutes with no loss; NFR2 (p95 <= 30 s) met during the endurance run.
"""
import subprocess
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

import psycopg

from coldchain.config import get_settings
from coldchain.sim.emulator import MqttSink

OUT = Path(__file__).resolve().parents[3] / "runs" / "eval" / "e10"
CONNECTIONS = 2000


def _url(role: str) -> str:
    return get_settings().db_url(role).replace("postgresql+psycopg://", "postgresql://")


def _stats(stop: threading.Event, rows: list) -> None:
    while not stop.wait(60):
        p = subprocess.run(["podman", "stats", "--no-stream", "--format", "{{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}"],
                           check=False, capture_output=True, text=True)
        at = datetime.now(UTC).isoformat()
        for line in p.stdout.splitlines():
            name, cpu, mem = (line.split("\t") + ["", "", ""])[:3]
            if name.startswith("coldchain-eval-"):
                rows.append({"at": at, "container": name.removeprefix("coldchain-eval-").removesuffix("-1"),
                             "cpu": cpu, "mem": mem.split(" / ")[0]})


def _generate(serials: list[str], phases: list[tuple[str, int, float]], state: dict) -> None:
    """Live readings: in each phase, every device reports every `interval` seconds, with its own sequence."""
    s = get_settings()
    sink = MqttSink(s.mqtt_host, s.mqtt_port, serials, CONNECTIONS, client_prefix="e10", max_pending=50000)
    seq = dict.fromkeys(serials, 0)
    n = len(serials)
    for name, minutes, interval in phases:
        began = time.monotonic()
        end = began + minutes * 60
        rate = n / interval
        sent0 = sink.stats["messages"]
        state.setdefault("phases", []).append({"name": name, "began": datetime.now(UTC).isoformat(),
                                               "minutes": minutes, "interval_s": interval, "offered_rate": rate})
        print(f"E10 {name}: {n} devices every {interval:.1f} s = {rate:.0f} readings/s for {minutes} min", flush=True)
        k = 0
        while (now := time.monotonic()) < end:
            due = began + k / rate                    # message k of this phase is due now
            if due > now:
                time.sleep(min(due - now, 0.05))
                continue
            serial = serials[k % n]
            seq[serial] += 1
            t = datetime.now(UTC)
            sink.send(serial, [{"seq_no": seq[serial], "event_time": t.isoformat(), "temp": 5.0,
                                "power_ok": True, "door_open": False}])
            k += 1
            if k % int(rate * 300) == 0:
                print(f"  {name}: {(now - began) / 60:.0f} min, {sink.stats['messages'] - sent0} sent", flush=True)
        state["phases"][-1].update(ended=datetime.now(UTC).isoformat(), sent=sink.stats["messages"] - sent0)
    sink.close(timeout=300)
    state["sent_per_device"] = seq
    state["unacked"] = sink.stats["unacked"]


def run(serials: list[str], facilities: list[str], target_min: int = 30, endurance_min: int = 120) -> dict:
    from coldchain.evaluation import latency
    OUT.mkdir(parents=True, exist_ok=True)
    state: dict = {"devices": len(serials)}
    stop, stats = threading.Event(), []
    threading.Thread(target=_stats, args=(stop, stats), daemon=True).start()
    phases = [("target", target_min, len(serials) / 1000.0), ("endurance", endurance_min, 60.0)]
    gen = threading.Thread(target=_generate, args=(serials, phases, state))
    gen.start()
    time.sleep(target_min * 60 + 60)                  # the scripted excursions run during the endurance phase
    spread = max(10, endurance_min - 40)
    state["latency"] = latency.run(facilities, n=100, spread_min=spread, seed=1010)
    gen.join()
    stop.set()
    state["resources"] = stats
    return state


def check(state: dict, settle_s: int = 180) -> dict:
    from coldchain.evaluation import latency
    time.sleep(settle_s)
    out = {"devices": state["devices"], "phases": state["phases"], "unacked": state.get("unacked")}
    with psycopg.connect(_url("owner")) as conn:
        conn.execute("SET statement_timeout = 0")
        sent = state["sent_per_device"]
        rows = conn.execute("""SELECT d.serial_no, count(DISTINCT r.seq_no), count(*)
                               FROM reading r JOIN device d ON d.id = r.device_id
                               WHERE d.serial_no = ANY(%s) GROUP BY d.serial_no""", (list(sent),)).fetchall()
        stored = {s: (distinct, total) for s, distinct, total in rows}
        out["readings"] = {"sent": sum(sent.values()),
                           "stored": sum(v[0] for v in stored.values()),
                           "lost": sum(max(0, n - stored.get(s, (0, 0))[0]) for s, n in sent.items()),
                           "duplicates": sum(v[1] - v[0] for v in stored.values())}
        for ph in out["phases"]:
            a, b = ph["began"], ph["ended"]
            # durable storage rate: readings whose storage completed inside the phase, per second of the phase
            per_s = conn.execute("""
                SELECT count(*) FROM reading r JOIN device d ON d.id = r.device_id
                WHERE d.serial_no = ANY(%s) AND r.processed_at >= %s AND r.processed_at < %s
                GROUP BY date_trunc('second', r.processed_at) ORDER BY 1""", (list(sent), a, b)).fetchall()
            rates = sorted(x for (x,) in per_s)
            dur = (datetime.fromisoformat(b) - datetime.fromisoformat(a)).total_seconds()
            lat = conn.execute("""
                SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM processed_at - received_at)),
                       percentile_cont(0.95) WITHIN GROUP (ORDER BY extract(epoch FROM processed_at - received_at)),
                       max(extract(epoch FROM processed_at - received_at)), count(*)
                FROM reading r JOIN device d ON d.id = r.device_id
                WHERE d.serial_no = ANY(%s) AND r.received_at >= %s AND r.received_at < %s""",
                (list(sent), a, b)).fetchone()
            ph["received"] = lat[3]
            ph["stored_in_phase"] = sum(rates)
            ph["stored_rate_mean"] = round(sum(rates) / max(1.0, dur), 1)
            ph["stored_rate_p5_per_s"] = rates[len(rates) // 20] if rates else 0
            ph["receipt_to_storage_s"] = {"median": round(lat[0] or 0, 3), "p95": round(lat[1] or 0, 3),
                                          "max": round(lat[2] or 0, 3)}
    out["notification"] = latency.check(state["latency"], settle_s=0)
    out["notification"] = {k: v for k, v in out["notification"].items() if not k.endswith("_rows")}
    out["resources"] = state["resources"]
    target = next(p for p in out["phases"] if p["name"] == "target")
    out["criteria"] = {
        "1,000 readings/s stored for 30 min": target["stored_rate_mean"] >= 990 and target["minutes"] >= 30,
        "no readings lost": out["readings"]["lost"] == 0,
        "NFR2 met during endurance (p95 <= 30 s)": out["notification"]["criteria"]["p95 <= 30 s"],
    }
    return out
