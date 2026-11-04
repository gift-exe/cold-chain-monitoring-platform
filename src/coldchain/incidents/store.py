"""Incidents in the database: creation from integrity events, the actions people take, and the
escalation steps the policy requires (Sections 3.4.2 to 3.4.4).

Every change to an incident is recorded in its audit trail (incident_action) in the same transaction,
and every notification the change requires is written to the outbox in that transaction too. The
functions here never commit on their own; callers run them inside conn.transaction().
"""
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from coldchain import kafka, outbox
from coldchain.errors import InvalidState, NotFound, NotPermitted
from coldchain.incidents import ownership
from coldchain.incidents.policy import TIERS, Clock, Policy, Step, above, advance, next_step, rank
from coldchain.notify import codes

RETROSPECTIVE_AFTER = timedelta(minutes=15)   # Section 3.4.4
RECURRENCE_WITHIN = timedelta(minutes=60)     # FR5.5
ALARM_LINK_WITHIN = timedelta(minutes=30)     # a local alarm this close to an excursion belongs to it
REVIEWER_ROLES = ("lga_officer", "state_officer", "national_officer")


@dataclass(frozen=True)
class Outcome:
    incident_id: str
    changed: bool      # whether anything changed (the workflow must then be told)
    message: str


# Audit trail ----------------------------------------------------------------------------------------

def record(conn: psycopg.Connection, incident_id: str, action: str, details: dict,
           actor_id: str | None = None, channel: str = "system") -> None:
    conn.execute("""
        INSERT INTO incident_action (incident_id, action, actor_id, channel, details)
        VALUES (%s, %s, %s, %s, %s)""", (incident_id, action, actor_id, channel, Jsonb(_plain(details))))


def _plain(value):
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, datetime):
        return value.isoformat()
    return value


# Creation from integrity events ---------------------------------------------------------------------

def mode_for(event_end: datetime | None, detected_at: datetime) -> str:
    """Operational if the condition is ongoing or ended less than 15 minutes before detection."""
    if event_end is None or detected_at - event_end < RETROSPECTIVE_AFTER:
        return "operational"
    return "retrospective"


def urgency(severity: str, mode: str) -> str:
    """How notifications for an incident are sent (Table 3.15)."""
    if severity == "critical":
        return "sms" if mode == "operational" else "digest"
    return "dashboard"


def _event(conn: psycopg.Connection, event_id: str) -> dict | None:
    return conn.cursor(row_factory=dict_row).execute("""
        SELECT id::text, device_id::text, consignment_id::text, incident_id::text, type, severity,
               event_start, event_end, peak_value, borderline, detected_at, details
        FROM integrity_event WHERE id = %s FOR UPDATE""", (event_id,)).fetchone()


def raise_custody_gap(conn: psycopg.Connection, consignment_id: str, start: datetime, end: datetime | None,
                      details: dict) -> str:
    """Store a custody-gap integrity event and queue it for the incident service."""
    ev = conn.cursor(row_factory=dict_row).execute("""
        INSERT INTO integrity_event (consignment_id, type, severity, event_start, event_end, details)
        VALUES (%s, 'custody_gap', 'warning', %s, %s, %s)
        RETURNING id::text, consignment_id::text, type, severity, event_start, event_end, detected_at""",
        (consignment_id, start, end, Jsonb(_plain(details)))).fetchone()
    outbox.enqueue(conn, kafka.INTEGRITY_EVENTS, consignment_id, {**ev, "change": "created"})
    return ev["id"]


def on_event(conn: psycopg.Connection, event_id: str) -> Outcome | None:
    """Create, update or reopen the incident for an integrity event. Idempotent: an event already
    handled changes nothing unless its severity or condition has changed."""
    ev = _event(conn, event_id)
    if ev is None:
        return None
    if ev["incident_id"]:
        return _update(conn, ev)
    if ev["type"] == "local_alarm":
        return _link_alarm(conn, ev)
    reopened = _recurrence(conn, ev)
    if reopened:
        return reopened
    return _create(conn, ev)


def _create(conn: psycopg.Connection, ev: dict) -> Outcome:
    if ev["type"] == "custody_gap":
        own = ownership.for_custody_gap(conn, ev["consignment_id"], ev["event_start"])
    elif ev["device_id"]:
        own = ownership.for_device(conn, ev["device_id"], ev["event_start"])
    else:
        own = ownership.for_consignment(conn, ev["consignment_id"], ev["event_start"])
    mode = mode_for(ev["event_end"], ev["detected_at"])
    cc = list(own.cc_ids)
    if mode == "retrospective":   # retrospective incidents go to the owner and the LGA officer
        cc += [o for o in ownership.officers(conn, own.jurisdiction_id, "lga") if o not in cc + [own.owner_id]]
    received = None
    if ev["device_id"]:
        received = conn.execute("""
            SELECT max(received_at) FROM reading WHERE device_id = %s AND event_time >= %s
                   AND received_at <= %s""", (ev["device_id"], ev["event_start"], ev["detected_at"])).fetchone()[0]
    subject = ev["device_id"] or ev["consignment_id"]
    key = f"{subject}:{ev['type']}:{ev['event_start'].isoformat()}"
    row = conn.execute("""
        INSERT INTO incident (incident_key, integrity_event_id, type, severity, mode, owner_id, device_id,
                              consignment_id, jurisdiction_id, cc_ids, ownership, condition_cleared,
                              received_at, detected_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::uuid[], %s, %s, %s, %s)
        RETURNING id::text, incident_no""",
        (key, ev["id"], ev["type"], ev["severity"], mode, own.owner_id, ev["device_id"],
         own.consignment_id or ev["consignment_id"], own.jurisdiction_id, cc, own.rule,
         ev["event_end"] is not None, received, ev["detected_at"])).fetchone()
    incident_id = row[0]
    conn.execute("UPDATE integrity_event SET incident_id = %s WHERE id = %s", (incident_id, ev["id"]))
    record(conn, incident_id, "created", {
        "integrity_event_id": ev["id"], "type": ev["type"], "severity": ev["severity"], "mode": mode,
        "event_start": ev["event_start"], "event_end": ev["event_end"], "owner_id": own.owner_id,
        "ownership": own.rule, "cc": cc})
    if ev["type"] in ("heat_excursion", "freeze"):
        _attach_alarms(conn, incident_id, ev)
    if own.custody_gap:
        raise_custody_gap(conn, own.consignment_id, ev["event_start"], ev["event_start"],
                          {"reason": "no custodian at the start of an event", "incident_id": incident_id})
    return Outcome(incident_id, True, f"incident {row[1]} created ({mode}), owner {own.owner_id}")


def _update(conn: psycopg.Connection, ev: dict) -> Outcome:
    severity, cleared = conn.execute("SELECT severity, condition_cleared FROM incident WHERE id = %s FOR UPDATE",
                                     (ev["incident_id"],)).fetchone()
    changed = False
    if ev["severity"] == "critical" and severity == "warning":
        conn.execute("UPDATE incident SET severity = 'critical' WHERE id = %s", (ev["incident_id"],))
        record(conn, ev["incident_id"], "updated", {"severity": "critical", "integrity_event_id": ev["id"]})
        changed = True
    ongoing = conn.execute("""
        SELECT EXISTS (SELECT 1 FROM integrity_event e JOIN incident i ON i.id = e.incident_id
                       WHERE i.id = %s AND e.type = i.type AND e.event_end IS NULL)""",
        (ev["incident_id"],)).fetchone()[0]
    if ongoing == cleared:
        conn.execute("UPDATE incident SET condition_cleared = %s WHERE id = %s", (not ongoing, ev["incident_id"]))
        record(conn, ev["incident_id"], "condition_cleared" if not ongoing else "updated",
               {"integrity_event_id": ev["id"], "event_end": ev["event_end"],
                **({} if not ongoing else {"condition": "resumed"})})
        changed = True
    return Outcome(ev["incident_id"], changed, "updated" if changed else "no change")


def _recurrence(conn: psycopg.Connection, ev: dict) -> Outcome | None:
    """FR5.5: the same condition on the same device within 60 minutes of resolution reopens the
    resolved incident, and the owner is notified again."""
    if not ev["device_id"]:
        return None
    row = conn.execute("""
        SELECT id::text, incident_no FROM incident
        WHERE device_id = %s AND type = %s AND state = 'resolved'
          AND %s BETWEEN resolved_at AND resolved_at + %s
        ORDER BY resolved_at DESC LIMIT 1 FOR UPDATE""",
        (ev["device_id"], ev["type"], ev["event_start"], RECURRENCE_WITHIN)).fetchone()
    if row is None:
        return None
    incident_id = row[0]
    conn.execute("""
        UPDATE incident SET state = 'open', reopened_at = now(), reopen_count = reopen_count + 1,
               acknowledged_at = NULL, acknowledged_by = NULL, resolved_at = NULL, resolved_by = NULL,
               ack_due_from = now(), ack_stage = -1, resolve_due_from = NULL, resolve_stage = 0,
               condition_cleared = %s,
               severity = CASE WHEN %s = 'critical' THEN 'critical' ELSE severity END
        WHERE id = %s""", (ev["event_end"] is not None, ev["severity"], incident_id))
    conn.execute("UPDATE integrity_event SET incident_id = %s WHERE id = %s", (incident_id, ev["id"]))
    record(conn, incident_id, "reopened", {"reason": "recurrence within 60 minutes of resolution",
                                           "integrity_event_id": ev["id"], "event_start": ev["event_start"]})
    return Outcome(incident_id, True, f"incident {row[1]} reopened by recurrence")


def _overlapping_incident(conn: psycopg.Connection, device_id: str, start: datetime,
                          end: datetime | None) -> str | None:
    row = conn.execute("""
        SELECT i.id::text FROM incident i JOIN integrity_event e ON e.incident_id = i.id AND e.type = i.type
        WHERE i.device_id = %s AND i.type IN ('heat_excursion', 'freeze')
          AND e.event_start <= coalesce(%s, 'infinity'::timestamptz) + %s
          AND coalesce(e.event_end, 'infinity') >= %s - %s
        ORDER BY e.event_start LIMIT 1""",
        (device_id, end, ALARM_LINK_WITHIN, start, ALARM_LINK_WITHIN)).fetchone()
    return row[0] if row else None


def _link_alarm(conn: psycopg.Connection, ev: dict) -> Outcome | None:
    """A local alarm is recorded on the excursion it belongs to (Table 3.17), not raised on its own. If
    the excursion has not been detected yet, it is attached when it is."""
    incident_id = _overlapping_incident(conn, ev["device_id"], ev["event_start"], ev["event_end"])
    if incident_id is None:
        return None
    conn.execute("UPDATE integrity_event SET incident_id = %s WHERE id = %s", (incident_id, ev["id"]))
    record(conn, incident_id, "linked", {"integrity_event_id": ev["id"], "type": "local_alarm",
                                         "event_start": ev["event_start"]})
    return Outcome(incident_id, False, "local alarm recorded on incident")


def _attach_alarms(conn: psycopg.Connection, incident_id: str, ev: dict) -> None:
    rows = conn.execute("""
        UPDATE integrity_event SET incident_id = %s
        WHERE device_id = %s AND type = 'local_alarm' AND incident_id IS NULL
          AND event_start <= coalesce(%s, 'infinity'::timestamptz) + %s
          AND coalesce(event_end, 'infinity') >= %s - %s
        RETURNING id::text, event_start""",
        (incident_id, ev["device_id"], ev["event_end"], ALARM_LINK_WITHIN, ev["event_start"],
         ALARM_LINK_WITHIN)).fetchall()
    for alarm_id, start in rows:
        record(conn, incident_id, "linked", {"integrity_event_id": alarm_id, "type": "local_alarm",
                                             "event_start": start})


# Escalation -----------------------------------------------------------------------------------------

INCIDENT = """
    SELECT id::text, incident_no, type, severity, mode, state, owner_id::text, jurisdiction_id,
           cc_ids::text[] AS cc_ids, current_tier, acknowledged_at, resolved_at, resolved_by::text,
           ack_due_from, ack_stage, resolve_due_from, resolve_stage, top_tier_at, overdue, last_reminder_at,
           notified_at, condition_cleared
    FROM incident WHERE id = %s
"""


def load_policy(conn: psycopg.Connection) -> Policy:
    return Policy(conn.execute(
        "SELECT severity, mode, kind, stage, tier, delay_min FROM escalation_policy").fetchall())


def clock(inc: dict) -> Clock:
    return Clock(inc["severity"], inc["mode"], inc["state"], inc["current_tier"],
                 inc["acknowledged_at"] is not None, inc["ack_due_from"], inc["ack_stage"],
                 inc["resolve_due_from"], inc["resolve_stage"], inc["top_tier_at"], inc["overdue"],
                 inc["last_reminder_at"])


def _get(conn: psycopg.Connection, incident_id: str, lock: bool = True) -> dict:
    inc = conn.cursor(row_factory=dict_row).execute(INCIDENT + (" FOR UPDATE" if lock else ""),
                                                    (incident_id,)).fetchone()
    if inc is None:
        raise NotFound(f"no incident {incident_id}")
    return inc


def _notify(conn: psycopg.Connection, inc: dict, kind: str, tier: str, recipients: list[str], due_at: datetime,
            now: datetime, reason: str | None = None, how: str | None = None) -> None:
    """Ask the notification service to tell people about the incident (by the incident's urgency unless
    `how` says otherwise)."""
    outbox.enqueue(conn, kafka.NOTIFICATIONS, inc["id"], {
        "id": str(uuid.uuid4()), "kind": kind, "incident_id": inc["id"], "incident_no": inc["incident_no"],
        "type": inc["type"], "severity": inc["severity"], "mode": inc["mode"], "tier": tier,
        "recipients": recipients, "cc": inc["cc_ids"] if kind == "alert" else [], "owner_id": inc["owner_id"],
        "urgency": how or urgency(inc["severity"], inc["mode"]), "reason": reason, "due_at": due_at,
        "requested_at": now})


CLOCK_COLUMNS = ("state", "current_tier", "ack_stage", "resolve_stage", "top_tier_at", "overdue",
                 "last_reminder_at")


def _apply(conn: psycopg.Connection, inc: dict, step: Step, policy: Policy, now: datetime,
           reason: str | None = None) -> None:
    """Apply one step: update the incident's clock, record the step in the audit trail and queue the
    notification it requires."""
    before = clock(inc)
    after = advance(before, step, now, policy)
    sets = {k: getattr(after, k) for k in CLOCK_COLUMNS if getattr(after, k) != getattr(before, k)}
    details = {"tier": step.tier, "stage": step.stage, "clock": step.clock, "due_at": step.at, "applied_at": now}
    if reason:
        details["reason"] = reason
    if step.kind == "notify":
        sets["notified_at"] = inc["notified_at"] or now
        recipients = [inc["owner_id"]]
        record(conn, inc["id"], "notified", {**details, "recipients": recipients, "cc": inc["cc_ids"]})
        _notify(conn, inc, "alert", "owner", recipients, step.at, now)
    elif step.kind in ("escalate", "overdue", "reminder"):
        recipients = ownership.recipients(conn, step.tier, inc["owner_id"], inc["jurisdiction_id"])
        record(conn, inc["id"], "escalated" if step.kind == "escalate" else step.kind,
               {**details, "recipients": recipients})
        _notify(conn, inc, "escalation" if step.kind == "escalate" else step.kind, step.tier, recipients, step.at,
                now, reason)
    # "skip": the tier was already reached; only the stage counter advances
    if sets:
        conn.execute("UPDATE incident SET " + ", ".join(f"{k} = %s" for k in sets) + " WHERE id = %s",
                      [*sets.values(), inc["id"]])
    inc.update(sets)


def apply_due(conn: psycopg.Connection, incident_id: str, now: datetime,
              policy: Policy | None = None) -> tuple[datetime | None, bool]:
    """Apply every policy step due by `now`, in order. Returns when the next step falls due (None if
    nothing is pending) and whether the incident is finished (closed)."""
    policy = policy or load_policy(conn)
    inc = _get(conn, incident_id)
    if inc["state"] == "closed":
        return None, True
    while True:
        step = next_step(clock(inc), policy)
        if step is None or step.at > now:
            return (step.at if step else None), False
        _apply(conn, inc, step, policy, now)


def flag_undelivered(conn: psycopg.Connection, incident_id: str, recipient_id: str, kind: str) -> None:
    """Table 3.20: the incident is flagged 'notification failed' on the dashboard."""
    conn.execute("UPDATE incident SET notification_failed = true WHERE id = %s", (incident_id,))
    record(conn, incident_id, "notification_failed", {"recipient": recipient_id, "kind": kind})


def escalate_undelivered(conn: psycopg.Connection, incident_id: str, failed_tier: str, recipient_id: str,
                         now: datetime, policy: Policy | None = None) -> Outcome:
    """An SMS about the incident could not be delivered (Table 3.20): flag the incident and, if it still
    needs acknowledging, escalate at once to the tier above the one the message was for, unless that tier
    has already been reached. Later policy stages for tiers already reached are skipped as usual."""
    inc = _get(conn, incident_id)
    flag_undelivered(conn, incident_id, recipient_id, "sms")
    target = above(failed_tier)
    if (inc["state"] in ("resolved", "closed") or inc["acknowledged_at"] is not None or target is None
            or rank(target) <= rank(inc["current_tier"])):
        return Outcome(incident_id, False, f"incident {inc['incident_no']} flagged: notification failed")
    policy = policy or load_policy(conn)
    if rank(target) > rank(policy.top_tier(inc["severity"], inc["mode"])):
        return Outcome(incident_id, False, f"incident {inc['incident_no']} flagged: notification failed")
    _apply(conn, inc, Step(now, "escalate", target, -1, "delivery"), policy, now, reason="undelivered")
    return Outcome(incident_id, True, f"incident {inc['incident_no']} escalated to {target}: notification failed")


# Actions by people ------------------------------------------------------------------------------------

def _user(conn: psycopg.Connection, user_id: str) -> dict:
    u = conn.cursor(row_factory=dict_row).execute(
        "SELECT id::text, role, jurisdiction_id, active FROM app_user WHERE id = %s", (user_id,)).fetchone()
    if u is None or not u["active"]:
        raise NotPermitted("unknown or deactivated user")
    return u


def _covers(conn: psycopg.Connection, outer: str, inner: str) -> bool:
    """Whether jurisdiction `outer` is `inner` or one of its ancestors."""
    return conn.execute("""
        WITH RECURSIVE up AS (SELECT id, parent_id FROM jurisdiction WHERE id = %s
                              UNION ALL SELECT j.id, j.parent_id FROM jurisdiction j JOIN up ON j.id = up.parent_id)
        SELECT EXISTS (SELECT 1 FROM up WHERE id = %s)""", (inner, outer)).fetchone()[0]


def entitled(conn: psycopg.Connection, inc: dict, user_id: str) -> bool:
    """Who may acknowledge, record action on or resolve an incident (Table 3.15): its owner, or an officer
    of a tier it has been escalated to; for warnings and retrospective incidents, also the LGA officer,
    who is notified from the start."""
    if user_id == inc["owner_id"]:
        return True
    reached = list(TIERS[1:rank(inc["current_tier"]) + 1])
    if (inc["severity"] == "warning" or inc["mode"] == "retrospective") and "lga" not in reached:
        reached.append("lga")
    return any(user_id in ownership.officers(conn, inc["jurisdiction_id"], t) for t in reached)


def can_view(conn: psycopg.Connection, incident_id: str, user_id: str) -> bool:
    """Owner, copied users, anyone entitled to act, administrators, and officers whose jurisdiction covers
    the incident. (Role-based views in full come with the dashboard, I6.)"""
    inc = _get(conn, incident_id, lock=False)
    user = conn.execute("SELECT role, jurisdiction_id FROM app_user WHERE id = %s AND active", (user_id,)).fetchone()
    if user is None:
        return False
    role, jurisdiction = user
    return (user_id in inc["cc_ids"] or role == "admin" or entitled(conn, inc, user_id)
            or (role in REVIEWER_ROLES and _covers(conn, jurisdiction, inc["jurisdiction_id"])))


def _reviewer(conn: psycopg.Connection, inc: dict, user: dict) -> bool:
    """Who may close or reopen (Table 3.15): an officer at LGA level or above whose jurisdiction covers the
    incident and who did not resolve it. Warnings may also be closed by their owner."""
    if inc["severity"] == "warning" and user["id"] == inc["owner_id"]:
        return True
    if user["role"] not in REVIEWER_ROLES or not _covers(conn, user["jurisdiction_id"], inc["jurisdiction_id"]):
        return False
    return inc["severity"] == "warning" or user["id"] != inc["resolved_by"]


def _open_for_action(conn: psycopg.Connection, incident_id: str, user_id: str) -> dict:
    _user(conn, user_id)
    inc = _get(conn, incident_id)
    if inc["state"] == "closed":
        raise InvalidState(f"incident {inc['incident_no']} is closed")
    if not entitled(conn, inc, user_id):
        raise NotPermitted(f"not entitled to act on incident {inc['incident_no']}")
    return inc


def acknowledge(conn: psycopg.Connection, incident_id: str, user_id: str, channel: str = "dashboard") -> Outcome:
    """Acknowledge. A late acknowledgement, after escalation, is accepted and stops further acknowledgement
    escalation, but the escalation already made stands (Section 3.4.2). A repeated one has no effect."""
    inc = _open_for_action(conn, incident_id, user_id)
    if inc["acknowledged_at"] is not None or inc["state"] == "resolved":
        return Outcome(incident_id, False, f"incident {inc['incident_no']} already acknowledged")
    conn.execute("""
        UPDATE incident SET acknowledged_at = now(), acknowledged_by = %s, resolve_due_from = now(),
               resolve_stage = 0, state = 'acknowledged'
        WHERE id = %s""", (user_id, incident_id))
    late = inc["current_tier"] != "owner"
    record(conn, incident_id, "acknowledged", {"tier_at_acknowledgement": inc["current_tier"], "late": late},
           user_id, channel)
    if late:   # Table 3.17: the escalation stands, and the officers it reached are informed
        informed = [u for t in TIERS[1:rank(inc["current_tier"]) + 1]
                    for u in ownership.officers(conn, inc["jurisdiction_id"], t) if u != user_id]
        if informed:
            _notify(conn, inc, "acknowledged", inc["current_tier"], informed, datetime.now(UTC), datetime.now(UTC),
                    how="dashboard")
    return Outcome(incident_id, True, f"incident {inc['incident_no']} acknowledged")


def record_action(conn: psycopg.Connection, incident_id: str, user_id: str, note: str,
                  channel: str = "dashboard") -> Outcome:
    inc = _open_for_action(conn, incident_id, user_id)
    record(conn, incident_id, "action_recorded", {"note": note}, user_id, channel)
    return Outcome(incident_id, False, f"action recorded on incident {inc['incident_no']}")


def resolve(conn: psycopg.Connection, incident_id: str, user_id: str, note: str,
            channel: str = "dashboard") -> Outcome:
    """Report corrective action. Resolving an unacknowledged incident acknowledges it first."""
    inc = _open_for_action(conn, incident_id, user_id)
    if inc["state"] == "resolved":
        return Outcome(incident_id, False, f"incident {inc['incident_no']} already resolved")
    if not note.strip():
        raise InvalidState("a resolution must describe the action taken")
    if inc["acknowledged_at"] is None:
        acknowledge(conn, incident_id, user_id, channel)
    conn.execute("UPDATE incident SET state = 'resolved', resolved_at = now(), resolved_by = %s WHERE id = %s",
                 (user_id, incident_id))
    record(conn, incident_id, "resolved", {"note": note, "condition_cleared": inc["condition_cleared"]},
           user_id, channel)
    codes.void(conn, incident_id=incident_id)
    # the officers it reached, or the LGA officers, are asked to review it on the dashboard
    tier = inc["current_tier"] if inc["current_tier"] != "owner" else "lga"
    reviewers = [u for u in ownership.officers(conn, inc["jurisdiction_id"], tier) if u != user_id]
    if reviewers:
        now = conn.execute("SELECT now()").fetchone()[0]
        _notify(conn, inc, "review", tier, reviewers, now, now, how="dashboard")
    return Outcome(incident_id, True, f"incident {inc['incident_no']} resolved")


def _resolved_for_review(conn: psycopg.Connection, incident_id: str, user_id: str) -> dict:
    user = _user(conn, user_id)
    inc = _get(conn, incident_id)
    if inc["state"] != "resolved":
        raise InvalidState(f"incident {inc['incident_no']} is {inc['state']}; only a resolved incident can be reviewed")
    if not _reviewer(conn, inc, user):
        raise NotPermitted("closure and reopening need a reviewing officer at LGA level or above "
                           "who did not resolve the incident")
    return inc


def close(conn: psycopg.Connection, incident_id: str, user_id: str, note: str = "") -> Outcome:
    """Close after review; on the dashboard only (Table 3.19 does not allow closure by SMS)."""
    inc = _resolved_for_review(conn, incident_id, user_id)
    conn.execute("UPDATE incident SET state = 'closed', closed_at = now(), closed_by = %s WHERE id = %s",
                 (user_id, incident_id))
    record(conn, incident_id, "closed", {"note": note}, user_id, "dashboard")
    codes.void(conn, incident_id=incident_id)
    return Outcome(incident_id, True, f"incident {inc['incident_no']} closed")


def reopen(conn: psycopg.Connection, incident_id: str, user_id: str, reason: str) -> Outcome:
    """The reviewer judges the action inadequate: the owner remains responsible and the resolution
    time limits start again."""
    inc = _resolved_for_review(conn, incident_id, user_id)
    if not reason.strip():
        raise InvalidState("reopening needs a reason")
    conn.execute("""
        UPDATE incident SET state = 'acknowledged', resolved_at = NULL, resolved_by = NULL,
               reopened_at = now(), reopen_count = reopen_count + 1, resolve_due_from = now(), resolve_stage = 0
        WHERE id = %s""", (incident_id,))
    record(conn, incident_id, "reopened", {"reason": "review", "note": reason}, user_id, "dashboard")
    now = datetime.now(UTC)   # the owner needs a new response code to resolve by SMS
    _notify(conn, inc, "reopened", "owner", [inc["owner_id"]], now, now)
    return Outcome(incident_id, True, f"incident {inc['incident_no']} reopened")
