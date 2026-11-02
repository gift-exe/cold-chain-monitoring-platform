"""I4 acceptance (Table 3.2, test E5): escalation and handoff timers produce the expected tier at the
expected time, run by Temporal workflows against the real database.

Temporal's time-skipping test server runs the workflows, so hours of escalation take a second; each
activity receives the workflow's time, which is what the escalation decisions use. Incidents and
consignments are committed (the activities use their own connections) and closed at the end.
"""
import asyncio
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import psycopg
import pytest
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from coldchain import custody
from coldchain.incidents import store
from coldchain.incidents.activities import Activities
from coldchain.incidents.workflows import HandoffWorkflow, IncidentWorkflow
from tests.conftest import HEALTH_FACILITY, PRODUCT, USERS, _pg_url, close_test_incidents

pytestmark = pytest.mark.integration
QUEUE = "test-incidents"
MIN = timedelta(minutes=1)


@pytest.fixture
def conn():
    with psycopg.connect(_pg_url("app"), autocommit=True) as c:
        yield c


@pytest.fixture
def cleanup():
    devices, consignments = [], []
    yield devices, consignments
    close_test_incidents(devices, consignments)


def run(scenario):
    """Run a scenario against a time-skipping Temporal server with the incident workflows and activities."""
    async def main():
        async with await WorkflowEnvironment.start_time_skipping() as env:
            acts = Activities()
            async with Worker(env.client, task_queue=QUEUE, workflows=[IncidentWorkflow, HandoffWorkflow],
                              activities=[acts.apply_due, acts.expire_handoff],
                              activity_executor=ThreadPoolExecutor(4)):
                await scenario(env)
    asyncio.run(main())


def start_clocks_at(conn, incident_id: str, t: datetime) -> None:
    """Measure the incident's time limits from the test server's clock (which need not equal now())."""
    conn.execute("UPDATE incident SET ack_due_from = %s WHERE id = %s", (t, incident_id))


def new_incident(conn, cleanup) -> str:
    dev = conn.execute("""INSERT INTO device (serial_no, facility_id, type, equipment_type)
                          VALUES (%s, %s, 'fixed', 'test_fridge') RETURNING id::text""",
                       (f"WF-{uuid.uuid4().hex[:10]}", HEALTH_FACILITY)).fetchone()[0]
    cleanup[0].append(dev)
    ev = conn.execute("""INSERT INTO integrity_event (device_id, type, severity, event_start, detected_at)
                         VALUES (%s, 'heat_excursion', 'critical', now() - interval '35 minutes', now())
                         RETURNING id::text""", (dev,)).fetchone()[0]
    with conn.transaction():
        return store.on_event(conn, ev).incident_id


def steps(conn, incident_id) -> list[tuple]:
    """(action, tier, minutes after creation it was due, minutes after creation it was applied)"""
    t0 = conn.execute("SELECT ack_due_from FROM incident WHERE id = %s", (incident_id,)).fetchone()[0]
    rows = conn.execute("""SELECT action, details->>'tier', details->>'due_at', details->>'applied_at'
                           FROM incident_action WHERE incident_id = %s AND details ? 'due_at' ORDER BY id""",
                        (incident_id,)).fetchall()
    return [(a, tier, (datetime.fromisoformat(d) - t0) / MIN, (datetime.fromisoformat(p) - t0) / MIN)
            for a, tier, d, p in rows]


def test_unacknowledged_critical_incident_escalates_at_15_60_and_240_minutes(conn, cleanup):
    inc = new_incident(conn, cleanup)

    async def scenario(env):
        start_clocks_at(conn, inc, await env.get_current_time())
        await env.client.start_workflow(IncidentWorkflow.run, inc, id=f"incident-{inc}", task_queue=QUEUE)
        await env.sleep(timedelta(hours=5))

    run(scenario)
    got = steps(conn, inc)
    assert [(a, t, due) for a, t, due, _ in got] == [
        ("notified", "owner", 0), ("escalated", "lga", 15), ("escalated", "state", 60),
        ("escalated", "national", 240)]
    assert all(abs(applied - due) < 1 for _, _, due, applied in got)   # E5: within one minute


def test_acknowledgement_stops_escalation_and_starts_resolution_limit(conn, cleanup):
    inc = new_incident(conn, cleanup)

    async def scenario(env):
        start_clocks_at(conn, inc, await env.get_current_time())
        handle = await env.client.start_workflow(IncidentWorkflow.run, inc, id=f"incident-{inc}", task_queue=QUEUE)
        await env.sleep(20 * MIN)                     # escalated to the LGA at 15 minutes
        with conn.transaction():                      # acknowledged at about 20 minutes (late)
            store.acknowledge(conn, inc, USERS["oic"])
            conn.execute("UPDATE incident SET resolve_due_from = %s WHERE id = %s",
                         (await env.get_current_time(), inc))
        await handle.signal(IncidentWorkflow.changed)
        await env.sleep(3 * 60 * MIN)                 # resolution limit: 2 hours later, to the state tier
        with conn.transaction():
            store.resolve(conn, inc, USERS["oic"], "fridge repaired")
            store.close(conn, inc, USERS["lga"], "checked")
        await handle.signal(IncidentWorkflow.changed)
        await handle.result()                         # the workflow finishes when the incident is closed

    run(scenario)
    got = [(a, t, round(due)) for a, t, due, _ in steps(conn, inc)]
    assert got == [("notified", "owner", 0), ("escalated", "lga", 15), ("escalated", "state", 140)]


def test_unconfirmed_handoff_raises_custody_gap_when_window_ends(conn, cleanup):
    with conn.transaction():
        cid = custody.create(conn, USERS["oic"], PRODUCT, 50, HEALTH_FACILITY, "Outreach", 10, None,
                             conn.execute("SELECT now()").fetchone()[0])
    cleanup[1].append(cid)
    result = {}

    async def scenario(env):
        t = await env.get_current_time()
        with conn.transaction():
            h = custody.release(conn, USERS["oic"], cid, USERS["outreach"], t)     # window: 10 + 30 minutes
        handle = await env.client.start_workflow(HandoffWorkflow.run, args=[h.id, h.window_ends_at.isoformat()],
                                                 id=f"handoff-{h.id}", task_queue=QUEUE)
        await env.sleep(39 * MIN)
        result["before"] = conn.execute("SELECT gap_event_id FROM custody_handoff WHERE id = %s",
                                        (h.id,)).fetchone()[0]
        result["gap"] = await handle.result()
        result["window"] = h.window_ends_at

    run(scenario)
    assert result["before"] is None and result["gap"] is not None
    start, reason = conn.execute("SELECT event_start, details->>'reason' FROM integrity_event WHERE id = %s",
                                 (result["gap"],)).fetchone()
    assert start == result["window"] and reason == "handoff not confirmed within its window"
    with conn.transaction():
        custody.cancel(conn, USERS["oic"], cid, "test finished", result["window"] + MIN)


def test_confirmed_handoff_raises_no_gap(conn, cleanup):
    with conn.transaction():
        cid = custody.create(conn, USERS["oic"], PRODUCT, 50, HEALTH_FACILITY, "Outreach", 10, None,
                             conn.execute("SELECT now()").fetchone()[0])
    cleanup[1].append(cid)
    result = {}

    async def scenario(env):
        t = await env.get_current_time()
        with conn.transaction():
            h = custody.release(conn, USERS["oic"], cid, USERS["outreach"], t)
        handle = await env.client.start_workflow(HandoffWorkflow.run, args=[h.id, h.window_ends_at.isoformat()],
                                                 id=f"handoff-{h.id}", task_queue=QUEUE)
        await env.sleep(25 * MIN)
        with conn.transaction():
            custody.confirm(conn, USERS["outreach"], cid, await env.get_current_time())
        await handle.signal(HandoffWorkflow.settled)
        result["gap"] = await handle.result()

    run(scenario)
    assert result["gap"] is None
    assert conn.execute("SELECT count(*) FROM integrity_event WHERE consignment_id = %s", (cid,)).fetchone()[0] == 0
