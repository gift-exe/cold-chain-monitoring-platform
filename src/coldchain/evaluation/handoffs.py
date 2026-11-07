"""E6 custody correctness (Table 3.10): is each event in transit assigned to the custodian at the time it began
(Table 3.16)? Anchored to the current time, through the complete chain.

Each replication (seeds 101-110) sends seven consignments out from Kano health facilities, each with its own
vaccine-carrier logger, from the facility's officer-in-charge to an outreach lead, over the six hours before
the test. The custody history is recorded through the prototype's own custody operations, each at the time it
happened, and every handoff's confirmation window is watched by the prototype's own handoff workflow, exactly
as the API does (a window that ended before the handoff was settled raises its custody gap first). The
loggers then upload their six hours of readings through the MQTT broker, with scripted excursions, so that
detection, the incident service and ownership run as in operation.

Scenarios (minutes from the consignment's creation at t0 = six hours before the upload; transit 60 minutes,
so the confirmation window ends 90 minutes after release, CR4). The expected owner is written here from
Table 3.16, not computed by the prototype's ownership code:

  normal-during      release 10, confirm 70; heat 30-65        -> releaser, receiver copied (CR2, CR6)
  normal-after       release 10, confirm 70; heat 120-165      -> receiver (CR3, CR6)
  retrospective      release 60, confirm 120; heat 5-50        -> releaser: custodian when it began (CR6)
  ongoing            release 10, confirm 70; heat from 40 minutes before the upload, still under way
                                                                -> receiver; operational (CR6)
  incomplete         release 10, never confirmed; heat 150-195 -> releaser, receiver copied; custody gap (CR2, CR4)
  rejected           release 10, rejected 70; heat 120-165     -> releaser; custody gap (CR5)
  delayed            release 10, confirmed 200 (window ended 100); heat 130-175 -> releaser, receiver copied,
                     custody gap from 100 until 200; freeze 240-260 -> receiver (CR3, CR4)

Metric: the proportion of incidents whose owner is the expected one and whose intended receiver is copied
exactly when Table 3.16 says so (others may be copied too: a retrospective incident's digest always goes to
the LGA officer as well, Table 3.15); custody gaps raised
where, and only where, expected. Criterion: 100%.
"""
import asyncio
import json
import random
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg

from coldchain import custody
from coldchain.config import get_settings
from coldchain.incidents import service as incidents
from coldchain.sim.register import user_id
from coldchain.simulator.basic import SimDevice, connect, topic

OUT = Path(__file__).resolve().parents[3] / "runs" / "eval" / "e6"
MIN = timedelta(minutes=1)
SPAN = 360            # minutes of history uploaded
TRANSIT = 60
HEAT, FREEZE = 12.0, -2.0

# name: (release, settle, how, events [(type, first, last or None for ongoing, expected owner, receiver cc'd)],
#        custody gap expected)
SCENARIOS = {
    "normal-during": (10, 70, "confirm", [("heat_excursion", 30, 65, "releaser", True)], False),
    "normal-after": (10, 70, "confirm", [("heat_excursion", 120, 165, "receiver", False)], False),
    "retrospective": (60, 120, "confirm", [("heat_excursion", 5, 50, "releaser", False)], False),
    "ongoing": (10, 70, "confirm", [("heat_excursion", SPAN - 40, None, "receiver", False)], False),
    "incomplete": (10, None, None, [("heat_excursion", 150, 195, "releaser", True)], True),
    "rejected": (10, 70, "reject", [("heat_excursion", 120, 165, "releaser", False)], True),
    "delayed": (10, 200, "confirm", [("heat_excursion", 130, 175, "releaser", True),
                                     ("freeze", 240, 260, "receiver", False)], True),
}


def _url(role: str) -> str:
    return get_settings().db_url(role).replace("postgresql+psycopg://", "postgresql://")


def _wait_gap(conn, handoff_id: str, timeout: float = 60) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if conn.execute("SELECT gap_event_id FROM custody_handoff WHERE id = %s", (handoff_id,)).fetchone()[0]:
            return True
        time.sleep(0.5)
    return False


async def _custody(cases: list[dict], t0: datetime) -> None:
    client = await incidents.connect_temporal()
    with psycopg.connect(_url("app"), autocommit=True) as conn:
        product = conn.execute("SELECT id::text FROM product ORDER BY id LIMIT 1").fetchone()[0]
        for c in cases:
            rel_at, settle_at, how, _events, _gap = SCENARIOS[c["scenario"]]
            c["consignment"] = custody.create(conn, c["releaser"], product, 100, c["facility"],
                                              f"E6 outreach {c['scenario']}", TRANSIT, c["device"], t0)
            h = custody.release(conn, c["releaser"], c["consignment"], c["receiver"], t0 + rel_at * MIN)
            c["handoff"], c["window_ends_at"] = h.id, h.window_ends_at.isoformat()
            settle = None if settle_at is None else t0 + settle_at * MIN
            if settle is None or settle > h.window_ends_at:
                # the window ended first: the handoff workflow raises the custody gap before anything else
                await incidents.watch_handoff(client, h.id, h.window_ends_at.isoformat())
                c["gap_raised_by_workflow"] = await asyncio.to_thread(_wait_gap, conn, h.id)
            if settle is not None:
                fn = custody.confirm if how == "confirm" else custody.reject
                args = (conn, c["receiver"], c["consignment"], settle) if how == "confirm" else \
                    (conn, c["receiver"], c["consignment"], "arrived warm (E6)", settle)
                fn(*args)
                if settle <= h.window_ends_at:
                    await incidents.watch_handoff(client, h.id, h.window_ends_at.isoformat())
                await incidents.settle_handoff(client, h.id)


def run(seeds: range, facilities: list[str], receivers: list[str]) -> dict:
    cases = []
    with psycopg.connect(_url("owner"), autocommit=True) as conn:
        k = 0
        for seed in seeds:
            for name in SCENARIOS:
                serial = f"E6-{seed}-{name}-{uuid.uuid4().hex[:6]}"
                dev = conn.execute("""INSERT INTO device (serial_no, type, equipment_type)
                                      VALUES (%s, 'mobile', 'vaccine_carrier') RETURNING id::text""",
                                   (serial,)).fetchone()[0]
                facility = facilities[k % len(facilities)]
                cases.append({"seed": seed, "scenario": name, "serial": serial, "device": dev, "facility": facility,
                              "releaser": user_id(f"oic.{facility}"), "receiver": receivers[k % len(receivers)]})
                k += 1
    time.sleep(3)   # the bridge reloads its device registry
    upload = datetime.now(UTC).replace(second=0, microsecond=0)
    t0 = upload - SPAN * MIN
    asyncio.run(_custody(cases, t0))

    client = connect(f"e6-{uuid.uuid4().hex[:8]}")
    for c in cases:
        rng = random.Random(f"{c['seed']}-{c['scenario']}")
        events = SCENARIOS[c["scenario"]][3]

        def temp(i, events=events):
            for typ, first, last, *_ in events:
                if first <= i < (last if last is not None else SPAN + 1):
                    return HEAT if typ == "heat_excursion" else FREEZE
            return None
        readings = list(SimDevice(c["serial"]).readings(t0, SPAN + 1, 60, rng, temp=temp))
        for i in range(0, len(readings), 200):
            client.publish(topic(c["serial"]), json.dumps({"device_id": c["serial"], "readings": readings[i:i + 200]}),
                           qos=1).wait_for_publish()
    client.loop_stop()
    client.disconnect()
    return {"t0": t0.isoformat(), "upload_at": upload.isoformat(), "cases": cases}


def check(state: dict, settle_s: int = 120) -> dict:
    time.sleep(settle_s)
    t0 = datetime.fromisoformat(state["t0"])
    rows, totals = [], {"incidents_expected": 0, "incidents_found": 0, "owner_correct": 0, "cc_correct": 0,
                        "duplicates": 0, "unexpected_incidents": 0, "gaps_expected": 0, "gaps_correct": 0}
    with psycopg.connect(_url("owner")) as conn:
        for c in state["cases"]:
            _rel, _settle, _how, events, gap_expected = SCENARIOS[c["scenario"]]
            found = conn.execute("""SELECT i.id::text, i.type, i.owner_id::text, i.cc_ids::text[], i.ownership, i.mode,
                                           min(e.event_start)
                                    FROM incident i JOIN integrity_event e ON e.incident_id = i.id
                                    WHERE i.device_id = %s AND i.type IN ('heat_excursion', 'freeze')
                                    GROUP BY i.id""", (c["device"],)).fetchall()
            used = set()
            for typ, first, _last, who, cc in events:
                start = t0 + first * MIN
                want = c["releaser"] if who == "releaser" else c["receiver"]
                match = [f for f in found if f[1] == typ and abs((f[6] - start).total_seconds()) <= 15 * 60]
                totals["incidents_expected"] += 1
                totals["incidents_found"] += bool(match)
                totals["duplicates"] += max(0, len(match) - 1)
                r = {"seed": c["seed"], "scenario": c["scenario"], "type": typ, "start_min": first,
                     "expected_owner": who, "expected_cc": bool(cc), "found": bool(match)}
                if match:
                    m = match[0]
                    used.add(m[0])
                    r.update(incident=m[0], owner_ok=m[2] == want, cc_ok=(c["receiver"] in m[3]) == cc, copied=len(m[3]),
                             ownership_rule=m[4], mode=m[5], start_offset_min=round((m[6] - start).total_seconds() / 60, 1))
                    totals["owner_correct"] += r["owner_ok"]
                    totals["cc_correct"] += r["cc_ok"]
                rows.append(r)
            totals["unexpected_incidents"] += sum(1 for f in found if f[0] not in used)
            gaps = conn.execute("""SELECT count(*) FROM integrity_event WHERE consignment_id = %s AND type = 'custody_gap'""",
                                (c["consignment"],)).fetchone()[0]
            totals["gaps_expected"] += 1
            totals["gaps_correct"] += (gaps > 0) == gap_expected
            c["custody_gaps"] = gaps
    n = totals["incidents_expected"]
    totals["proportion_correct"] = round(sum(1 for r in rows if r.get("owner_ok") and r.get("cc_ok")) / n, 4)
    totals["criteria"] = {"100% owners correct": totals["owner_correct"] == n and totals["cc_correct"] == n,
                          "no duplicates or unexpected incidents": totals["duplicates"] == 0
                          and totals["unexpected_incidents"] == 0,
                          "custody gaps where and only where expected": totals["gaps_correct"] == totals["gaps_expected"]}
    return {"t0": state["t0"], "upload_at": state["upload_at"], "totals": totals, "incidents": rows,
            "cases": state["cases"]}
