"""Incident service (Table 3.11): turns integrity events into incidents and runs their workflows.

One process runs four loops:
- the Temporal worker, which executes incident, handoff and audit workflows;
- a Kafka consumer of integrity-events, which creates or updates incidents and then starts or
  signals each incident's workflow (offsets are committed only after both);
- the outbox relay, which publishes notifications and custody-gap events written by any service;
- a sweep that makes sure every pending handoff has its confirmation-window timer, in case the service
  that recorded the release stopped before starting it.

Run: uv run python -m coldchain.incidents.service
"""
import asyncio
import json
import logging
from concurrent.futures import ThreadPoolExecutor

import psycopg
from temporalio.client import (
    Client,
    Schedule,
    ScheduleActionStartWorkflow,
    ScheduleAlreadyRunningError,
    ScheduleCalendarSpec,
    ScheduleRange,
    ScheduleSpec,
)
from temporalio.common import WorkflowIDConflictPolicy
from temporalio.service import RPCError
from temporalio.worker import Worker

from coldchain import kafka, outbox
from coldchain.config import get_settings
from coldchain.incidents import store
from coldchain.incidents.activities import Activities, db_url
from coldchain.incidents.workflows import AuditWorkflow, HandoffWorkflow, IncidentWorkflow

log = logging.getLogger("incidents.service")
HANDOFF_SWEEP_S = 60
RELAY_IDLE_S = 0.5


async def connect_temporal() -> Client:
    s = get_settings()
    return await Client.connect(s.temporal_address, namespace=s.temporal_namespace)


async def touch_incident(client: Client, incident_id: str) -> None:
    """Start the incident's workflow, or signal it if it is already running."""
    await client.start_workflow(IncidentWorkflow.run, incident_id, id=f"incident-{incident_id}",
                                task_queue=get_settings().temporal_task_queue, start_signal="changed")


async def watch_handoff(client: Client, handoff_id: str, window_ends_at: str) -> None:
    await client.start_workflow(HandoffWorkflow.run, args=[handoff_id, window_ends_at], id=f"handoff-{handoff_id}",
                                task_queue=get_settings().temporal_task_queue,
                                id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING)


async def settle_handoff(client: Client, handoff_id: str) -> None:
    try:
        await client.get_workflow_handle(f"handoff-{handoff_id}").signal(HandoffWorkflow.settled)
    except RPCError as e:   # already finished, or never started: expire() re-checks the state anyway
        log.info("handoff %s timer not signalled: %s", handoff_id, e)


async def ensure_audit_schedule(client: Client) -> None:
    """Verify and anchor the audit chains daily at 23:55 WAT (22:55 UTC)."""
    try:
        await client.create_schedule("audit-daily", Schedule(
            action=ScheduleActionStartWorkflow(AuditWorkflow.run, id="audit-daily",
                                               task_queue=get_settings().temporal_task_queue),
            spec=ScheduleSpec(calendars=[ScheduleCalendarSpec(hour=[ScheduleRange(22)],
                                                              minute=[ScheduleRange(55)])])))
    except ScheduleAlreadyRunningError:
        pass


def _blocking(fn, *args):
    return asyncio.get_running_loop().run_in_executor(None, fn, *args)


async def consume(client: Client) -> None:
    consumer = kafka.consumer("incidents", [kafka.INTEGRITY_EVENTS])
    conn = psycopg.connect(db_url(), autocommit=True)

    def handle(event_id: str) -> store.Outcome | None:
        with conn.transaction():
            return store.on_event(conn, event_id)

    while True:
        msgs = await _blocking(consumer.consume, 200, 0.5)
        if not msgs:
            continue
        touched = []
        for m in msgs:
            if m.error():
                log.error("Kafka error: %s", m.error())
                continue
            ev = json.loads(m.value())
            outcome = await _blocking(handle, ev["id"])
            if outcome:
                log.info("%s %s: %s", ev["type"], ev["id"], outcome.message)
                if outcome.changed and outcome.incident_id not in touched:
                    touched.append(outcome.incident_id)
        for incident_id in touched:
            await touch_incident(client, incident_id)
        await _blocking(lambda: consumer.commit(asynchronous=False))


async def relay() -> None:
    producer = kafka.producer()
    conn = None
    while True:
        try:
            if conn is None or conn.closed:
                conn = psycopg.connect(db_url(), autocommit=True)
            if await _blocking(outbox.relay, conn, producer) == 0:
                await asyncio.sleep(RELAY_IDLE_S)
        except (psycopg.Error, RuntimeError):
            log.exception("outbox relay failed; reconnecting and retrying")
            if conn is not None:
                conn.close()
            conn = None
            await asyncio.sleep(5)


async def sweep(client: Client) -> None:
    conn = psycopg.connect(db_url(), autocommit=True)
    open_incidents = (await _blocking(conn.execute, "SELECT id::text FROM incident WHERE state <> 'closed'")).fetchall()
    for (incident_id,) in open_incidents:     # after a restore or a lost start, every open incident has a timer
        await touch_incident(client, incident_id)
    log.info("workflows ensured for %d open incidents", len(open_incidents))
    while True:
        rows = (await _blocking(conn.execute, """
            SELECT id::text, window_ends_at FROM custody_handoff WHERE state = 'pending' AND gap_event_id IS NULL
        """)).fetchall()
        for handoff_id, ends in rows:
            await watch_handoff(client, handoff_id, ends.isoformat())
        await asyncio.sleep(HANDOFF_SWEEP_S)


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    kafka.ensure_topics()
    client = await connect_temporal()
    await ensure_audit_schedule(client)
    worker = Worker(client, task_queue=get_settings().temporal_task_queue,
                    workflows=[IncidentWorkflow, HandoffWorkflow, AuditWorkflow],
                    activities=[Activities().apply_due, Activities().expire_handoff, Activities().verify_and_anchor],
                    activity_executor=ThreadPoolExecutor(16))
    async with worker:
        await asyncio.gather(consume(client), relay(), sweep(client))


if __name__ == "__main__":
    asyncio.run(main())
