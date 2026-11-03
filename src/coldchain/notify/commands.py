"""Incoming SMS: the reply commands and their security checks (Table 3.19, Section 3.4.6).

handle() decides what an incoming message does and what, if anything, is replied. Every message is
logged in sms_inbound with its outcome (response codes masked). The checks, in order:
- a message the gateway delivers twice (same id) is processed once;
- a number may send 20 commands an hour; beyond that, messages are logged and ignored (one reply says so);
- the number must belong to an active user and must not have been deactivated (lost phone); otherwise
  the message is rejected without a reply, so an unknown sender learns nothing and costs nothing;
- the command must parse, the incident or consignment must exist and be open, the sender must be entitled
  to act on it, and RES, REC and REJ must carry the sender's current one-time code.
A command that has already taken effect (a second ACK) is accepted without further effect.

The command itself runs in a savepoint: if it fails, nothing it did is kept; the log entry is, and so is
the count of wrong codes, which is written after the savepoint is rolled back.
"""
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import psycopg

from coldchain import custody
from coldchain.errors import InvalidState, NotFound, NotPermitted
from coldchain.incidents import store as incidents
from coldchain.notify import codes, store, templates

RATE_LIMIT = 20
RATE_WINDOW = timedelta(hours=1)
NUMBER = r"#?(\d{1,9})"
CODE = r"([A-Za-z0-9]{3,8})"
PATTERNS = {
    "ACK": re.compile(rf"^ACK\s+{NUMBER}\s*$", re.IGNORECASE),
    "RES": re.compile(rf"^RES\s+{NUMBER}\s+{CODE}(?:\s+(.*))?$", re.IGNORECASE | re.DOTALL),
    "REC": re.compile(rf"^REC\s+{NUMBER}\s+{CODE}\s*$", re.IGNORECASE),
    "REJ": re.compile(rf"^REJ\s+{NUMBER}\s+{CODE}(?:\s+(.*))?$", re.IGNORECASE | re.DOTALL),
    "HELP": re.compile(r"^HELP\s*$", re.IGNORECASE),
}
USAGE = {"ACK": "ACK <incident>", "RES": "RES <incident> <code> <note>", "REC": "REC <consignment> <code>",
         "REJ": "REJ <consignment> <code> <reason>"}


class Rejected(Exception):
    """The command is refused; the message is the reply."""


@dataclass
class Result:
    outcome: str                         # accepted | no_effect | rejected | rate_limited | duplicate
    reason: str | None = None
    reply: str | None = None
    command: str | None = None
    incident_id: str | None = None       # its workflow must be told (when changed)
    handoff_id: str | None = None        # its timer must be told (when settled)
    changed: bool = False
    links: dict = field(default_factory=dict)
    wrong_code_for: dict | None = None   # incident_id or handoff_id whose code was wrong


def parse(text: str) -> tuple[str, tuple]:
    """Returns the command and its arguments; raises Rejected with a usage hint if it does not parse."""
    text = text.strip()
    word = text.split(maxsplit=1)[0].upper() if text else ""
    if word == "CLOSE":
        raise Rejected("Closing is done on the dashboard by a reviewing officer.")
    if word not in PATTERNS:
        raise Rejected("Not understood. Send HELP for the list of commands.")
    m = PATTERNS[word].match(text)
    if not m:
        raise Rejected(f"Not understood. Use: {USAGE[word]}")
    return word, m.groups()


def mask(text: str, command: str | None) -> str:
    """The text as stored: the response code (third word of RES, REC and REJ) replaced by ****."""
    if command in ("RES", "REC", "REJ"):
        words = text.split(maxsplit=3)
        if len(words) >= 3:
            words[2] = "****"
            return " ".join(words)
    return text


def handle(conn: psycopg.Connection, gateway_id: str, from_number: str, text: str, now: datetime,
           to_number: str | None = None, link_id: str | None = None) -> Result:
    inbound = conn.execute("""
        INSERT INTO sms_inbound (gateway_id, from_number, to_number, text, link_id, received_at, outcome)
        VALUES (%s, %s, %s, '', %s, %s, 'rejected') ON CONFLICT (gateway_id) DO NOTHING RETURNING id""",
        (gateway_id, from_number, to_number, link_id, now)).fetchone()
    if inbound is None:
        return Result("duplicate", "already received")
    result, user = _decide(conn, from_number, text, now)
    try:
        command = parse(text)[0]
    except Rejected:
        command = None
    conn.execute("""
        UPDATE sms_inbound SET text = %s, user_id = %s, command = %s, outcome = %s, reason = %s, incident_id = %s,
               consignment_id = %s WHERE id = %s""",
        (mask(text, command), user, command, result.outcome, result.reason, result.links.get("incident_id"),
         result.links.get("consignment_id"), inbound[0]))
    if result.reply and user:
        store._direct(conn, "reply", user, templates.gsm(result.reply)[:templates.LIMIT], now, sms_only=True,
                      incident_id=result.links.get("incident_id"), consignment_id=result.links.get("consignment_id"))
    return result


def _decide(conn: psycopg.Connection, from_number: str, text: str, now: datetime) -> tuple[Result, str | None]:
    recent = conn.execute("SELECT count(*) FROM sms_inbound WHERE from_number = %s AND received_at > %s",
                          (from_number, now - RATE_WINDOW)).fetchone()[0]   # includes this message
    user = conn.execute("""SELECT id::text, phone_deactivated_at FROM app_user
                           WHERE phone_number = %s AND active""", (from_number,)).fetchone()
    if recent > RATE_LIMIT:
        reply = "Too many messages from this number. Please wait an hour or use the dashboard."
        return Result("rate_limited", f"more than {RATE_LIMIT} messages in an hour",
                      reply if recent == RATE_LIMIT + 1 else None), (user[0] if user and not user[1] else None)
    if user is None:
        return Result("rejected", "unregistered number"), None
    if user[1] is not None:
        return Result("rejected", "deactivated number"), None
    user_id = user[0]
    try:
        command, args = parse(text)
    except Rejected as e:
        return Result("rejected", "not understood", str(e)), user_id
    if command == "HELP":
        return Result("accepted", None, templates.HELP, command), user_id
    result = Result("rejected", command=command)
    try:
        with conn.transaction():          # savepoint: a failed command leaves nothing behind
            {"ACK": _ack, "RES": _res, "REC": _rec, "REJ": _rej}[command](conn, user_id, args, now, result)
    except Rejected as e:
        result.outcome, result.reason, result.reply = "rejected", result.reason or str(e), str(e)
    except (NotFound, NotPermitted, InvalidState) as e:
        result.outcome, result.reason, result.reply = "rejected", str(e), _sentence(str(e))
    if result.wrong_code_for:
        codes.failed(conn, user_id, now, **result.wrong_code_for)
    return result, user_id


def _sentence(message: str) -> str:
    return f"Not accepted: {message}."


def _incident(conn: psycopg.Connection, number: str, result: Result) -> dict:
    row = conn.execute("SELECT id::text FROM incident WHERE incident_no = %s", (int(number),)).fetchone()
    if row is None:
        raise Rejected(f"No incident #{number}. Check the number in your SMS.")
    result.links["incident_id"] = result.incident_id = row[0]
    inc = incidents._get(conn, row[0])
    if inc["state"] == "closed":   # Table 3.20: recorded; the sender is told it is closed
        result.reason = "incident closed"
        raise Rejected(f"#{number} is closed; no action taken.")
    return inc


def _ack(conn, user_id, args, now, result: Result) -> None:
    (number,) = args
    _incident(conn, number, result)
    outcome = incidents.acknowledge(conn, result.incident_id, user_id, "sms")
    result.changed = outcome.changed
    result.outcome = "accepted" if outcome.changed else "no_effect"
    result.reply = (f"OK #{number} acknowledged. When fixed: RES {number} <code> <note>" if outcome.changed
                    else f"#{number} was already acknowledged; no change.")


def _res(conn, user_id, args, now, result: Result) -> None:
    number, code, note = args
    inc = _incident(conn, number, result)
    if inc["state"] == "resolved":
        result.outcome, result.reply = "no_effect", f"#{number} is already resolved and awaiting review."
        return
    if not incidents.entitled(conn, inc, user_id):
        raise NotPermitted(f"you cannot act on #{number}")
    if not (note or "").strip():
        result.reason = "no note"
        raise Rejected(f"Add what you did: RES {number} <code> <note>")
    problem = codes.check(conn, user_id, code, now, incident_id=result.incident_id)
    if problem:
        result.reason = problem
        if problem == codes.WRONG:
            result.wrong_code_for = {"incident_id": result.incident_id}
        raise Rejected(f"Not accepted: {problem} for #{number}. Use the code in your latest SMS, or the dashboard.")
    incidents.resolve(conn, result.incident_id, user_id, note.strip(), "sms")
    result.changed, result.outcome = True, "accepted"
    result.reply = f"OK #{number} resolved and sent for review. Thank you."


def _handoff(conn, user_id, number: str, code: str, now, result: Result) -> str:
    row = conn.execute("""
        SELECT c.id::text, h.id::text, h.to_user::text FROM consignment c
        LEFT JOIN custody_handoff h ON h.consignment_id = c.id AND h.state = 'pending'
        WHERE c.consignment_no = %s""", (int(number),)).fetchone()
    if row is None:
        raise Rejected(f"No consignment {number}. Check the number in your SMS.")
    result.links["consignment_id"], handoff_id, receiver = row
    if handoff_id is None:
        result.reason = "no handoff awaiting confirmation"
        raise Rejected(f"Consignment {number} is not awaiting confirmation.")
    if receiver != user_id:
        raise NotPermitted(f"consignment {number} was not released to you")
    problem = codes.check(conn, user_id, code, now, handoff_id=handoff_id)
    if problem:
        result.reason = problem
        if problem == codes.WRONG:
            result.wrong_code_for = {"handoff_id": handoff_id}
        raise Rejected(f"Not accepted: {problem} for consignment {number}. Use the code in your latest SMS.")
    result.handoff_id = handoff_id
    return result.links["consignment_id"]


def _rec(conn, user_id, args, now, result: Result) -> None:
    number, code = args
    cid = _handoff(conn, user_id, number, code, now, result)
    custody.confirm(conn, user_id, cid, now)
    result.changed, result.outcome = True, "accepted"
    result.reply = f"OK consignment {number} received. It is now in your custody."


def _rej(conn, user_id, args, now, result: Result) -> None:
    number, code, reason = args
    if not (reason or "").strip():
        result.reason = "no reason"
        raise Rejected(f"Give a reason: REJ {number} <code> <reason>")
    cid = _handoff(conn, user_id, number, code, now, result)
    custody.reject(conn, user_id, cid, reason.strip(), now)
    result.changed, result.outcome = True, "accepted"
    result.reply = f"Consignment {number} rejected. It stays with the sender, who has been told."

