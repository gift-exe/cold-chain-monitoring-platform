"""Simulation service command line.

    uv run python -m coldchain.sim sources [--gts DIR]           # derived extracts from data/ (once)
    uv run python -m coldchain.sim network [CONFIG]              # build and summarise the network
    uv run python -m coldchain.sim run CONFIG [--readings]       # one run offline; outputs in runs/<name>/
    uv run python -m coldchain.sim register CONFIG [--retire]    # register the network's devices in the database
    uv run python -m coldchain.sim emulate CONFIG [--now] [--speed X] [--connections N]
                                                                 # publish a run's readings over MQTT
    uv run python -m coldchain.sim calibrate [--days 30]         # Table 3.13 checks; runs/calibration/
"""
import argparse
import json
import sys
import time
from pathlib import Path

from coldchain.sim import config as config_mod

ROOT = Path(__file__).resolve().parents[3]


def _config(path: str | None):
    if path:
        return config_mod.load(path)
    return config_mod.from_dict({"name": "default", "purpose": "demo", "seed": 900})


def cmd_network(args) -> None:
    from coldchain.sim.network import build_for
    print(json.dumps(build_for(_config(args.config)).counts(), indent=2))


def _outputs(out: Path, cfg, net, sim, began: float, sink) -> dict:
    (out / "config.toml").write_text(config_mod.dump(cfg))
    net.write(out / "sites.csv", out / "units.csv")
    _write_csv(out / "ground_truth.csv", sim.ground_truth())
    _write_csv(out / "conditions.csv", sim.conditions)
    _write_csv(out / "trips.csv", [t.row() for t in sim.trips])
    _write_csv(out / "connectivity.csv", sim.gaps)
    summary = {**sim.summary(), "wall_seconds": round(time.monotonic() - began, 1)}
    if hasattr(sink, "stats"):
        summary["mqtt"] = dict(sink.stats)
    elif hasattr(sink, "readings"):
        summary["readings"] = sink.readings
    (out / "summary.json").write_text(json.dumps(summary, indent=2))
    return summary


def cmd_run(args) -> None:
    from coldchain.sim.engine import CountingSink, CsvSink, Simulation
    from coldchain.sim.network import build_for
    cfg = config_mod.load(args.config)
    out = Path(args.out) if args.out else ROOT / "runs" / cfg.name
    out.mkdir(parents=True, exist_ok=True)
    net = build_for(cfg)
    sink = CsvSink(out / "readings.csv.gz") if args.readings or cfg["output"]["readings"] else CountingSink()
    began = time.monotonic()
    sim = Simulation(cfg, net, sink).run()
    if hasattr(sink, "close"):
        sink.close()
    print(json.dumps(_outputs(out, cfg, net, sim, began, sink), indent=2))
    print(f"outputs in {out}")


def _db():
    import psycopg

    from coldchain.config import get_settings
    return psycopg.connect(get_settings().db_url("owner").replace("postgresql+psycopg://", "postgresql://"))


def cmd_register(args) -> None:
    from coldchain.sim import register
    from coldchain.sim.network import build_for
    net = build_for(config_mod.load(args.config))
    with _db() as conn:
        if args.retire:
            print(f"{register.retire(conn, net)} devices retired")
        else:
            print(register.register(conn, net))


def cmd_emulate(args) -> None:
    from datetime import UTC, datetime

    from coldchain.config import get_settings
    from coldchain.sim.emulator import MqttSink
    from coldchain.sim.engine import Simulation
    from coldchain.sim.network import build_for
    cfg = config_mod.load(args.config)
    if args.now:      # the run starts at the current minute, so event times are live
        now = datetime.now(UTC).replace(second=0, microsecond=0)
        cfg = config_mod.from_dict({**cfg.values, "name": cfg.name, "purpose": cfg.purpose, "seed": cfg.seed,
                                    "start": now.isoformat()})
    net = build_for(cfg)
    s = get_settings()
    interval = net.units[0].interval_s
    sink = MqttSink(s.mqtt_host, s.mqtt_port, [u.serial for u in net.units],
                    args.connections or max(1, len(net.units) // 10), client_prefix=f"sim-{cfg.name}",
                    topic_prefix=args.topic_prefix, paced_interval_s=interval / args.speed if args.speed else None)
    out = Path(args.out) if args.out else ROOT / "runs" / cfg.name
    out.mkdir(parents=True, exist_ok=True)
    print(f"{len(net.units)} devices over {sink.n} connections; "
          f"{'real time x' + str(args.speed) if args.speed else 'as fast as possible'}", flush=True)
    began = time.monotonic()
    sim = Simulation(cfg, net, sink, realtime_factor=60 / args.speed if args.speed else None).run()
    sink.close()
    print(json.dumps(_outputs(out, cfg, net, sim, began, sink), indent=2))


def _write_csv(path: Path, rows: list[dict]) -> None:
    import csv
    with path.open("w", newline="") as f:
        if not rows:
            return
        w = csv.DictWriter(f, list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="python -m coldchain.sim")
    sub = p.add_subparsers(dest="cmd", required=True)
    so = sub.add_parser("sources")
    so.add_argument("--gts", help="GTS export folder (Tracks/*.csv); needs: uv run --with duckdb")
    n = sub.add_parser("network")
    n.add_argument("config", nargs="?")
    r = sub.add_parser("run")
    r.add_argument("config")
    r.add_argument("--out")
    r.add_argument("--readings", action="store_true", help="write every reading to readings.csv.gz")
    g = sub.add_parser("register")
    g.add_argument("config")
    g.add_argument("--retire", action="store_true", help="retire the network's devices instead")
    e = sub.add_parser("emulate")
    e.add_argument("config")
    e.add_argument("--now", action="store_true", help="start the run at the current minute")
    e.add_argument("--speed", type=float, help="simulated minutes per real minute (1 = real time); "
                                               "omit to publish as fast as possible")
    e.add_argument("--connections", type=int, help="MQTT connections (default: one per 10 devices)")
    e.add_argument("--topic-prefix", default="devices")
    e.add_argument("--out")
    c = sub.add_parser("calibrate")
    c.add_argument("--days", type=int, default=30)
    args = p.parse_args(argv)
    if args.cmd == "sources":
        from coldchain.sim.sources import main as sources
        sources(args.gts)
    elif args.cmd == "network":
        cmd_network(args)
    elif args.cmd == "run":
        cmd_run(args)
    elif args.cmd == "register":
        cmd_register(args)
    elif args.cmd == "emulate":
        cmd_emulate(args)
    elif args.cmd == "calibrate":
        from coldchain.sim.calibrate import main as calibrate
        sys.exit(0 if calibrate(days=args.days) else 1)


if __name__ == "__main__":
    main()
