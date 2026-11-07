"""Evaluation runner (Section 3.2.7, Table 3.10).

    uv run python -m coldchain.evaluation replay [--seeds 101-110] [--no-reset]
    uv run python -m coldchain.evaluation report
    uv run python -m coldchain.evaluation e7            (E7; evaluation stack, switched to live mode)
    uv run python -m coldchain.evaluation e4            (E4; evaluation stack, reset and switched to live mode)
    uv run python -m coldchain.evaluation e6            (E6; evaluation stack, reset and switched to live mode)
    uv run python -m coldchain.evaluation e11           (E11; evaluation stack, reset, live; restarts services)
    uv run python -m coldchain.evaluation e5            (E5; any database with the seed data, e.g. make e5-db)
    uv run python -m coldchain.evaluation coverage      (E12; development stack running)

replay: for each test seed of the detection scenario set (Table 3.14), reset the evaluation stack, register
the Kano network, switch to accelerated replay (compose.replay.yaml), replay the 7-day run through the MQTT
broker as fast as the pipeline accepts it, wait until every service has processed everything, and score E1,
E2 and E3. Each replication starts from an empty stack, so replications are independent.
Results: runs/eval/detection/<run>/ (simulator outputs, score.json, anomaly.json, pipeline.json).

report: aggregates the replications into runs/eval/detection/summary.json and report.md.

The development stack must be stopped first (the evaluation stack uses the same ports).
"""
import argparse
import json
import random
import subprocess
import sys
import time
from pathlib import Path

import psycopg

from coldchain.config import get_settings
from coldchain.evaluation import anomaly as e3
from coldchain.evaluation import detection as e12
from coldchain.sim import config as config_mod

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "runs" / "eval" / "detection"
TEMPLATE = ROOT / "configs" / "sim" / "detection-101.toml"
REPLAY_RATE = 3000   # messages/s: below the bridge's ~5,000/s, so the broker's queue for it cannot overflow


def sh(cmd: str, attempts: int = 1) -> str:
    print(f"$ {cmd}", flush=True)
    for attempt in range(1, attempts + 1):
        p = subprocess.run(cmd, check=False, shell=True, cwd=ROOT, capture_output=True, text=True)
        if p.returncode == 0:
            return p.stdout
        print(f"  failed (attempt {attempt}/{attempts}): {(p.stdout + p.stderr)[-1500:]}", flush=True)
        time.sleep(30)
    raise RuntimeError(f"{cmd} failed")


def _db() -> psycopg.Connection:
    return psycopg.connect(get_settings().db_url("app").replace("postgresql+psycopg://", "postgresql://"))


def lag() -> dict[str, int]:
    out = sh("podman exec coldchain-eval-kafka-1 /opt/kafka/bin/kafka-consumer-groups.sh "
             "--bootstrap-server localhost:19092 --describe --all-groups 2>/dev/null || true")
    total: dict[str, int] = {}
    for line in out.splitlines():
        f = line.split()
        if len(f) >= 6 and f[5].isdigit():
            total[f[0]] = total.get(f[0], 0) + int(f[5])
    return total


def drain(expected: int, poll: float = 15, quiet: int = 3) -> dict:
    """Wait until all readings are stored and no consumer group has a backlog for `quiet` polls in a row."""
    began, still = time.monotonic(), 0
    last = (0, 0, 0)
    while still < quiet or (last[0] < expected and still < 8):   # a shortfall is recorded, not waited for
        time.sleep(poll)
        with _db() as conn:
            stored, events, incidents = conn.execute(
                "SELECT (SELECT count(*) FROM reading), (SELECT count(*) FROM integrity_event), "
                "(SELECT count(*) FROM incident)").fetchone()
        backlog = lag()
        state = (stored, events, incidents)
        done = not any(backlog.values())
        still = still + 1 if done and state == last else 0
        last = state
        print(f"  {time.monotonic() - began:6.0f} s  stored {stored}/{expected}  events {events}  "
              f"incidents {incidents}  lag {backlog}", flush=True)
    return {"stored": last[0], "shortfall": max(0, expected - last[0]), "integrity_events": last[1], "incidents": last[2],
            "drain_seconds": round(time.monotonic() - began - poll * still, 1)}


def replicate(seed: int, reset: bool) -> None:
    from coldchain.sim.__main__ import _outputs
    from coldchain.sim.emulator import MqttSink
    from coldchain.sim.engine import Simulation
    from coldchain.sim.network import build_for

    base = config_mod.load(TEMPLATE)
    cfg = config_mod.from_dict({**base.values, "name": f"detection-{seed}", "purpose": "test", "seed": seed})
    out = OUT / cfg.name
    out.mkdir(parents=True, exist_ok=True)
    if reset:
        sh("make eval-reset", attempts=2)
        sh(f"make eval-up EVAL_NET={TEMPLATE}", attempts=3)   # a slow health check occasionally times out
    sh("make eval-replay", attempts=3)
    net = build_for(cfg)
    s = get_settings()
    sink = MqttSink(s.mqtt_host, s.mqtt_port, [u.serial for u in net.units], max(1, len(net.units) // 10),
                    client_prefix=f"eval-{cfg.name}", max_rate=REPLAY_RATE)
    began = time.monotonic()
    sim = Simulation(cfg, net, sink).run()
    sink.close(timeout=600)
    summary = _outputs(out, cfg, net, sim, began, sink)
    sent = time.monotonic() - began
    print(f"{cfg.name}: {summary['mqtt']['readings']} readings sent in {sent:.0f} s; waiting for the pipeline",
          flush=True)
    pipeline = {"send_seconds": round(sent, 1), **drain(summary["mqtt"]["readings"]),
                "broker_drop_warnings": sh("podman logs coldchain-eval-mosquitto-1 2>&1 | "
                                           "grep -c 'messages are being dropped' || true").strip()}
    (out / "pipeline.json").write_text(json.dumps(pipeline, indent=2))
    with _db() as conn:
        e12.save_observed(out, conn)
        (out / "score.json").write_text(json.dumps(e12.score(out, conn), indent=2, default=str))
        (out / "anomaly.json").write_text(json.dumps(e3.score(out, conn), indent=2, default=str))
    print(f"{cfg.name}: scored", flush=True)


def report() -> None:
    runs = sorted(p for p in OUT.glob("detection-*") if (p / "anomaly.json").exists())
    scores = [json.loads((p / "score.json").read_text()) for p in runs]
    anomalies = [json.loads((p / "anomaly.json").read_text()) for p in runs]
    pipelines = [json.loads((p / "pipeline.json").read_text()) for p in runs]
    doc = {"runs": [p.name for p in runs], "e1_e2": e12.aggregate(scores), "e3": e3.aggregate(anomalies),
           "pipeline": pipelines}
    (OUT / "summary.json").write_text(json.dumps(doc, indent=2))
    print(json.dumps(doc, indent=2))


def run_e4(args) -> None:
    from coldchain.evaluation import latency
    from coldchain.sim.network import build_for
    if not args.no_reset:
        sh("make eval-reset", attempts=2)
        sh(f"make eval-up EVAL_NET={TEMPLATE}", attempts=3)
    sh("make eval-live", attempts=3)
    bg, log = _live_network(latency.OUT)
    try:
        time.sleep(120)   # let the network settle into real-time reporting
        net = build_for(config_mod.load(TEMPLATE))
        facilities = [s.id for s in net.sites if s.tier == "health_facility"]
        state = latency.run(facilities, n=args.loggers)
        doc = latency.check(state)
    finally:
        bg.terminate()
        bg.wait(timeout=60)
        log.close()
    (latency.OUT / "latency.json").write_text(json.dumps(doc, indent=2, default=str))
    print(json.dumps({k: v for k, v in doc.items() if not k.endswith("_rows")}, indent=2, default=str))


def _live_network(out: Path) -> tuple[subprocess.Popen, object]:
    """The Kano network streaming in real time (one day of the detection template, starting now)."""
    out.mkdir(parents=True, exist_ok=True)
    cfg = out / "network.toml"
    cfg.write_text("\n".join(f'name = "{out.name}-network"' if ln.startswith("name =")
                             else "days = 1" if ln.startswith("days =") else ln
                             for ln in TEMPLATE.read_text().splitlines()) + "\n")
    log = open(out / "network.log", "w")  # noqa: SIM115 - held open for the subprocess's lifetime
    bg = subprocess.Popen(["uv", "run", "python", "-m", "coldchain.sim", "emulate", str(cfg), "--now", "--speed", "1",
                           "--out", str(out / "network")], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
    return bg, log


def run_e6(args) -> None:
    from coldchain.evaluation import handoffs
    from coldchain.sim.network import build_for
    if not args.no_reset:
        sh("make eval-reset", attempts=2)
        sh(f"make eval-up EVAL_NET={TEMPLATE}", attempts=3)
    sh("make eval-live", attempts=3)
    net = build_for(config_mod.load(TEMPLATE))
    facilities = [s.id for s in net.sites if s.tier == "health_facility"]
    random.Random(6).shuffle(facilities)
    with _db() as conn:
        receivers = [r for (r,) in conn.execute(
            "SELECT id::text FROM app_user WHERE role = 'outreach_lead' AND active ORDER BY username")]
    lo, _, hi = args.seeds.partition("-")
    state = handoffs.run(range(int(lo), int(hi or lo) + 1), facilities, receivers)
    handoffs.OUT.mkdir(parents=True, exist_ok=True)
    (handoffs.OUT / "state.json").write_text(json.dumps(state, indent=2, default=str))
    doc = handoffs.check(state)
    (handoffs.OUT / "custody.json").write_text(json.dumps(doc, indent=2, default=str))
    print(json.dumps(doc["totals"], indent=2))
    for r in doc["incidents"]:
        if not (r.get("owner_ok") and r.get("cc_ok")):
            print(json.dumps(r, default=str))


def run_e11(args) -> None:
    from coldchain.evaluation import resilience
    from coldchain.sim.network import build_for
    if not args.no_reset:
        sh("make eval-reset", attempts=2)
        sh(f"make eval-up EVAL_NET={TEMPLATE}", attempts=3)
    sh("make eval-live", attempts=3)
    bg, log = _live_network(resilience.OUT)
    try:
        time.sleep(120)
        net = build_for(config_mod.load(TEMPLATE))
        facilities = [s.id for s in net.sites if s.tier == "health_facility"]
        random.Random(11).shuffle(facilities)        # spread the incidents over many LGAs
        state = resilience.run(facilities)
        (resilience.OUT / "state.json").write_text(json.dumps(state, indent=2, default=str))
        doc = resilience.check(state)
    finally:
        bg.terminate()
        bg.wait(timeout=60)
        log.close()
    (resilience.OUT / "resilience.json").write_text(json.dumps(doc, indent=2, default=str))
    print(json.dumps({k: v for k, v in doc.items() if k != "escalation_rows"}, indent=2, default=str))


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="coldchain.evaluation")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("replay")
    r.add_argument("--seeds", default="101-110")
    r.add_argument("--no-reset", action="store_true", help="replay into the running stack as it is")
    sub.add_parser("report")
    e7 = sub.add_parser("e7", help="E7 offline recovery on the evaluation stack in live mode")
    e7.add_argument("--seeds", default="101-110")
    e5 = sub.add_parser("e5", help="E5 escalation with scripted responders (time-skipping Temporal server)")
    e5.add_argument("--seeds", default="101-110")
    e4 = sub.add_parser("e4", help="E4 notification latency, live, with the Kano network streaming in real time")
    e4.add_argument("--loggers", type=int, default=100)
    e4.add_argument("--no-reset", action="store_true")
    e6 = sub.add_parser("e6", help="E6 custody correctness: handoff scenarios, readings through the complete chain")
    e6.add_argument("--seeds", default="101-110")
    e6.add_argument("--no-reset", action="store_true")
    e11 = sub.add_parser("e11", help="E11 fault tolerance, live: services restarted during escalations and load")
    e11.add_argument("--no-reset", action="store_true")
    sub.add_parser("coverage", help="E12: run the test suite (development stack) and score tests/requirements.toml")
    args = ap.parse_args(argv)
    if args.cmd == "replay":
        lo, _, hi = args.seeds.partition("-")
        for seed in range(int(lo), int(hi or lo) + 1):
            replicate(seed, reset=not args.no_reset)
        report()
    elif args.cmd == "e7":
        from coldchain.evaluation import offline
        from coldchain.sim.network import build_for
        lo, _, hi = args.seeds.partition("-")
        sh("make eval-live")
        net = build_for(config_mod.load(TEMPLATE))
        facility = next(x.id for x in net.sites if x.tier == "health_facility" and x.lga == "Gwale")
        doc = offline.check(offline.run(range(int(lo), int(hi or lo) + 1), facility))
        offline.OUT.mkdir(parents=True, exist_ok=True)
        (offline.OUT / "offline.json").write_text(json.dumps(doc, indent=2))
        print(json.dumps(doc["totals"], indent=2))
    elif args.cmd == "e4":
        run_e4(args)
    elif args.cmd == "e6":
        run_e6(args)
    elif args.cmd == "e11":
        run_e11(args)
    elif args.cmd == "e5":
        from coldchain.evaluation import escalation
        lo, _, hi = args.seeds.partition("-")
        doc = escalation.run(range(int(lo), int(hi or lo) + 1))
        escalation.OUT.mkdir(parents=True, exist_ok=True)
        (escalation.OUT / "escalation.json").write_text(json.dumps(doc, indent=2, default=str))
        print(json.dumps({k: v for k, v in doc.items() if k not in ("results", "wrong")}, indent=2))
        for w in doc["wrong"][:10]:
            print(json.dumps(w, default=str))
    elif args.cmd == "coverage":
        from coldchain.evaluation import coverage
        doc = coverage.score(coverage.outcomes(coverage.run_tests()))
        (coverage.OUT / "coverage.json").write_text(json.dumps(doc, indent=2))
        for r in doc["rows"]:
            bad = {t: v for t, v in r["tests"].items() if v != "passed"}
            print(f"{r['requirement']:7} {'covered' if r['covered'] else 'NOT covered':12} {r['status']} {bad or ''}")
        print(f"{doc['covered']}/{doc['in_scope']} in-scope requirements covered ({doc['coverage']:.1%})")
    else:
        report()


if __name__ == "__main__":
    sys.exit(main())
