"""API service (Table 3.11): the interface used by the dashboard (I6) and, for now, by tests.

Iteration I4 provides the commands for incidents and custody; I5 adds dashboard notifications and the
administrator's control of phone numbers; I6 adds the dashboard's read views (api/views.py), product
administration and a WebSocket that tells dashboards when data changes. Every request carries a Keycloak access
token; the user is the token's subject, who must be an active user in the database. Each command runs in
one database transaction; afterwards the affected workflow is told (incident) or started (handoff).

Run: uv run uvicorn coldchain.api.app:app --port 8000
"""
import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Annotated, Literal

import jwt
import psycopg
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from psycopg.rows import dict_row
from pydantic import BaseModel, Field

from coldchain import admin, audit, custody
from coldchain.api import views
from coldchain.config import get_settings
from coldchain.errors import InvalidState, NotFound, NotPermitted
from coldchain.incidents import service as incidents
from coldchain.incidents import store
from coldchain.incidents.activities import db_url
from coldchain.incidents.ownership import custodian_at
from coldchain.notify import store as notifications

log = logging.getLogger("api")
CLIENTS = ("coldchain-dashboard", "coldchain-dev-cli")
CHANGE_DELAY_S = 1.0          # changes are gathered for this long, then sent as one message


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.temporal = await incidents.connect_temporal()
    app.state.sockets = set()
    listener = asyncio.create_task(_listen_for_changes(app))
    yield
    listener.cancel()


app = FastAPI(title="Cold chain integrity API", version="0.6.0", lifespan=lifespan)


@lru_cache
def _jwks() -> jwt.PyJWKClient:
    s = get_settings()
    return jwt.PyJWKClient(f"{s.keycloak_url}/realms/{s.keycloak_realm}/protocol/openid-connect/certs")


def current_user(authorization: Annotated[str | None, Header()] = None) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "missing bearer token")
    return _subject(authorization.removeprefix("Bearer "))


def _subject(token: str) -> str:
    s = get_settings()
    try:
        key = _jwks().get_signing_key_from_jwt(token).key
        claims = jwt.decode(token, key, algorithms=["RS256"], options={"verify_aud": False},
                            issuer=f"{s.keycloak_public_url}/realms/{s.keycloak_realm}")
    except jwt.PyJWTError as e:
        raise HTTPException(401, f"invalid token: {e}") from None
    if claims.get("azp") not in CLIENTS:
        raise HTTPException(401, "token issued to an unknown client")
    return claims["sub"]


User = Annotated[str, Depends(current_user)]


def _run(fn, *args):
    """Run a domain function in one transaction, mapping domain errors to HTTP status codes."""
    with psycopg.connect(db_url(), autocommit=True) as conn:
        try:
            with conn.transaction():
                return fn(conn, *args)
        except NotFound as e:
            raise HTTPException(404, str(e)) from None
        except NotPermitted as e:
            raise HTTPException(403, str(e)) from None
        except InvalidState as e:
            raise HTTPException(409, str(e)) from None


def now() -> datetime:
    return datetime.now(UTC)


def _incident_id(conn: psycopg.Connection, ref: str) -> str:
    """Incidents are referred to by id or by their short number (as in SMS)."""
    row = conn.execute("SELECT id::text FROM incident WHERE " + ("incident_no = %s" if ref.isdigit() else "id::text = %s"),
                       (int(ref) if ref.isdigit() else ref,)).fetchone()
    if row is None:
        raise NotFound(f"no incident {ref}")
    return row[0]


class Note(BaseModel):
    note: str = Field("", max_length=1000)


class Reason(BaseModel):
    reason: str = Field(min_length=1, max_length=1000)


async def _incident_command(request: Request, ref: str, fn, *args) -> dict:
    def go(conn):
        return fn(conn, _incident_id(conn, ref), *args)
    outcome: store.Outcome = await asyncio.to_thread(_run, go)
    if outcome.changed:
        await incidents.touch_incident(request.app.state.temporal, outcome.incident_id)
    return {"incident_id": outcome.incident_id, "changed": outcome.changed, "message": outcome.message}


@app.post("/incidents/{ref}/acknowledge")
async def acknowledge(request: Request, ref: str, user: User):
    return await _incident_command(request, ref, store.acknowledge, user)


@app.post("/incidents/{ref}/actions")
async def record_action(request: Request, ref: str, body: Note, user: User):
    return await _incident_command(request, ref, store.record_action, user, body.note)


@app.post("/incidents/{ref}/resolve")
async def resolve(request: Request, ref: str, body: Note, user: User):
    return await _incident_command(request, ref, store.resolve, user, body.note)


@app.post("/incidents/{ref}/close")
async def close(request: Request, ref: str, user: User, body: Note | None = None):
    return await _incident_command(request, ref, store.close, user, body.note if body else "")


@app.post("/incidents/{ref}/reopen")
async def reopen(request: Request, ref: str, body: Reason, user: User):
    return await _incident_command(request, ref, store.reopen, user, body.reason)


@app.get("/incidents")
async def list_incidents(user: User, state: Literal["active", "review", "closed", "all"] = "active",
                         severity: Literal["critical", "warning"] | None = None, facility: str | None = None,
                         limit: int = 200):
    """The incidents the user may see (Table 3.21): active ones by severity and age."""
    return await asyncio.to_thread(_run, lambda conn: views.incidents(
        conn, views.viewer(conn, user), state, severity, facility, min(limit, 500)))


@app.get("/incidents/{ref}")
async def get_incident(ref: str, user: User):
    """An incident with its timeline (audit trail and notifications) and the actions open to the user."""
    return await asyncio.to_thread(_run, lambda conn: views.incident(
        conn, views.viewer(conn, user), _incident_id(conn, ref)))


@app.get("/incidents/{ref}/readings")
async def incident_readings(ref: str, user: User, before_min: int = 60, after_min: int = 30):
    """The readings behind an incident, with the temperature limits that applied."""
    return await asyncio.to_thread(_run, lambda conn: views.incident_readings(
        conn, views.viewer(conn, user), _incident_id(conn, ref), min(before_min, 1440), min(after_min, 1440)))


# Dashboard views -------------------------------------------------------------------------------------

@app.get("/me")
async def me(user: User):
    return await asyncio.to_thread(_run, lambda conn: views.me(conn, views.viewer(conn, user)))


@app.get("/map")
async def map_units(user: User):
    """Facilities and their units by status, and mobile units on consignments with their tracks."""
    return await asyncio.to_thread(_run, lambda conn: views.map_units(conn, views.viewer(conn, user)))


@app.get("/performance")
async def performance(user: User, group: Literal["tier", "facility", "lga", "state"] = "facility",
                      days: Annotated[int, Query(gt=0, le=366)] = 30, mode: Literal["operational", "retrospective", "all"] = "operational"):
    """Mean time to acknowledge and to resolve (FR10.3) over the last `days` days."""
    until = now()
    return await asyncio.to_thread(_run, lambda conn: views.performance(
        conn, views.viewer(conn, user), until - timedelta(days=days), until, group, mode))


# Custody -------------------------------------------------------------------------------------------

class NewConsignment(BaseModel):
    product_id: str
    quantity: int = Field(gt=0)
    origin_facility_id: str
    destination: str
    expected_transit_min: int = Field(gt=0)
    device_serial: str | None = None


class Release(BaseModel):
    to_user: str                      # user id or username of the intended receiver
    expected_transit_min: int | None = Field(None, gt=0)


def _user_id(conn: psycopg.Connection, ref: str) -> str:
    row = conn.execute("SELECT id::text FROM app_user WHERE id::text = %s OR username = %s", (ref, ref)).fetchone()
    if row is None:
        raise NotFound(f"no user {ref}")
    return row[0]


@app.post("/consignments", status_code=201)
async def create_consignment(body: NewConsignment, user: User):
    def go(conn):
        device_id = None
        if body.device_serial:
            row = conn.execute("SELECT id::text FROM device WHERE serial_no = %s", (body.device_serial,)).fetchone()
            if row is None:
                raise NotFound(f"no logger {body.device_serial}")
            device_id = row[0]
        return custody.create(conn, user, body.product_id, body.quantity, body.origin_facility_id,
                              body.destination, body.expected_transit_min, device_id, now())
    return {"consignment_id": await asyncio.to_thread(_run, go)}


@app.post("/consignments/{cid}/release")
async def release(request: Request, cid: str, body: Release, user: User):
    h = await asyncio.to_thread(_run, lambda conn: custody.release(
        conn, user, cid, _user_id(conn, body.to_user), now(), body.expected_transit_min))
    await incidents.watch_handoff(request.app.state.temporal, h.id, h.window_ends_at.isoformat())
    return {"handoff_id": h.id, "window_ends_at": h.window_ends_at}


async def _settle(request: Request, fn, *args) -> dict:
    handoff_id = await asyncio.to_thread(_run, fn, *args)
    await incidents.settle_handoff(request.app.state.temporal, handoff_id)
    return {"handoff_id": handoff_id}


@app.post("/consignments/{cid}/confirm")
async def confirm(request: Request, cid: str, user: User):
    return await _settle(request, custody.confirm, user, cid, now())


@app.post("/consignments/{cid}/reject")
async def reject(request: Request, cid: str, body: Reason, user: User):
    return await _settle(request, custody.reject, user, cid, body.reason, now())


@app.post("/consignments/{cid}/cancel")
async def cancel(request: Request, cid: str, body: Reason, user: User):
    return await _settle(request, custody.cancel, user, cid, body.reason, now())


@app.post("/consignments/{cid}/complete")
async def complete(cid: str, user: User):
    await asyncio.to_thread(_run, custody.complete, user, cid, now())
    return {"consignment_id": cid, "state": "completed"}


@app.get("/consignments")
async def list_consignments(user: User, completed: bool = False):
    return await asyncio.to_thread(_run, lambda conn: views.consignments(conn, views.viewer(conn, user), completed))


@app.get("/consignments/{cid}")
async def get_consignment(cid: str, user: User):
    return await asyncio.to_thread(_run, lambda conn: views.consignment(conn, views.viewer(conn, user), cid))


@app.get("/lookups")
async def lookups(user: User):
    """Choices for the consignment forms: origin facilities, products, free loggers, possible receivers."""
    def go(conn):
        v = views.viewer(conn, user)
        return {"origins": views.origins(conn, v), "products": admin.products(conn),
                "loggers": views.loggers(conn), "receivers": views.receivers(conn, v)}
    return await asyncio.to_thread(_run, go)


@app.get("/consignments/{cid}/custody")
async def custody_at(cid: str, user: User, at: datetime | None = None):
    """Who held the consignment at time `at` (default: now), and its custody state."""
    def go(conn):
        return {"consignment_id": cid, "state": custody.state(conn, cid), "at": at or now(),
                "holder": custodian_at(conn, cid, at or now())}
    return await asyncio.to_thread(_run, go)


# Notifications and phone numbers -------------------------------------------------------------------------

@app.get("/notifications")
async def my_notifications(user: User, unread: bool = False, limit: int = 50):
    """The user's dashboard notifications, newest first (FR9.1)."""
    def go(conn):
        return conn.cursor(row_factory=dict_row).execute("""
            SELECT n.id, n.kind, n.incident_id, i.incident_no, n.consignment_id, n.body, n.created_at, n.read_at,
                   i.notification_failed
            FROM notification n LEFT JOIN incident i ON i.id = n.incident_id
            WHERE n.recipient_id = %s AND n.channel = 'dashboard' AND (NOT %s OR n.read_at IS NULL)
            ORDER BY n.created_at DESC LIMIT %s""", (user, unread, min(limit, 200))).fetchall()
    return await asyncio.to_thread(_run, go)


@app.post("/notifications/{nid}/read")
async def read_notification(nid: str, user: User):
    def go(conn):
        if not notifications.mark_read(conn, user, nid):
            raise NotFound(f"no notification {nid}")
    await asyncio.to_thread(_run, go)
    return {"id": nid, "read": True}


class Phone(BaseModel):
    phone_number: str = Field(pattern=r"^\+[1-9][0-9]{7,14}$")


@app.post("/users/{ref}/phone/deactivate")
async def deactivate_phone(ref: str, user: User):
    """A lost phone: every command from the number is rejected from now on (Section 3.4.6)."""
    await asyncio.to_thread(_run, admin.deactivate_phone, user, ref)
    return {"user": ref, "phone": "deactivated"}


@app.put("/users/{ref}/phone")
async def set_phone(ref: str, body: Phone, user: User):
    """Register a (new) number for a user; it is active at once."""
    await asyncio.to_thread(_run, admin.set_phone, user, ref, body.phone_number)
    return {"user": ref, "phone": "active"}


# Administration ------------------------------------------------------------------------------------

class ProductChange(BaseModel):
    lower_limit: float | None = Field(None, ge=-30, le=30)
    upper_limit: float | None = Field(None, ge=-30, le=30)
    freeze_limit: float | None = Field(None, ge=-30, le=30)
    heat_tolerance_min: int | None = Field(None, ge=0, le=10080)
    freeze_tolerance_min: int | None = Field(None, ge=0, le=10080)
    freeze_sensitive: bool | None = None


@app.get("/products")
async def products(user: User):
    return await asyncio.to_thread(_run, lambda conn: (views.viewer(conn, user), admin.products(conn))[1])


@app.patch("/products/{pid}")
async def update_product(pid: str, body: ProductChange, user: User):
    """Change a product's temperature limits or tolerances (national officers; recorded, FR11.2)."""
    return await asyncio.to_thread(_run, lambda conn: admin.update_product(conn, user, pid, body.model_dump()))


@app.get("/users")
async def list_users(user: User):
    return await asyncio.to_thread(_run, admin.users, user)


@app.get("/system/status")
async def system_status(user: User):
    return await asyncio.to_thread(_run, admin.system_status, user)


# Audit ---------------------------------------------------------------------------------------------

@app.get("/audit/verify")
async def verify_audit(user: User):
    def go(conn):
        role = conn.execute("SELECT role FROM app_user WHERE id = %s AND active", (user,)).fetchone()
        if role is None or role[0] not in ("admin", "national_officer"):
            raise NotPermitted("audit verification is for administrators and national officers")
    await asyncio.to_thread(_run, go)

    def check():
        with psycopg.connect(db_url()) as conn:
            return audit.verify(conn, get_settings().audit_anchor_dir)
    reports = await asyncio.to_thread(check)
    return [{"table": r.table, "records": r.records, "intact": r.ok, "breaks": r.breaks} for r in reports]


# Live updates --------------------------------------------------------------------------------------

@app.websocket("/ws")
async def changes(ws: WebSocket):
    """Tells a dashboard which kinds of data changed (incident, notification, consignment,
    custody_handoff, device_status), so it can fetch them again. The first message must be the access
    token (a browser cannot set headers on a WebSocket). Only kinds of data are sent, never the data."""
    await ws.accept()
    try:
        token = await asyncio.wait_for(ws.receive_text(), timeout=10)
        await asyncio.to_thread(_run, lambda conn: views.viewer(conn, _subject(token)))
    except (TimeoutError, HTTPException, WebSocketDisconnect):
        await ws.close(code=4401)
        return
    ws.app.state.sockets.add(ws)
    try:
        while True:
            await ws.receive_text()           # nothing is expected; this waits for the client to leave
    except WebSocketDisconnect:
        pass
    finally:
        ws.app.state.sockets.discard(ws)


async def _listen_for_changes(app: FastAPI) -> None:
    """Relay the database's change notices (migration 0007) to connected dashboards, gathered for
    CHANGE_DELAY_S so that a burst of changes is one message. Reconnects if the database goes away."""
    while True:
        try:
            async with await psycopg.AsyncConnection.connect(db_url(), autocommit=True) as conn:
                await conn.execute("LISTEN coldchain_changes")
                pending: set[str] = set()
                gen = conn.notifies()
                while True:
                    try:
                        n = await asyncio.wait_for(anext(gen), timeout=CHANGE_DELAY_S if pending else None)
                        pending.add(n.payload)
                    except TimeoutError:
                        message = {"changed": sorted(pending)}
                        pending.clear()
                        for ws in list(app.state.sockets):
                            with contextlib.suppress(Exception):
                                await ws.send_json(message)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("change listener failed; reconnecting")
            await asyncio.sleep(2)


@app.get("/health")
async def health():
    return {"status": "ok"}
