"""Simulation service command line.

    uv run python -m coldchain.sim sources                       # derived extracts from data/ (once)
    uv run python -m coldchain.sim network [CONFIG]              # build and summarise the network
    uv run python -m coldchain.sim run CONFIG [--readings]       # one run; outputs in runs/<name>/
    uv run python -m coldchain.sim calibrate [--days 30]         # Table 3.13 checks for I7a
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
    from coldchain.sim.network import build
    cfg = _config(args.config)
    net = build(cfg["network"], cfg["network"]["seed"])
    print(json.dumps(net.counts(), indent=2))


def cmd_run(args) -> None:
    from coldchain.sim.engine import CountingSink, CsvSink, Simulation
    from coldchain.sim.network import build
    cfg = config_mod.load(args.config)
    out = Path(args.out) if args.out else ROOT / "runs" / cfg.name
    out.mkdir(parents=True, exist_ok=True)
    net = build(cfg["network"], cfg["network"]["seed"])
    sink = CsvSink(out / "readings.csv.gz") if args.readings or cfg["output"]["readings"] else CountingSink()
    began = time.monotonic()
    sim = Simulation(cfg, net, sink).run()
    if hasattr(sink, "close"):
        sink.close()
    (out / "config.toml").write_text(config_mod.dump(cfg))
    net.write(out / "sites.csv", out / "units.csv")
    _write_csv(out / "ground_truth.csv", sim.ground_truth())
    _write_csv(out / "conditions.csv", sim.conditions)
    summary = {**sim.summary(), "wall_seconds": round(time.monotonic() - began, 1)}
    if isinstance(sink, CountingSink):
        summary["readings"] = sink.readings
    (out / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))
    print(f"outputs in {out}")


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
    sub.add_parser("sources")
    n = sub.add_parser("network")
    n.add_argument("config", nargs="?")
    r = sub.add_parser("run")
    r.add_argument("config")
    r.add_argument("--out")
    r.add_argument("--readings", action="store_true", help="write every reading to readings.csv.gz")
    c = sub.add_parser("calibrate")
    c.add_argument("--days", type=int, default=30)
    args = p.parse_args(argv)
    if args.cmd == "sources":
        from coldchain.sim.sources import main as sources
        sources()
    elif args.cmd == "network":
        cmd_network(args)
    elif args.cmd == "run":
        cmd_run(args)
    elif args.cmd == "calibrate":
        from coldchain.sim.calibrate import main as calibrate
        sys.exit(0 if calibrate(days=args.days) else 1)


if __name__ == "__main__":
    main()
