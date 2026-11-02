"""API service (Table 3.11): the interface used by the dashboard (I6) and, for now, by tests.

Iteration I4 provides the commands for incidents and custody. Every request carries a Keycloak access
token; the user is the token's subject, who must be an active user in the database. Each command runs in
one database transaction; afterwards the affected workflow is told (incident) or started (handoff).

Run: uv run uvicorn coldchain.api.app:app --port 8000
"""
import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from functools import lru_cache
from typing import Annotated

import jwt
import psycopg
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from psycopg.rows import dict_row
from pydantic import BaseModel, Field

from coldchain import audit, custody
from coldchain.config import get_settings
from coldchain.errors import InvalidState, NotFound, NotPermitted
from coldchain.incidents import service as incidents
from coldchain.incidents import store
from coldchain.incidents.activities import db_url
from coldchain.incidents.ownership import custodian_at

CLIENTS = ("coldchain-dashboard", "coldchain-dev-cli")


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.temporal = await incidents.connect_temporal()
    yield


app = FastAPI(title="Cold chain integrity API", version="0.4.0", lifespan=lifespan)


@lru_cache
def _jwks() -> jwt.PyJWKClient:
    s = get_settings()
    return jwt.PyJWKClient(f"{s.keycloak_url}/realms/{s.keycloak_realm}/protocol/openid-connect/certs")


def current_user(authorization: Annotated[str | None, Header()] = None) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "missing bearer token")
    token = authorization.removeprefix("Bearer ")
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


@app.get("/incidents/{ref}")
async def get_incident(ref: str, user: User):
    def go(conn):
        incident_id = _incident_id(conn, ref)
        if not store.can_view(conn, incident_id, user):
            raise NotPermitted("not permitted to view this incident")
        cur = conn.cursor(row_factory=dict_row)
        inc = cur.execute("SELECT * FROM incident WHERE id = %s", (incident_id,)).fetchone()
        inc.pop("response_code_hash", None)
        inc["actions"] = cur.execute("""
            SELECT id, action, actor_id, channel, occurred_at, details FROM incident_action
            WHERE incident_id = %s ORDER BY id""", (inc["id"],)).fetchall()
        return inc
    return await asyncio.to_thread(_run, go)


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


@app.get("/consignments/{cid}/custody")
async def custody_at(cid: str, user: User, at: datetime | None = None):
    """Who held the consignment at time `at` (default: now), and its custody state."""
    def go(conn):
        return {"consignment_id": cid, "state": custody.state(conn, cid), "at": at or now(),
                "holder": custodian_at(conn, cid, at or now())}
    return await asyncio.to_thread(_run, go)


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


@app.get("/health")
async def health():
    return {"status": "ok"}
