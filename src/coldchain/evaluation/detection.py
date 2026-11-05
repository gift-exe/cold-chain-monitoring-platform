"""E1 detection and E2 false alarms (Table 3.10): the prototype's integrity events and incidents for one replayed
scenario run, compared with the simulator's ground truth.

Ground truth is the simulator's log of true conditions (conditions.csv): every window in which the true
temperature, power supply or door state met a condition, with the injected failure that caused it, if any.
Conditions are judged against the prototype's own rules (Table 3.17), so that recall measures detection and
not a difference of definitions:

- a condition *qualifies* if it lasted at least the rule's tolerance (heat 30 min above 8 °C; freeze 5 min
  at or below 0 °C; power 10 min absent; door 5 min open). An injected failure whose condition never
  qualified (a fault in a unit that was already cold, or one too short to matter) is counted and reported,
  but it is not an event the prototype should have detected;
- E1 recall: an injected failure is detected if an integrity event of the same type on the same unit,
  linked to an incident, starts within 10 minutes of the start of the qualifying condition it caused
  (strict). Also reported (overlap): the event overlaps the condition and starts no later than 10 minutes
  after it; it differs from strict only for events that start more than 10 minutes *early*, which happens
  when a slow cool-down or warm-up hovers at the limit and sensor noise crosses it first. (Table
  3.10 says "of the injected start"; the condition's start is used because a fault can take hours to drive
  the temperature out of range, e.g. a stuck thermostat, and the prototype cannot detect a condition before
  it exists.)
- E2 precision: an incident is a true alarm if one of its events overlaps a true condition of the same kind
  on the same unit, of any cause: the simulator also produces natural excursions (degraded units, long
  power cuts), and an alert about a real excursion is not false. Table 3.10's literal definition (incidents
  matching an *injected* event ÷ all critical incidents) is reported alongside.
"""
import csv
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import psycopg

from coldchain.evaluation import wilson

MATCH = timedelta(minutes=10)
SLACK = timedelta(minutes=10)      # overlap tolerance for precision (sensor noise around a threshold)
# integrity event type -> (condition kind in conditions.csv, rule tolerance)
RULES = {"heat_excursion": ("above_upper", timedelta(minutes=30)),
         "freeze": ("at_or_below_zero", timedelta(minutes=5)),
         "power_loss": ("no_power", timedelta(minutes=10)),
         "door_open": ("door_open", timedelta(minutes=5))}
KIND_TO_TYPE = {kind: t for t, (kind, _) in RULES.items()}


def _t(s: str) -> datetime:
    return datetime.fromisoformat(s)


def _read(path: Path) -> list[dict]:
    with path.open() as f:
        return list(csv.DictReader(f))


def observed(conn: psycopg.Connection, serials: list[str]) -> tuple[list[dict], dict[str, dict]]:
    """Integrity events (with their unit's serial) and incidents, for the run's devices."""
    events = conn.execute("""
        SELECT e.id::text, d.serial_no, e.type, e.severity, e.event_start, e.event_end, e.incident_id::text
        FROM integrity_event e JOIN device d ON d.id = e.device_id
        WHERE d.serial_no = ANY(%s) AND e.type = ANY(%s)""", (serials, list(RULES))).fetchall()
    ev = [dict(zip(("id", "serial", "type", "severity", "start", "end", "incident"), r, strict=True)) for r in events]
    incidents = conn.execute("""
        SELECT i.id::text, d.serial_no, i.type, i.severity, i.mode FROM incident i JOIN device d ON d.id = i.device_id
        WHERE d.serial_no = ANY(%s) AND i.type = ANY(%s)""", (serials, list(RULES))).fetchall()
    inc = {r[0]: dict(zip(("id", "serial", "type", "severity", "mode"), r, strict=True)) for r in incidents}
    return ev, inc


def save_observed(run_dir: Path, conn: psycopg.Connection) -> None:
    """Keep the prototype's events and incidents with the run, so it can be rescored without the database."""
    import json
    serials = [u["serial"] for u in _read(run_dir / "units.csv")]
    events, incidents = observed(conn, serials) if conn is not None else _load_observed(run_dir)
    (run_dir / "observed.json").write_text(json.dumps({"events": events, "incidents": incidents}, default=str))


def _load_observed(run_dir: Path) -> tuple[list[dict], dict[str, dict]]:
    import json
    doc = json.loads((run_dir / "observed.json").read_text())
    for e in doc["events"]:
        e["start"] = _t(e["start"])
        e["end"] = _t(e["end"]) if e["end"] else None
    return doc["events"], doc["incidents"]


def score(run_dir: Path, conn: psycopg.Connection | None = None) -> dict:
    summary_units = _read(run_dir / "units.csv")
    serials = [u["serial"] for u in summary_units]
    conditions = [c for c in _read(run_dir / "conditions.csv") if c["kind"] in KIND_TO_TYPE]
    injections = [g for g in _read(run_dir / "ground_truth.csv") if g["type"] in RULES]
    events, incidents = observed(conn, serials) if conn is not None else _load_observed(run_dir)
    end_of_run = max(_t(c["end"]) for c in conditions) if conditions else None

    by_unit_type = defaultdict(list)
    for e in events:
        by_unit_type[(e["serial"], e["type"])].append(e)
    cond_by_unit_kind = defaultdict(list)
    for c in conditions:
        cond_by_unit_kind[(c["serial"], c["kind"])].append(c)

    # E1 ------------------------------------------------------------------------------------------------
    recall = defaultdict(Counter)
    misses = []
    for g in injections:
        kind, tolerance = RULES[g["type"]]
        caused = sorted((c for c in cond_by_unit_kind[(g["serial"], kind)] if g["id"] in c["cause"].split(";")),
                        key=lambda c: c["start"])
        qualifying = [c for c in caused
                      if _t(c["end"]) - _t(c["start"]) >= tolerance and not (
                          c["open_at_end"] == "True" and _t(c["end"]) - _t(c["start"]) < tolerance)]
        r = recall[g["type"]]
        r["injected"] += 1
        if not qualifying:
            r["no_qualifying_condition"] += 1
            continue
        r["events"] += 1
        c0 = _t(qualifying[0]["start"])
        cands = [e for e in by_unit_type[(g["serial"], g["type"])] if e["incident"]]
        # overlap: the event overlaps the condition and began no later than 10 minutes after it (an event
        # that began earlier, because noisy readings crossed the limit first, still detected it)
        overlap = [e for e in cands if e["start"] <= c0 + MATCH and (e["end"] or end_of_run) >= c0]
        r["detected_overlap"] += bool(overlap)
        if overlap:
            r["early_minutes_sum"] += max(0.0, (c0 - min(e["start"] for e in overlap)).total_seconds() / 60)
        hit = [e for e in cands if abs(e["start"] - c0) <= MATCH]
        if hit:
            r["detected"] += 1
        else:
            near = min(by_unit_type[(g["serial"], g["type"])], key=lambda e: abs(e["start"] - c0), default=None)
            misses.append({"injection": g["id"], "serial": g["serial"], "condition_start": c0.isoformat(),
                           "nearest_event_start": near["start"].isoformat() if near else None,
                           "nearest_event_incident": bool(near and near["incident"]),
                           "event_found_but_no_incident": bool(hit)})

    # E2 ------------------------------------------------------------------------------------------------
    def real(e) -> tuple[bool, bool]:
        """(overlaps a true condition, overlaps an injected one)"""
        kind = RULES[e["type"]][0]
        e_end = e["end"] or end_of_run
        overlapping = [c for c in cond_by_unit_kind[(e["serial"], kind)]
                       if _t(c["start"]) <= e_end + SLACK and _t(c["end"]) >= e["start"] - SLACK]
        return bool(overlapping), any(c["cause"] != "natural" for c in overlapping)

    per_incident = defaultdict(lambda: [False, False])
    for e in events:
        if e["incident"]:
            is_real, injected = real(e)
            p = per_incident[e["incident"]]
            p[0] |= is_real
            p[1] |= injected
    precision = defaultdict(Counter)
    false_alarms = []
    for iid, inc in incidents.items():
        is_real, injected = per_incident.get(iid, (False, False))
        for key in (inc["severity"], f"{inc['severity']}:{inc['type']}"):
            p = precision[key]
            p["incidents"] += 1
            p["true"] += is_real
            p["injected"] += injected
        if not is_real:
            false_alarms.append({"incident": iid, **{k: inc[k] for k in ("serial", "type", "severity", "mode")}})

    unit_days = float(_summary(run_dir)["unit_days"])
    crit = precision["critical"]
    return {
        "run": run_dir.name,
        "unit_days": unit_days,
        "recall": {t: dict(c) for t, c in recall.items()},
        "precision": {k: dict(c) for k, c in sorted(precision.items())},
        "false_critical_per_1000_device_days": round(1000 * (crit["incidents"] - crit["true"]) / unit_days, 3),
        "misses": misses,
        "false_alarms": false_alarms,
        "events_without_incident": sum(1 for e in events if not e["incident"]),
    }


def _summary(run_dir: Path) -> dict:
    import json
    return json.loads((run_dir / "summary.json").read_text())


def aggregate(results: list[dict]) -> dict:
    """Totals over replications, with Wilson intervals (Table 3.10: recall >= 0.95, precision >= 0.90)."""
    rec, prec = defaultdict(Counter), defaultdict(Counter)
    for r in results:
        for t, c in r["recall"].items():
            rec[t].update(c)
        for k, c in r["precision"].items():
            prec[k].update(c)
    critical_types = ("heat_excursion", "freeze")
    crit = Counter()
    for t in critical_types:
        crit.update(rec[t])
    unit_days = sum(r["unit_days"] for r in results)
    out = {"replications": len(results), "unit_days": unit_days, "recall": {}, "precision": {}}
    for t, c in [*sorted(rec.items()), ("critical (heat + freeze)", crit)]:
        out["recall"][t] = {**c, "recall": round(c["detected"] / c["events"], 4) if c["events"] else None,
                            "ci95": wilson(c["detected"], c["events"]),
                            "recall_overlap": round(c["detected_overlap"] / c["events"], 4) if c["events"] else None,
                            "ci95_overlap": wilson(c["detected_overlap"], c["events"]),
                            "mean_early_minutes": round(c["early_minutes_sum"] / c["detected_overlap"], 1)
                            if c["detected_overlap"] else None}
    for k, c in sorted(prec.items()):
        out["precision"][k] = {**c, "precision": round(c["true"] / c["incidents"], 4) if c["incidents"] else None,
                               "ci95": wilson(c["true"], c["incidents"]),
                               "precision_injected_only": round(c["injected"] / c["incidents"], 4)
                               if c["incidents"] else None}
    pc = prec["critical"]
    out["false_critical_per_1000_device_days"] = round(1000 * (pc["incidents"] - pc["true"]) / unit_days, 3)
    out["criteria"] = {
        "E1 recall >= 0.95 (critical, strict)": bool(crit["events"]) and crit["detected"] / crit["events"] >= 0.95,
        "E1 recall >= 0.95 (critical, overlap)": bool(crit["events"])
        and crit["detected_overlap"] / crit["events"] >= 0.95,
        "E2 precision >= 0.90 (critical)": bool(pc["incidents"]) and pc["true"] / pc["incidents"] >= 0.90}
    return out
