"""Custody of consignments (Section 3.4.3, Table 3.16).

Custody is recorded in three tables. custody_interval says who held a consignment from one time to
another (at most one holder at any instant, enforced by an exclusion constraint). custody_handoff is a
transfer in progress. custody_event is the append-only, hash-chained log of everything that happened
(CR9). The releaser remains the custodian until the receiver confirms (CR2), and the transfer takes
effect when the confirmation is received (CR3).

As in incidents.store, the functions here run inside the caller's transaction. Custody-gap events are
written to the outbox in the same transaction. A handoff's confirmation window is watched by a durable
timer (incidents.workflows.HandoffWorkflow), which calls expire() when the window ends.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from coldchain import kafka, outbox
from coldchain.errors import InvalidState, NotFound, NotPermitted
from coldchain.incidents.store import raise_custody_gap

CONFIRMATION_MARGIN = timedelta(minutes=30)   # CR4: expected transit time plus 30 minutes
HOLDER_ROLES = ("facility_oic", "outreach_lead", "driver", "lga_officer", "state_officer")


@dataclass(frozen=True)
class Handoff:
    id: str
    consignment_id: str
    from_user: str
    to_user: str
    released_at: datetime
    window_ends_at: datetime


def _log(conn: psycopg.Connection, consignment_id: str, type_: str, actor: str | None, from_user: str | None,
         to_user: str | None, handoff_id: str | None = None, details: dict | None = None) -> None:
    conn.execute("""
        INSERT INTO custody_event (consignment_id, type, actor_id, from_user, to_user, handoff_id, details)
        VALUES (%s, %s, %s, %s, %s, %s, %s)""",
        (consignment_id, type_, actor, from_user, to_user, handoff_id, Jsonb(details or {})))


def _active_user(conn: psycopg.Connection, user_id: str) -> None:
    row = conn.execute("SELECT active, role FROM app_user WHERE id = %s", (user_id,)).fetchone()
    if row is None or not row[0]:
        raise NotPermitted("unknown or deactivated user")
    if row[1] not in HOLDER_ROLES:
        raise NotPermitted(f"a {row[1]} cannot hold consignments")


def holder(conn: psycopg.Connection, consignment_id: str) -> str | None:
    row = conn.execute("""
        SELECT holder_id::text FROM custody_interval WHERE consignment_id = %s AND valid_to IS NULL""",
        (consignment_id,)).fetchone()
    return row[0] if row else None


def state(conn: psycopg.Connection, consignment_id: str) -> str:
    """CR1: held, in_handoff or rejected; or completed once custody has ended."""
    row = conn.execute("SELECT completed_at FROM consignment WHERE id = %s", (consignment_id,)).fetchone()
    if row is None:
        raise NotFound(f"no consignment {consignment_id}")
    if row[0] is not None:
        return "completed"
    open_ = conn.execute("SELECT state FROM custody_handoff WHERE consignment_id = %s AND state IN "
                         "('pending', 'rejected')", (consignment_id,)).fetchone()
    return {None: "held", "pending": "in_handoff", "rejected": "rejected"}[open_[0] if open_ else None]


def _open_handoff(conn: psycopg.Connection, consignment_id: str) -> dict | None:
    return conn.cursor(row_factory=dict_row).execute("""
        SELECT id::text, from_user::text, to_user::text, released_at, window_ends_at, state, gap_event_id::text
        FROM custody_handoff WHERE consignment_id = %s AND state IN ('pending', 'rejected')
        FOR UPDATE""", (consignment_id,)).fetchone()


def _end_gap(conn: psycopg.Connection, gap_event_id: str | None, at: datetime) -> None:
    if gap_event_id is None:
        return
    ev = conn.cursor(row_factory=dict_row).execute("""
        UPDATE integrity_event SET event_end = greatest(event_start, %s), updated_at = now()
        WHERE id = %s AND event_end IS NULL
        RETURNING id::text, consignment_id::text, type, severity, event_start, event_end, detected_at""",
        (at, gap_event_id)).fetchone()
    if ev:
        outbox.enqueue(conn, kafka.INTEGRITY_EVENTS, ev["consignment_id"], {**ev, "change": "updated"})


def create(conn: psycopg.Connection, actor: str, product_id: str, quantity: int, origin_facility_id: str,
           destination: str, expected_transit_min: int, device_id: str | None, now: datetime) -> str:
    """Record a new consignment held by the actor, with its logger assigned (CR8)."""
    _active_user(conn, actor)
    cid = conn.execute("""
        INSERT INTO consignment (product_id, quantity, origin_facility_id, destination, expected_transit_min)
        VALUES (%s, %s, %s, %s, %s) RETURNING id::text""",
        (product_id, quantity, origin_facility_id, destination, expected_transit_min)).fetchone()[0]
    conn.execute("INSERT INTO custody_interval (consignment_id, holder_id, valid_from) VALUES (%s, %s, %s)",
                 (cid, actor, now))
    if device_id:
        try:
            with conn.transaction():
                conn.execute("""INSERT INTO device_assignment (device_id, consignment_id, valid_from)
                                VALUES (%s, %s, %s)""", (device_id, cid, now))
        except psycopg.errors.ExclusionViolation:
            raise InvalidState("the logger is already assigned to another consignment") from None
    _log(conn, cid, "create", actor, None, actor, details={"device_id": device_id, "quantity": quantity})
    return cid


def release(conn: psycopg.Connection, actor: str, consignment_id: str, to_user: str, now: datetime,
            expected_transit_min: int | None = None) -> Handoff:
    """The holder releases the consignment to an intended receiver. Custody stays with the releaser
    until the receiver confirms (CR2); the confirmation window is the transit time plus 30 minutes (CR4)."""
    if holder(conn, consignment_id) != actor:
        raise NotPermitted("only the current holder can release a consignment")
    if _open_handoff(conn, consignment_id):
        raise InvalidState("the consignment already has a handoff in progress or a rejection to settle")
    _active_user(conn, to_user)
    if to_user == actor:
        raise InvalidState("cannot release a consignment to oneself")
    transit = expected_transit_min or conn.execute(
        "SELECT expected_transit_min FROM consignment WHERE id = %s", (consignment_id,)).fetchone()[0]
    window_ends = now + timedelta(minutes=transit) + CONFIRMATION_MARGIN
    hid = conn.execute("""
        INSERT INTO custody_handoff (consignment_id, from_user, to_user, released_at, window_ends_at)
        VALUES (%s, %s, %s, %s, %s) RETURNING id::text""",
        (consignment_id, actor, to_user, now, window_ends)).fetchone()[0]
    _log(conn, consignment_id, "release", actor, actor, to_user, hid,
         {"window_ends_at": window_ends.isoformat(), "expected_transit_min": transit})
    return Handoff(hid, consignment_id, actor, to_user, now, window_ends)


def confirm(conn: psycopg.Connection, actor: str, consignment_id: str, now: datetime) -> str:
    """The receiver confirms receipt; custody transfers at the time the confirmation is received (CR3). A
    confirmation after the window (a delayed handoff) also ends the custody gap raised at expiry."""
    h = _open_handoff(conn, consignment_id)
    if h is None or h["state"] != "pending":
        raise InvalidState("there is no handoff awaiting confirmation")
    if h["to_user"] != actor:
        raise NotPermitted("only the intended receiver can confirm")
    conn.execute("UPDATE custody_interval SET valid_to = %s WHERE consignment_id = %s AND valid_to IS NULL",
                 (now, consignment_id))
    conn.execute("INSERT INTO custody_interval (consignment_id, holder_id, valid_from) VALUES (%s, %s, %s)",
                 (consignment_id, actor, now))
    conn.execute("UPDATE custody_handoff SET state = 'confirmed', ended_at = %s WHERE id = %s", (now, h["id"]))
    _log(conn, consignment_id, "confirm", actor, h["from_user"], actor, h["id"],
         {"delayed": now > h["window_ends_at"]})
    _end_gap(conn, h["gap_event_id"], now)
    return h["id"]


def reject(conn: psycopg.Connection, actor: str, consignment_id: str, reason: str, now: datetime) -> str:
    """The receiver refuses the consignment: custody stays with the releaser, the reason is recorded and a
    custody gap is raised (CR5). The gap lasts until the releaser withdraws the handoff."""
    h = _open_handoff(conn, consignment_id)
    if h is None or h["state"] != "pending":
        raise InvalidState("there is no handoff awaiting confirmation")
    if h["to_user"] != actor:
        raise NotPermitted("only the intended receiver can reject")
    if not reason.strip():
        raise InvalidState("a rejection needs a reason")
    gap = h["gap_event_id"] or raise_custody_gap(conn, consignment_id, now, None, {
        "reason": "handoff rejected", "handoff_id": h["id"], "rejection": reason, "releaser": h["from_user"]})
    conn.execute("""UPDATE custody_handoff SET state = 'rejected', ended_at = %s, reason = %s, gap_event_id = %s
                    WHERE id = %s""", (now, reason, gap, h["id"]))
    _log(conn, consignment_id, "reject", actor, h["from_user"], actor, h["id"], {"reason": reason})
    return h["id"]


def cancel(conn: psycopg.Connection, actor: str, consignment_id: str, reason: str, now: datetime) -> str:
    """The releaser withdraws a pending handoff, or settles a rejected one (the consignment is back in the
    releaser's hands); any custody gap ends."""
    h = _open_handoff(conn, consignment_id)
    if h is None:
        raise InvalidState("there is no handoff to withdraw")
    if h["from_user"] != actor:
        raise NotPermitted("only the releaser can withdraw a handoff")
    conn.execute("UPDATE custody_handoff SET state = 'cancelled', ended_at = coalesce(ended_at, %s) WHERE id = %s",
                 (now, h["id"]))
    _log(conn, consignment_id, "cancel", actor, actor, None, h["id"], {"reason": reason, "was": h["state"]})
    _end_gap(conn, h["gap_event_id"], now)
    return h["id"]


def expire(conn: psycopg.Connection, handoff_id: str, now: datetime) -> str | None:
    """Called when a confirmation window ends. If the handoff is still unconfirmed, a custody-gap warning
    is raised for the releaser and the LGA officer (CR4). Returns the gap event id, if one was raised."""
    h = conn.cursor(row_factory=dict_row).execute("""
        SELECT id::text, consignment_id::text, from_user::text, to_user::text, window_ends_at, state, gap_event_id
        FROM custody_handoff WHERE id = %s FOR UPDATE""", (handoff_id,)).fetchone()
    if h is None or h["state"] != "pending" or h["gap_event_id"] or now < h["window_ends_at"]:
        return None
    gap = raise_custody_gap(conn, h["consignment_id"], h["window_ends_at"], None, {
        "reason": "handoff not confirmed within its window", "handoff_id": h["id"],
        "releaser": h["from_user"], "receiver": h["to_user"]})
    conn.execute("UPDATE custody_handoff SET gap_event_id = %s WHERE id = %s", (gap, handoff_id))
    return gap


def complete(conn: psycopg.Connection, actor: str, consignment_id: str, now: datetime) -> None:
    """The holder records that the consignment has been delivered or used: custody and the logger's
    assignment end."""
    if holder(conn, consignment_id) != actor:
        raise NotPermitted("only the current holder can complete a consignment")
    if _open_handoff(conn, consignment_id):
        raise InvalidState("settle the handoff in progress first")
    conn.execute("UPDATE custody_interval SET valid_to = %s WHERE consignment_id = %s AND valid_to IS NULL",
                 (now, consignment_id))
    conn.execute("UPDATE device_assignment SET valid_to = %s WHERE consignment_id = %s AND valid_to IS NULL",
                 (now, consignment_id))
    conn.execute("UPDATE consignment SET completed_at = %s WHERE id = %s", (now, consignment_id))
    _log(conn, consignment_id, "complete", actor, actor, None)
