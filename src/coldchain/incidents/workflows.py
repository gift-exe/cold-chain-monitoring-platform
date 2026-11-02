"""Temporal workflows: durable timers for incident escalation, handoff confirmation windows and the
daily audit verification (Table 3.12: escalation timers survive failures, NFR6).

The database is the record of what has happened; a workflow only decides when to look again. Signals
("something changed") wake it early. If a signal is lost, the workflow still wakes at the next deadline
(or within an hour) and finds the change in the database.
"""
from datetime import datetime, timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from coldchain.incidents.activities import Activities

ACTIVITY = {"start_to_close_timeout": timedelta(seconds=60),
            "retry_policy": RetryPolicy(initial_interval=timedelta(seconds=1), maximum_interval=timedelta(seconds=30))}
MAX_WAIT = timedelta(hours=1)
ITERATIONS_PER_RUN = 200     # then continue as new, so the workflow history stays short


@workflow.defn
class IncidentWorkflow:
    def __init__(self) -> None:
        self._changed = False

    @workflow.signal
    def changed(self) -> None:
        self._changed = True

    @workflow.run
    async def run(self, incident_id: str) -> None:
        for _ in range(ITERATIONS_PER_RUN):
            self._changed = False
            due = await workflow.execute_activity_method(
                Activities.apply_due, args=[incident_id, workflow.now().isoformat()], **ACTIVITY)
            if due.finished:
                return
            wait = MAX_WAIT
            if due.next_at is not None:
                wait = min(wait, max(timedelta(0), datetime.fromisoformat(due.next_at) - workflow.now()))
            try:
                await workflow.wait_condition(lambda: self._changed, timeout=wait)
            except TimeoutError:
                pass
        workflow.continue_as_new(incident_id)


@workflow.defn
class HandoffWorkflow:
    """Waits for a handoff's confirmation window to end; if it is still unconfirmed, raises a custody gap
    (CR4)."""

    def __init__(self) -> None:
        self._settled = False

    @workflow.signal
    def settled(self) -> None:
        self._settled = True

    @workflow.run
    async def run(self, handoff_id: str, window_ends_at: str) -> str | None:
        wait = max(timedelta(0), datetime.fromisoformat(window_ends_at) - workflow.now())
        try:
            await workflow.wait_condition(lambda: self._settled, timeout=wait)
            return None
        except TimeoutError:
            return await workflow.execute_activity_method(
                Activities.expire_handoff, args=[handoff_id, workflow.now().isoformat()], **ACTIVITY)


@workflow.defn
class AuditWorkflow:
    """Verify the audit chains and anchor their latest hashes outside the database (FR7.3)."""

    @workflow.run
    async def run(self) -> list[dict]:
        return await workflow.execute_activity_method(
            Activities.verify_and_anchor, start_to_close_timeout=timedelta(minutes=30),
            retry_policy=RetryPolicy(maximum_attempts=3))   # a broken chain is reported, not retried for ever
