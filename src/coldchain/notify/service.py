"""Notification service (Table 3.11): sends SMS and dashboard notifications, receives delivery reports and
parses SMS replies.

One process runs an HTTP server for the gateway's callbacks and three loops:
- a Kafka consumer of notification requests, which creates the notifications (offsets committed after);
- the dispatcher, which sends due SMS through the gateway (woken as soon as new ones are created);
- a sweep, every few seconds, for SMS with no final status after 10 minutes and for due RETRO digests.
When a reply or a failed delivery changes an incident or handoff, its workflow is told.

The callbacks are accepted only at URLs containing the shared secret (SMS_CALLBACK_TOKEN); anything else
gets 404. In deployment they are served over HTTPS (Section 3.4.6).

Run: uv run uvicorn coldchain.notify.service:app --port 8001
"""
import asyncio
import hmac
import json
import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Annotated

import httpx
import psycopg
from confluent_kafka import TopicPartition
from fastapi import FastAPI, Form, HTTPException

from coldchain import kafka
from coldchain.config import get_settings
from coldchain.incidents import service as incidents
from coldchain.incidents.activities import db_url
from coldchain.notify import commands, store

log = logging.getLogger("notify.service")
SWEEP_S = 5
DISPATCH_IDLE_S = 1


def now() -> datetime:
    return datetime.now(UTC)


class Loop:
    """A background loop with its own database connection, reconnecting after database errors."""

    def __init__(self, name: str):
        self.name, self.conn = name, None

    def connection(self) -> psycopg.Connection:
        if self.conn is None or self.conn.closed:
            self.conn = psycopg.connect(db_url(), autocommit=True)
        return self.conn

    async def run(self, step) -> None:
        while True:
            try:
                await step(self)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("%s failed; retrying", self.name)
                if self.conn is not None:
                    self.conn.close()
                self.conn = None
                await asyncio.sleep(2)


async def tell(app: FastAPI, incident_ids=(), handoff_ids=()) -> None:
    for incident_id in dict.fromkeys(incident_ids):
        await incidents.touch_incident(app.state.temporal, incident_id)
    for handoff_id in dict.fromkeys(handoff_ids):
        await incidents.settle_handoff(app.state.temporal, handoff_id)


def _in_transaction(fn, *args):
    with psycopg.connect(db_url(), autocommit=True) as conn, conn.transaction():
        return fn(conn, *args)


async def expand_batch(consumer, loop: Loop, msgs: list) -> list[str]:
    """Store the notifications requested by a batch of messages; return the incidents touched. If anything
    fails, rewind the consumer to the start of the batch before raising, so that the retry reads the batch
    again. Its position has already moved past the batch, so without this the next poll would skip it and the
    next commit lose it for good (E11 found four escalation notices lost this way after a database restart).
    Expanding a request again is harmless: notifications are unique per request and recipient."""
    touched: list[str] = []
    try:
        for m in msgs:
            if m.error():
                log.error("Kafka error: %s", m.error())
                continue
            req = json.loads(m.value())

            def expand(req=req):
                with loop.connection().transaction():
                    return store.expand(loop.conn, req, now())
            touched += await asyncio.to_thread(expand)
    except Exception:
        first: dict[tuple[str, int], int] = {}
        for m in msgs:
            if not m.error():
                first.setdefault((m.topic(), m.partition()), m.offset())
        for (topic, partition), offset in first.items():
            await asyncio.to_thread(consumer.seek, TopicPartition(topic, partition, offset))
        raise
    return touched


async def consume(app: FastAPI) -> None:
    consumer = kafka.consumer("notifications", [kafka.NOTIFICATIONS])

    async def step(loop: Loop) -> None:
        msgs = await asyncio.to_thread(consumer.consume, 200, 0.5)
        touched = await expand_batch(consumer, loop, msgs)
        if msgs:
            app.state.wake.set()
            await tell(app, touched)
            await asyncio.to_thread(lambda: consumer.commit(asynchronous=False))

    await Loop("consumer").run(step)


async def dispatch(app: FastAPI) -> None:
    client = httpx.Client(timeout=10)

    async def step(loop: Loop) -> None:
        attempted, touched = await asyncio.to_thread(lambda: store.dispatch(loop.connection(), now(), client))
        await tell(app, touched)
        if attempted == 0:
            app.state.wake.clear()
            try:
                await asyncio.wait_for(app.state.wake.wait(), DISPATCH_IDLE_S)
            except TimeoutError:
                pass

    await Loop("dispatcher").run(step)


async def sweep(app: FastAPI) -> None:
    async def step(loop: Loop) -> None:
        def go():
            with loop.connection().transaction():
                return store.sweep(loop.conn, now())
        await tell(app, await asyncio.to_thread(go))
        app.state.wake.set()
        await asyncio.sleep(SWEEP_S)

    await Loop("sweep").run(step)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    kafka.ensure_topics()
    app.state.temporal = await incidents.connect_temporal()
    app.state.wake = asyncio.Event()
    tasks = [asyncio.create_task(f(app)) for f in (consume, dispatch, sweep)]
    yield
    for t in tasks:
        t.cancel()


app = FastAPI(title="Notification service", version="0.5.0", lifespan=lifespan)


def _check_token(token: str) -> None:
    if not hmac.compare_digest(token.encode(), get_settings().sms_callback_token.encode()):
        log.warning("callback with a wrong token rejected")
        raise HTTPException(404)


@app.post("/sms/{token}/inbound")
async def inbound(token: str, id: Annotated[str, Form()], text: Annotated[str, Form()],
                  sender: Annotated[str, Form(alias="from")], to: Annotated[str | None, Form()] = None,
                  linkId: Annotated[str | None, Form()] = None):
    """An incoming SMS (Africa's Talking incoming-message callback)."""
    _check_token(token)
    result = await asyncio.to_thread(_in_transaction, commands.handle, id, sender, text, now(), to, linkId)
    log.info("SMS from %s: %s %s (%s)", sender, result.command, result.outcome, result.reason)
    if result.changed:
        await tell(app, [result.incident_id] if result.incident_id and not result.handoff_id else [],
                   [result.handoff_id] if result.handoff_id else [])
    app.state.wake.set()   # send the reply now
    return {"outcome": result.outcome}


@app.post("/sms/{token}/delivery")
async def delivery(token: str, id: Annotated[str, Form()], status: Annotated[str, Form()],
                   failureReason: Annotated[str | None, Form()] = None):
    """A delivery report (Africa's Talking delivery-report callback)."""
    _check_token(token)
    touched = await asyncio.to_thread(_in_transaction, store.on_delivery_report, id, status, failureReason, now())
    await tell(app, touched)
    app.state.wake.set()   # a retry or a failure notice may now be due
    return {"status": "ok"}


@app.get("/health")
async def health():
    return {"status": "ok"}
