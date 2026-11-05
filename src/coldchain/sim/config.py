"""Run configuration (Section 3.3.5, Reproducibility): a TOML file naming the purpose, seed, network,
parameters and scenario. Values not given take the defaults in params.py; the full effective configuration
is written to the run's output so that the run can be repeated exactly from it.
"""
import copy
import tomllib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from coldchain.sim.params import DEFAULTS, SEED_SETS


@dataclass
class RunConfig:
    name: str
    purpose: str
    seed: int
    values: dict

    def __getitem__(self, key):
        return self.values[key]

    @property
    def start(self) -> datetime:
        return datetime.fromisoformat(self.values["start"])

    @property
    def minutes(self) -> int:
        return int(self.values["days"] * 1440)


def merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in override.items():
        if k not in out and k not in ("name", "purpose", "seed"):
            raise ValueError(f"unknown setting: {k}")
        out[k] = merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def from_dict(doc: dict) -> RunConfig:
    for k in ("name", "purpose", "seed"):
        if k not in doc:
            raise ValueError(f"configuration needs '{k}'")
    purpose, seed = doc["purpose"], int(doc["seed"])
    if purpose not in SEED_SETS:
        raise ValueError(f"purpose must be one of {sorted(SEED_SETS)}")
    if seed not in SEED_SETS[purpose]:
        r = SEED_SETS[purpose]
        raise ValueError(f"seed {seed} is not a {purpose} seed ({r.start}–{r.stop - 1}, Table 3.14)")
    values = merge(DEFAULTS, {k: v for k, v in doc.items() if k not in ("name", "purpose", "seed")})
    mix = values["network"]["power_mix"]
    if abs(sum(mix.values()) - 1) > 1e-9 or abs(sum(values["network"]["grid_bands"].values()) - 1) > 1e-9:
        raise ValueError("power_mix and grid_bands must each sum to 1")
    return RunConfig(doc["name"], purpose, seed, values)


def load(path: str | Path) -> RunConfig:
    return from_dict(tomllib.loads(Path(path).read_text()))


def dump(cfg: RunConfig) -> str:
    """The effective configuration as TOML (a small writer: tables, lists, strings, numbers, booleans)."""
    def scalar(v):
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, str):
            return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'
        if isinstance(v, list):
            return "[" + ", ".join(scalar(x) for x in v) + "]"
        return repr(v)

    lines = [f"name = {scalar(cfg.name)}", f"purpose = {scalar(cfg.purpose)}", f"seed = {cfg.seed}"]

    def table(prefix: str, d: dict):
        plain = [(k, v) for k, v in d.items() if not isinstance(v, dict)]
        nested = [(k, v) for k, v in d.items() if isinstance(v, dict)]
        if prefix and plain:
            lines.append(f"\n[{prefix}]")
        lines.extend(f"{k} = {scalar(v)}" for k, v in plain)
        for k, v in nested:
            table(f"{prefix}.{k}" if prefix else k, v)

    table("", cfg.values)
    return "\n".join(lines) + "\n"
