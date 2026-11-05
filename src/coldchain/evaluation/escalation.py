"""E5 acknowledgement and escalation (Table 3.10): scripted responders against the prototype's own incident
workflows, run on Temporal's time-skipping test server so that limits of hours or days are applied exactly
without waiting for them.

Each replication creates one incident per scenario and starts its workflow (the real IncidentWorkflow and
activities, against a database). Scripted responders acknowledge and resolve at set times (on time, late or
never), each time shifted by up to three minutes by the replication's seed. The expected timeline of every
incident is written out here from Table 3.15, independently of the policy code:

- operational critical: owner notified at once; unacknowledged -> LGA at 15 min, state at 60 min, national
  at 240 min (cumulative from creation); after acknowledgement, unresolved -> the tier above the highest
  already notified at 2 h, and the following tier at 4 h; acknowledgement after escalation is accepted and
  the escalation already made stands;
- retrospective critical: owner (digest) at once; unacknowledged -> LGA at 24 h, state at 72 h; after
  acknowledgement, unresolved at the 7-day review limit -> state;
- warning: owner (dashboard) at once; unacknowledged -> LGA at 24 h; never overdue;
- an unresolved critical incident at its top tier is marked overdue: when a resolution limit passes there,
  or 24 hours after it reached that tier (the interpretation recorded in I4); an overdue operational
  incident then gets a reminder every 24 hours until it is resolved, a retrospective one none.

Metric: the proportion of incidents whose every step has the expected tier and time, applied within one
minute of it, whose late acknowledgements were accepted without undoing escalation, and whose overdue mark
and daily reminders come when expected. Criterion: 100%.
"""
import asyncio
import random
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import psycopg
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from coldchain.config import get_settings
from coldchain.incidents import store
from coldchain.incidents.activities import Activities
from coldchain.incidents.workflows import HandoffWorkflow, IncidentWorkflow

QUEUE = "e5"
MIN = timedelta(minutes=1)
OUT = Path(__file__).resolve().parents[3] / "runs" / "eval" / "e5"
OWNER = "0b1e5a10-0000-4000-8000-000000000004"        # test.facility, OIC of the facility below (seed data)
FACILITY = "8a3c1f52-2b7e-4c1d-9d6a-0f4e5b6c7d01"
JITTER = 3                                           # minutes either way

H = 60
D = 24 * H
TOP = {"operational": "national", "retrospective": "state", "warning": "lga"}
ACK_LIMITS = {"operational": [("lga", 15), ("state", 60), ("national", 240)],
              "retrospective": [("lga", D), ("state", 3 * D)],
              "warning": [("lga", D)]}
RANK = ["owner", "lga", "state", "national"]


@dataclass(frozen=True)
class Scenario:
    kind: str                 # operational | retrospective | warning
    ack: int | None           # minutes after creation, or never
    resolve: int | None       # minutes after creation, or never
    horizon: int              # minutes the replication runs

    @property
    def name(self) -> str:
        return f"{self.kind}/ack={self.ack}/resolve={self.resolve}"


def scenarios() -> list[Scenario]:
    out = []
    for ack in (10, 30, 100, 300):                     # on time; after LGA; after state; after national
        for res in (60, 180, None):                    # in time; after the first resolution limit; never
            out.append(Scenario("operational", ack, None if res is None else ack + res, 10 * D))
    out.append(Scenario("operational", None, None, 10 * D))
    for ack in (10 * H, 2000):                         # within 24 h; after the LGA escalation
        for res in (3 * D, None):
            out.append(Scenario("retrospective", ack, None if res is None else ack + res, 10 * D))
    out.append(Scenario("retrospective", None, None, 10 * D))
    out += [Scenario("warning", 10 * H, None, 10 * D), Scenario("warning", None, None, 10 * D)]
    return out


def expected(s: Scenario) -> tuple[list[tuple[str, str, int]], int | None]:
    """Steps (action, tier, minute) from Table 3.15, and when the incident becomes overdue (None: never)."""
    steps, tier, top_at, overdue = [("notified", "owner", 0)], "owner", None, None
    for t, at in ACK_LIMITS[s.kind]:
        if (s.ack is None or at < s.ack) and at <= s.horizon:
            steps.append(("escalated", t, at))
            tier = t
            top_at = at if t == TOP[s.kind] else top_at
    if s.kind == "warning":
        return steps, None
    if s.ack is not None:
        limits = [2 * H, 4 * H] if s.kind == "operational" else [7 * D]
        for delay in limits:
            at = s.ack + delay
            if (s.resolve is not None and s.resolve <= at) or at > s.horizon:
                break
            if s.kind == "retrospective":
                nxt = "state" if RANK.index(tier) < RANK.index("state") else None
            else:
                nxt = RANK[RANK.index(tier) + 1] if tier != "national" else None
            if nxt is None:
                overdue = at                           # resolution limit passed at the top tier
                break
            steps.append(("escalated", nxt, at))
            tier = nxt
            top_at = at if nxt == TOP[s.kind] else top_at
    unresolved_at = s.resolve if s.resolve is not None else s.horizon + 1
    if overdue is None and top_at is not None and top_at + D <= min(s.horizon, unresolved_at):
        overdue = top_at + D
    return steps, overdue


def _url() -> str:
    return get_settings().db_url("app").replace("postgresql+psycopg://", "postgresql://")


def _incident(conn, s: Scenario) -> str:
    dev = conn.execute("""INSERT INTO device (serial_no, facility_id, type, equipment_type)
                          VALUES (%s, %s, 'fixed', 'ice_lined_refrigerator') RETURNING id::text""",
                       (f"E5-{uuid.uuid4().hex[:10]}", FACILITY)).fetchone()[0]
    if s.kind == "operational":
        ev = ("heat_excursion", "critical", "now() - interval '35 minutes'", "NULL")
    elif s.kind == "retrospective":
        ev = ("heat_excursion", "critical", "now() - interval '5 hours'", "now() - interval '4 hours'")
    else:
        ev = ("power_loss", "warning", "now() - interval '15 minutes'", "NULL")
    eid = conn.execute(f"""INSERT INTO integrity_event (device_id, type, severity, event_start, event_end, detected_at)
                           VALUES (%s, %s, %s, {ev[2]}, {ev[3]}, now()) RETURNING id::text""",
                       (dev, ev[0], ev[1])).fetchone()[0]
    with conn.transaction():
        inc = store.on_event(conn, eid).incident_id
    mode = conn.execute("SELECT mode, severity FROM incident WHERE id = %s", (inc,)).fetchone()
    assert (s.kind == "warning") == (mode[1] == "warning") and (s.kind != "retrospective") == (mode[0] == "operational")
    return inc


async def _sleep_until(env, t: datetime) -> None:
    """Advance the test server's clock to t, in steps (one very long sleep times out its RPC)."""
    while (left := t - await env.get_current_time()) > timedelta(0):
        await env.sleep(min(left, timedelta(hours=6)))


async def _replication(conn, seed: int, plan: list[Scenario]) -> list[dict]:
    rng = random.Random(seed)
    jit = [(rng.randint(-JITTER, JITTER), rng.randint(-JITTER, JITTER)) for _ in plan]
    plan = [Scenario(s.kind, None if s.ack is None else s.ack + a, None if s.resolve is None else s.resolve + a + r,
                     s.horizon) for s, (a, r) in zip(plan, jit, strict=True)]
    async with await WorkflowEnvironment.start_time_skipping() as env:
        acts = Activities()
        async with Worker(env.client, task_queue=QUEUE, workflows=[IncidentWorkflow, HandoffWorkflow],
                          activities=[acts.apply_due, acts.expire_handoff], activity_executor=ThreadPoolExecutor(8)):
            t0 = await env.get_current_time()
            incs = []
            for s in plan:
                inc = _incident(conn, s)
                conn.execute("UPDATE incident SET ack_due_from = %s WHERE id = %s", (t0, inc))
                handle = await env.client.start_workflow(IncidentWorkflow.run, inc, id=f"incident-{inc}",
                                                         task_queue=QUEUE)
                incs.append((s, inc, handle))
            # the responders' actions, in time order
            actions = sorted([(s.ack, "ack", i) for i, (s, _, _) in enumerate(incs) if s.ack is not None]
                             + [(s.resolve, "resolve", i) for i, (s, _, _) in enumerate(incs) if s.resolve is not None])
            late = {}
            for minute, what, i in actions:
                s, inc, handle = incs[i]
                if minute > s.horizon:
                    continue
                await _sleep_until(env, t0 + minute * MIN)
                now = await env.get_current_time()
                with conn.transaction():
                    if what == "ack":
                        tier = conn.execute("SELECT current_tier FROM incident WHERE id = %s", (inc,)).fetchone()[0]
                        store.acknowledge(conn, inc, OWNER)
                        conn.execute("UPDATE incident SET acknowledged_at = %s, resolve_due_from = %s WHERE id = %s",
                                     (now, now, inc))
                        after = conn.execute("SELECT current_tier FROM incident WHERE id = %s", (inc,)).fetchone()[0]
                        late[i] = {"tier_before": tier, "tier_after": after}
                    else:
                        store.resolve(conn, inc, OWNER, "e5")
                await handle.signal(IncidentWorkflow.changed)
            end = t0 + max(s.horizon for s in plan) * MIN
            await _sleep_until(env, end)
    return [_score(conn, s, inc, t0, late.get(i)) for i, (s, inc, _) in enumerate(incs)]


def _score(conn, s: Scenario, inc: str, t0: datetime, ack: dict | None) -> dict:
    rows = conn.execute("""SELECT action, details->>'tier', details->>'due_at', details->>'applied_at'
                           FROM incident_action WHERE incident_id = %s AND details ? 'due_at' ORDER BY id""",
                        (inc,)).fetchall()
    got = [(a, tier, (datetime.fromisoformat(d) - t0) / MIN, (datetime.fromisoformat(p) - t0) / MIN)
           for a, tier, d, p in rows]
    steps = [g for g in got if g[0] in ("notified", "escalated")]
    want, overdue_at = expected(s)
    timeline_ok = (len(steps) == len(want) and all(
        a == wa and t == wt and abs(due - wm) < 1 and abs(applied - due) < 1
        for (a, t, due, applied), (wa, wt, wm) in zip(steps, want, strict=False)))
    # overdue when expected; then, for operational incidents only, a reminder every day until resolved
    marked = [g for g in got if g[0] == "overdue"]
    overdue_ok = (not marked) if overdue_at is None else (len(marked) == 1 and abs(marked[0][2] - overdue_at) < 1)
    end = min(s.horizon, s.resolve if s.resolve is not None else s.horizon)
    want_reminders = [] if overdue_at is None or s.kind != "operational" else \
        [overdue_at + k * D for k in range(1, 100) if overdue_at + k * D <= end]
    reminders = [g[2] for g in got if g[0] == "reminder"]
    reminders_ok = len(reminders) == len(want_reminders) and all(
        abs(a - b) < 1 for a, b in zip(reminders, want_reminders, strict=False))
    flag = conn.execute("SELECT overdue FROM incident WHERE id = %s", (inc,)).fetchone()[0]
    late_ok = ack is None or ack["tier_after"] == ack["tier_before"]     # escalation already made stands
    return {"scenario": s.name, "incident": inc, "expected": want, "expected_overdue_at": overdue_at,
            "expected_reminders": want_reminders, "got": [g[:3] for g in got], "overdue_flag": flag,
            "late_ack": ack, "checks": {"timeline": timeline_ok, "overdue": overdue_ok and flag == (overdue_at is not None),
                                        "reminders": reminders_ok, "late_acknowledgement": late_ok},
            "correct": timeline_ok and overdue_ok and flag == (overdue_at is not None) and reminders_ok and late_ok,
            "max_lateness_min": max((applied - due for _, _, due, applied in got), default=0)}


def run(seeds: range) -> dict:
    results = []
    with psycopg.connect(_url(), autocommit=True) as conn:
        for seed in seeds:
            results += [{**r, "seed": seed} for r in asyncio.run(_replication(conn, seed, scenarios()))]
            print(f"seed {seed}: {sum(r['correct'] for r in results)}/{len(results)} correct so far", flush=True)
    correct = sum(r["correct"] for r in results)
    return {"incidents": len(results), "correct": correct, "proportion": round(correct / len(results), 4),
            "criterion_100pct": correct == len(results),
            "max_lateness_min": round(max(r["max_lateness_min"] for r in results), 3),
            "wrong": [r for r in results if not r["correct"]], "results": results}
