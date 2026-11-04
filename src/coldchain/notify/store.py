"""Notifications in the database (FR9, Tables 3.19 and 3.20).

A notification request (from the outbox, via Kafka) names the people to tell and how urgently. expand()
turns it into one row per recipient and channel: every recipient gets a dashboard notification, so an
SMS failure never leaves an incident invisible; urgent requests also get an SMS now, and retrospective
ones an SMS in the next daily RETRO digest. dispatch() sends due SMS through the gateway, composing each
message as it is sent (so each attempt carries a fresh response code, and only the masked text is
stored). Delivery reports, retries, the 10-minute limit and the digest move each SMS to its final state;
a message that cannot be delivered is handled as Table 3.20 sets out.

As elsewhere, the functions run inside the caller's transaction. Those that can change an incident's
escalation return the ids of the incidents whose workflows must be told.
"""
import re
import uuid
from datetime import datetime, timedelta

import httpx
import psycopg
from psycopg.rows import dict_row

from coldchain.config import get_settings
from coldchain.incidents import store as incidents
from coldchain.notify import codes, gateway, templates

RETRY_DELAYS = [timedelta(minutes=1), timedelta(minutes=2), timedelta(minutes=2)]   # 3 retries in 5 minutes
FINAL_STATUS_WITHIN = timedelta(minutes=10)
INCIDENT_SMS = ("alert", "escalation", "overdue", "reminder", "reopened")   # carry a code; escalate on failure


# Context for templates ------------------------------------------------------------------------------------

def incident_view(conn: psycopg.Connection, incident_id: str) -> templates.Incident:
    r = conn.cursor(row_factory=dict_row).execute("""
        SELECT i.incident_no, i.type, i.severity, i.acknowledged_at, e.event_start, e.event_end, e.peak_value,
               f.name AS facility, d.equipment_type, c.consignment_no, c.destination, u.full_name AS owner
        FROM incident i JOIN integrity_event e ON e.id = i.integrity_event_id
             JOIN app_user u ON u.id = i.owner_id
             LEFT JOIN device d ON d.id = i.device_id
             LEFT JOIN facility f ON f.id = d.facility_id
             LEFT JOIN consignment c ON c.id = i.consignment_id
        WHERE i.id = %s""", (incident_id,)).fetchone()
    return templates.Incident(
        r["incident_no"], r["type"], r["severity"],
        templates.place(r["facility"], r["equipment_type"], r["consignment_no"], r["destination"]),
        float(r["peak_value"]) if r["peak_value"] is not None else None, r["event_start"], r["event_end"],
        templates.name(r["owner"]), r["acknowledged_at"] is not None)


def _consignment_no(conn: psycopg.Connection, consignment_id: str | None) -> int | None:
    if consignment_id is None:
        return None
    return conn.execute("SELECT consignment_no FROM consignment WHERE id = %s", (consignment_id,)).fetchone()[0]


def next_digest(now: datetime) -> datetime:
    at = now.replace(hour=get_settings().digest_hour_utc, minute=0, second=0, microsecond=0)
    return at if at > now else at + timedelta(days=1)


def _phone(conn: psycopg.Connection, user_id: str) -> tuple[str | None, str | None]:
    """The number to text, or why there is none."""
    row = conn.execute("SELECT phone_number, active, phone_deactivated_at FROM app_user WHERE id = %s",
                       (user_id,)).fetchone()
    if row is None or not row[1]:
        return None, "user deactivated"
    if row[0] is None:
        return None, "no phone number"
    if row[2] is not None:
        return None, "phone number deactivated"
    return row[0], None


# Requests ------------------------------------------------------------------------------------------------

def _insert(conn: psycopg.Connection, request_id: str, kind: str, channel: str, recipient: str, body: str,
            status: str, due_at: datetime, *, phone: str | None = None, incident_id: str | None = None,
            consignment_id: str | None = None, handoff_id: str | None = None, tier: str | None = None,
            reason: str | None = None, failure_reason: str | None = None) -> str | None:
    row = conn.execute("""
        INSERT INTO notification (request_id, kind, channel, recipient_id, phone_number, incident_id,
                                  consignment_id, handoff_id, tier, reason, body, status, due_at, failure_reason)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (request_id, recipient_id, channel) DO NOTHING RETURNING id::text""",
        (request_id, kind, channel, recipient, phone, incident_id, consignment_id, handoff_id, tier, reason, body,
         status, due_at, failure_reason)).fetchone()
    return row[0] if row else None


def expand(conn: psycopg.Connection, req: dict, now: datetime) -> list[str]:
    """Create the notifications a request asks for. Idempotent. Returns the incidents whose escalation
    changed (a recipient who cannot be texted at all counts as an undelivered SMS)."""
    incident_id, consignment_id = req.get("incident_id"), req.get("consignment_id")
    view = incident_view(conn, incident_id) if incident_id else None
    cons_no = _consignment_no(conn, consignment_id)
    targets = [(req["kind"], r) for r in req["recipients"]]
    targets += [("cc", r) for r in req.get("cc", []) if r not in req["recipients"]]
    touched: list[str] = []
    for kind, recipient in targets:
        common = {"incident_id": incident_id, "consignment_id": consignment_id, "handoff_id": req.get("handoff_id"),
                  "tier": req.get("tier"), "reason": req.get("reason")}
        line = templates.dashboard("undelivered" if req.get("reason") == "undelivered" else kind, view, now, cons_no)
        _insert(conn, req["id"], kind, "dashboard", recipient, line, "posted", now, **common)
        if req["urgency"] == "dashboard":
            continue
        phone, why_not = _phone(conn, recipient)
        status, due = ("held", next_digest(now)) if req["urgency"] == "digest" else ("queued", now)
        if why_not:
            status = "rejected"
        n = _insert(conn, req["id"], kind, "sms", recipient, line, status, due, phone=phone,
                    failure_reason=why_not, **common)
        if n and why_not:
            touched += _final(conn, _get(conn, n), "rejected", why_not, now)
    return touched


def _direct(conn: psycopg.Connection, kind: str, recipient: str, body: str, now: datetime, *, sms_only=False,
            **links) -> None:
    """A notification composed here rather than requested (replies, failure notices, digests)."""
    request_id = str(uuid.uuid4())
    if not sms_only:
        _insert(conn, request_id, kind, "dashboard", recipient, body, "posted", now, **links)
    phone, _ = _phone(conn, recipient)
    if phone:
        _insert(conn, request_id, kind, "sms", recipient, body, "queued", now, phone=phone, **links)


# Sending ------------------------------------------------------------------------------------------------

NOTIFICATION = """
    SELECT id::text, request_id::text, kind, channel, recipient_id::text, phone_number, incident_id::text,
           consignment_id::text, handoff_id::text, tier, reason, body, status, attempts, first_sent_at
    FROM notification WHERE id = %s FOR UPDATE
"""


def _get(conn: psycopg.Connection, notification_id: str) -> dict:
    return conn.cursor(row_factory=dict_row).execute(NOTIFICATION, (notification_id,)).fetchone()


def compose(conn: psycopg.Connection, n: dict, now: datetime) -> tuple[str, str]:
    """The SMS text for a notification, and the same text with any response code masked (to store)."""
    kind, recipient = n["kind"], n["recipient_id"]
    code = None
    if kind in INCIDENT_SMS:
        i = incident_view(conn, n["incident_id"])
        code = codes.issue(conn, recipient, now, incident_id=n["incident_id"])
        undelivered = n["reason"] == "undelivered"
        text = {"alert": lambda: templates.alert(i, code, now),
                "escalation": lambda: templates.escalation(i, code, now, undelivered),
                "overdue": lambda: templates.overdue(i, code, now),
                "reminder": lambda: templates.overdue(i, code, now, reminder=True),
                "reopened": lambda: templates.reopened(i, code, now)}[kind]()
    elif kind == "cc":
        text = templates.cc(incident_view(conn, n["incident_id"]), now)
    elif kind == "handoff":
        h = conn.execute("""
            SELECT c.consignment_no, c.quantity, p.name, u.full_name FROM custody_handoff h
            JOIN consignment c ON c.id = h.consignment_id JOIN product p ON p.id = c.product_id
            JOIN app_user u ON u.id = h.from_user WHERE h.id = %s""", (n["handoff_id"],)).fetchone()
        code = codes.issue(conn, recipient, now, handoff_id=n["handoff_id"])
        text = templates.handoff(h[0], templates.name(h[3]), h[1], h[2], code)
    elif kind == "reply" and n["reason"] == "code":   # an acknowledgement repeats the code for RES
        code = codes.issue(conn, recipient, now, incident_id=n["incident_id"], keep_earlier=True)
        text = n["body"].replace("****", code)
    else:                                   # composed when created: digest, reply, notices
        text = n["body"]
    return text, re.sub(rf"\b{code}\b", "****", text) if code else text


def dispatch(conn: psycopg.Connection, now: datetime, client: httpx.Client | None = None,
             limit: int = 50) -> tuple[int, list[str]]:
    """Send the SMS that are due, one transaction each. Returns how many were attempted and the incidents
    whose escalation changed."""
    attempted, touched = 0, []
    while attempted < limit:
        with conn.transaction():
            row = conn.execute("""
                SELECT id::text FROM notification WHERE channel = 'sms' AND status IN ('queued', 'retrying')
                  AND due_at <= %s ORDER BY due_at LIMIT 1 FOR UPDATE SKIP LOCKED""", (now,)).fetchone()
            if row is None:
                break
            touched += send_one(conn, _get(conn, row[0]), now, client)
            attempted += 1
    return attempted, touched


def send_one(conn: psycopg.Connection, n: dict, now: datetime, client: httpx.Client | None = None) -> list[str]:
    try:
        text, stored = compose(conn, n, now)
        if not templates.fits(text):
            raise templates.TooLong(stored)
    except templates.TooLong:   # a template defect: never send a split or UCS-2 message
        return _final(conn, n, "rejected", "message does not fit one GSM SMS (template defect)", now)
    result = gateway.send(n["phone_number"], text, client)
    conn.execute("""
        UPDATE notification SET body = %s, attempts = attempts + 1, first_sent_at = coalesce(first_sent_at, %s),
               sent_at = %s, gateway_status = %s, gateway_message_id = %s
        WHERE id = %s""", (stored, now, now, result.status, result.message_id, n["id"]))
    n.update(attempts=n["attempts"] + 1, first_sent_at=n["first_sent_at"] or now)
    if result.outcome == "sent" and get_settings().sms_trust_sent:     # later reports then change nothing
        conn.execute("UPDATE notification SET status = 'delivered', delivered_at = %s WHERE id = %s", (now, n["id"]))
        _audit(conn, n, "delivered", "accepted by the gateway; delivery reports not used", now)
        return []
    if result.outcome == "sent":
        conn.execute("UPDATE notification SET status = 'sent' WHERE id = %s", (n["id"],))
        return []
    if result.outcome == "rejected":
        return _final(conn, n, "rejected", result.status, now)
    return _failed_attempt(conn, n, result.status, now)


def _failed_attempt(conn: psycopg.Connection, n: dict, reason: str, now: datetime) -> list[str]:
    """A temporary failure: retried up to three times over five minutes (Table 3.20)."""
    if n["attempts"] <= len(RETRY_DELAYS):
        conn.execute("""UPDATE notification SET status = 'retrying', due_at = %s, failure_reason = %s
                        WHERE id = %s""", (now + RETRY_DELAYS[n["attempts"] - 1], reason, n["id"]))
        return []
    return _final(conn, n, "undelivered", reason, now)


# Delivery reports ---------------------------------------------------------------------------------------

def on_delivery_report(conn: psycopg.Connection, message_id: str, status: str, failure_reason: str | None,
                       now: datetime) -> list[str]:
    row = conn.execute("SELECT id::text FROM notification WHERE gateway_message_id = %s", (message_id,)).fetchone()
    if row is None:
        return []                      # not ours, or a report for an attempt that has since been retried
    n = _get(conn, row[0])
    conn.execute("UPDATE notification SET gateway_status = %s WHERE id = %s", (status, n["id"]))
    if n["status"] != "sent":
        return []                      # already final (e.g. timed out); a late report changes nothing
    result = gateway.outcome(status, failure_reason)
    reason = f"{status}: {failure_reason}" if failure_reason else status
    if result == "delivered":
        conn.execute("UPDATE notification SET status = 'delivered', delivered_at = %s WHERE id = %s", (now, n["id"]))
        _audit(conn, n, "delivered", None, now)
        return []
    if result == "rejected":
        return _final(conn, n, "rejected", reason, now)
    if result == "failed":
        return _failed_attempt(conn, n, reason, now)
    return []


def _audit(conn: psycopg.Connection, n: dict, status: str, reason: str | None, now: datetime) -> None:
    """Final delivery outcomes go in the audit trail of the incidents the message was about."""
    incident_ids = [n["incident_id"]] if n["incident_id"] else [r[0] for r in conn.execute(
        "SELECT DISTINCT incident_id::text FROM notification WHERE digest_id = %s AND incident_id IS NOT NULL",
        (n["id"],))]
    for incident_id in incident_ids:
        incidents.record(conn, incident_id, "delivery_report", {
            "notification_id": n["id"], "kind": n["kind"], "recipient": n["recipient_id"], "status": status,
            "reason": reason, "attempts": n["attempts"], "at": now})


def _final(conn: psycopg.Connection, n: dict, status: str, reason: str, now: datetime) -> list[str]:
    """An SMS that will not be delivered: undelivered (failed, retries exhausted, or no final status in
    time) or rejected. Table 3.20: the next tier is notified at once and the incident flagged; for a
    rejected number the administrator is alerted too."""
    conn.execute("UPDATE notification SET status = %s, failure_reason = %s WHERE id = %s", (status, reason, n["id"]))
    _audit(conn, n, status, reason, now)
    touched = []
    if n["kind"] in INCIDENT_SMS:
        outcome = incidents.escalate_undelivered(conn, n["incident_id"], n["tier"] or "owner", n["recipient_id"],
                                                 now)
        if outcome.changed:
            touched.append(outcome.incident_id)
    elif n["kind"] == "digest":
        for (incident_id,) in conn.execute("""SELECT DISTINCT incident_id::text FROM notification
                                              WHERE digest_id = %s AND incident_id IS NOT NULL""", (n["id"],)).fetchall():
            incidents.flag_undelivered(conn, incident_id, n["recipient_id"], n["kind"])
    elif n["kind"] == "handoff":
        releaser, receiver = conn.execute("""
            SELECT h.from_user::text, u.full_name FROM custody_handoff h JOIN app_user u ON u.id = h.to_user
            WHERE h.id = %s""", (n["handoff_id"],)).fetchone()
        _direct(conn, "undelivered", releaser,
                templates.handoff_undelivered(_consignment_no(conn, n["consignment_id"]), templates.name(receiver)),
                now, consignment_id=n["consignment_id"], handoff_id=n["handoff_id"])
    if status == "rejected" and n["kind"] not in ("admin", "reply"):
        user = conn.execute("SELECT full_name FROM app_user WHERE id = %s", (n["recipient_id"],)).fetchone()[0]
        for (admin,) in conn.execute("SELECT id::text FROM app_user WHERE role = 'admin' AND active").fetchall():
            _direct(conn, "admin", admin, templates.admin_rejected(user, n["phone_number"], reason), now)
    return touched


# Periodic work -----------------------------------------------------------------------------------------

def sweep(conn: psycopg.Connection, now: datetime) -> list[str]:
    """Time out SMS with no final status within 10 minutes of dispatch, and send due RETRO digests."""
    touched = []
    for (nid,) in conn.execute("""SELECT id::text FROM notification WHERE status = 'sent' AND first_sent_at <= %s
                                  FOR UPDATE SKIP LOCKED""", (now - FINAL_STATUS_WITHIN,)).fetchall():
        touched += _final(conn, _get(conn, nid), "undelivered", "no final delivery status within 10 minutes", now)
    held = conn.execute("""
        SELECT recipient_id::text, array_agg(id::text ORDER BY created_at), array_agg(DISTINCT incident_id::text)
        FROM notification WHERE status = 'held' AND due_at <= %s GROUP BY recipient_id""", (now,)).fetchall()
    for recipient, ids, incident_ids in held:
        numbers = [r[0] for r in conn.execute("SELECT incident_no FROM incident WHERE id = ANY(%s::uuid[]) "
                                              "ORDER BY incident_no", (incident_ids,))]
        request_id = str(uuid.uuid4())
        phone, _ = _phone(conn, recipient)
        digest_id = _insert(conn, request_id, "digest", "sms", recipient, templates.digest(now, numbers),
                            "queued" if phone else "rejected", now, phone=phone,
                            failure_reason=None if phone else "no usable phone number")
        conn.execute("UPDATE notification SET status = 'digested', digest_id = %s WHERE id = ANY(%s::uuid[])",
                     (digest_id, ids))
    return touched


def mark_read(conn: psycopg.Connection, user_id: str, notification_id: str) -> bool:
    return conn.execute("""UPDATE notification SET read_at = coalesce(read_at, now())
                           WHERE id = %s AND recipient_id = %s AND channel = 'dashboard'""",
                        (notification_id, user_id)).rowcount == 1
