"""Read models for the dashboard (FR10, Section 3.4.7): what each role sees, filtered to its jurisdiction
as Table 3.21 sets out.

- Officers at LGA, state and national level see everything in their jurisdiction and below.
- A facility officer-in-charge sees their facility, and incidents they own or are copied on.
- Outreach team leads and drivers see the consignments they have held or been offered, and incidents
  they own or that concern those consignments.
- Administrators see no incidents or units (they manage the system, not the cold chain).

A unit is stale when its last reading is older than three reporting intervals (Section 3.4.7).
"""
from dataclasses import dataclass
from datetime import datetime, timedelta

import psycopg
from psycopg.rows import dict_row

from coldchain import custody
from coldchain.errors import NotFound, NotPermitted
from coldchain.incidents import store

OFFICERS = store.REVIEWER_ROLES                 # lga_officer, state_officer, national_officer
CARRIERS = ("outreach_lead", "driver")
STALE_INTERVALS = 3
OPEN_STATES = ("open", "notified", "acknowledged", "escalated")
INCIDENT_STATES = {"active": OPEN_STATES, "review": ("resolved",), "closed": ("closed",),
                   "all": OPEN_STATES + ("resolved", "closed")}
GROUPS = ("tier", "facility", "lga", "state")

AREA = """WITH RECURSIVE area AS (SELECT id FROM jurisdiction WHERE id = %(jurisdiction)s
                                 UNION ALL SELECT j.id FROM jurisdiction j JOIN area a ON j.parent_id = a.id)"""
HANDLED = """SELECT consignment_id FROM custody_interval WHERE holder_id = %(user)s
             UNION SELECT consignment_id FROM custody_handoff WHERE to_user = %(user)s OR from_user = %(user)s"""


@dataclass(frozen=True)
class Viewer:
    id: str
    username: str
    full_name: str
    role: str
    jurisdiction_id: str
    facility_id: str | None

    @property
    def params(self) -> dict:
        return {"user": self.id, "jurisdiction": self.jurisdiction_id, "facility": self.facility_id}


def viewer(conn: psycopg.Connection, user_id: str) -> Viewer:
    row = conn.execute("""SELECT id::text, username, full_name, role, jurisdiction_id, facility_id::text
                          FROM app_user WHERE id = %s AND active""", (user_id,)).fetchone()
    if row is None:
        raise NotPermitted("not an active user")
    return Viewer(*row)


def me(conn: psycopg.Connection, v: Viewer) -> dict:
    cur = conn.cursor(row_factory=dict_row)
    j = cur.execute("SELECT id, name, level FROM jurisdiction WHERE id = %s", (v.jurisdiction_id,)).fetchone()
    f = cur.execute("SELECT id::text, name FROM facility WHERE id = %s", (v.facility_id,)).fetchone() \
        if v.facility_id else None
    unread = conn.execute("""SELECT count(*) FROM notification WHERE recipient_id = %s AND channel = 'dashboard'
                             AND read_at IS NULL""", (v.id,)).fetchone()[0]
    return {"id": v.id, "username": v.username, "full_name": v.full_name, "role": v.role, "jurisdiction": j,
            "facility": f, "unread": unread}


# Incidents -------------------------------------------------------------------------------------------

def _incident_scope(v: Viewer) -> str:
    """A condition on incident i (with device d) for the incidents the viewer may see."""
    if v.role in OFFICERS:
        return "i.jurisdiction_id IN (SELECT id FROM area)"
    mine = "(i.owner_id = %(user)s OR %(user)s = ANY(i.cc_ids))"
    if v.role == "facility_oic":
        return f"(d.facility_id = %(facility)s OR {mine})"
    if v.role in CARRIERS:
        return f"({mine} OR i.consignment_id IN ({HANDLED}))"
    return "false"


INCIDENT_COLUMNS = """
    i.id::text, i.incident_no, i.type, i.severity, i.mode, i.state, i.current_tier, i.overdue,
    i.notification_failed, i.condition_cleared, i.reopen_count, i.ownership, i.created_at, i.received_at,
    i.detected_at, i.notified_at, i.acknowledged_at, i.resolved_at, i.closed_at, i.jurisdiction_id,
    e.event_start, ep.event_end, coalesce(ep.peak_value, e.peak_value) AS peak_value, e.borderline,
    ep.episodes, e.details->>'explanation' AS explanation, d.serial_no AS device_serial, d.type AS device_type, d.equipment_type,
    f.id::text AS facility_id, f.name AS facility_name, f.ward AS facility_ward, j.name AS jurisdiction_name,
    i.consignment_id::text, c.consignment_no, c.destination, c.quantity, pr.name AS product_name, i.owner_id::text, o.full_name AS owner_name,
    ab.full_name AS acknowledged_by_name, rb.full_name AS resolved_by_name, cb.full_name AS closed_by_name
"""
INCIDENT_FROM = """
    FROM incident i
    LEFT JOIN integrity_event e ON e.id = i.integrity_event_id
    LEFT JOIN LATERAL (   -- an incident may hold several episodes of its condition (new episodes join it)
        SELECT CASE WHEN bool_or(x.event_end IS NULL) THEN NULL ELSE max(x.event_end) END AS event_end,
               CASE WHEN i.type = 'freeze' THEN min(x.peak_value) ELSE max(x.peak_value) END AS peak_value,
               count(*) AS episodes
        FROM integrity_event x WHERE x.incident_id = i.id AND x.type = i.type) ep ON true
    LEFT JOIN device d ON d.id = i.device_id
    LEFT JOIN facility f ON f.id = d.facility_id
    LEFT JOIN jurisdiction j ON j.id = i.jurisdiction_id
    LEFT JOIN consignment c ON c.id = i.consignment_id
    LEFT JOIN product pr ON pr.id = c.product_id
    LEFT JOIN app_user o ON o.id = i.owner_id
    LEFT JOIN app_user ab ON ab.id = i.acknowledged_by
    LEFT JOIN app_user rb ON rb.id = i.resolved_by
    LEFT JOIN app_user cb ON cb.id = i.closed_by
"""


def incidents(conn: psycopg.Connection, v: Viewer, state: str = "active", severity: str | None = None,
              facility_id: str | None = None, limit: int = 200, area: str | None = None,
              owner: str | None = None) -> list[dict]:
    """Active incidents by severity, then oldest first (the ones waiting longest); others newest first.
    Optionally only one facility, one area (an LGA, or a state with its LGAs) or one owner, as opened
    from the performance page; these narrow the viewer's scope, never widen it."""
    order = ("(i.severity = 'critical') DESC, i.created_at" if state == "active" else "i.created_at DESC")
    params = {**v.params, "states": list(INCIDENT_STATES[state]), "severity": severity, "fac": facility_id,
              "limit": limit, "area_id": area, "owner": owner}
    return conn.cursor(row_factory=dict_row).execute(f"""
        {AREA}
        SELECT {INCIDENT_COLUMNS} {INCIDENT_FROM}
        WHERE {_incident_scope(v)} AND i.state = ANY(%(states)s)
          AND (%(severity)s::text IS NULL OR i.severity = %(severity)s)
          AND (%(fac)s::uuid IS NULL OR d.facility_id = %(fac)s::uuid)
          AND (%(area_id)s::text IS NULL OR i.jurisdiction_id = %(area_id)s
               OR i.jurisdiction_id IN (SELECT id FROM jurisdiction WHERE parent_id = %(area_id)s))
          AND (%(owner)s::uuid IS NULL OR i.owner_id = %(owner)s::uuid)
        ORDER BY {order} LIMIT %(limit)s""", params).fetchall()


def incident(conn: psycopg.Connection, v: Viewer, incident_id: str) -> dict:
    """One incident with its timeline (audit trail and notifications) and what the viewer may do."""
    if not store.can_view(conn, incident_id, v.id):
        raise NotPermitted("not permitted to view this incident")
    cur = conn.cursor(row_factory=dict_row)
    inc = cur.execute(f"SELECT {INCIDENT_COLUMNS} {INCIDENT_FROM} WHERE i.id = %s", (incident_id,)).fetchone()
    inc["actions"] = cur.execute("""
        SELECT a.id, a.action, a.channel, a.occurred_at, a.details, u.full_name AS actor_name
        FROM incident_action a LEFT JOIN app_user u ON u.id = a.actor_id
        WHERE a.incident_id = %s ORDER BY a.id""", (incident_id,)).fetchall()
    inc["notifications"] = cur.execute("""
        SELECT n.id::text, n.kind, n.channel, n.status, n.tier, n.reason, n.attempts, n.body, n.created_at,
               n.sent_at, n.delivered_at, n.failure_reason, u.full_name AS recipient_name
        FROM notification n JOIN app_user u ON u.id = n.recipient_id
        WHERE n.incident_id = %s ORDER BY n.created_at, n.channel""", (incident_id,)).fetchall()
    raw = store._get(conn, incident_id, lock=False)
    active = inc["state"] in OPEN_STATES
    inc["can_act"] = active and store.entitled(conn, raw, v.id)
    inc["can_review"] = inc["state"] == "resolved" and store.can_review(conn, incident_id, v.id)
    inc["location"] = location(conn, inc)
    return inc


def location(conn: psycopg.Connection, inc: dict, track_points: int = 300) -> dict | None:
    """Where an incident is, for the map in its details (E9, P12): the facility; or, for a consignment, its
    logger's last known position and its route since the logger was assigned. None if unknown."""
    cur = conn.cursor(row_factory=dict_row)
    if inc["facility_id"]:
        return cur.execute("""SELECT 'facility' AS kind, tier, ST_Y(location::geometry) AS lat,
                                     ST_X(location::geometry) AS lon, '[]'::json AS track
                              FROM facility WHERE id = %s""", (inc["facility_id"],)).fetchone()
    if not inc["consignment_id"]:
        return None
    a = cur.execute("""SELECT device_id, valid_from, valid_to FROM device_assignment WHERE consignment_id = %s
                       ORDER BY valid_from DESC LIMIT 1""", (inc["consignment_id"],)).fetchone()
    if a is None:
        return None
    track = conn.execute("""
        SELECT ST_X(location::geometry), ST_Y(location::geometry), event_time FROM (
            SELECT location, event_time FROM reading WHERE device_id = %s AND location IS NOT NULL
              AND event_time >= %s AND (%s::timestamptz IS NULL OR event_time <= %s)
            ORDER BY event_time DESC LIMIT %s) t ORDER BY event_time""",
        (a["device_id"], a["valid_from"], a["valid_to"], a["valid_to"], track_points)).fetchall()
    last = track[-1] if track else conn.execute(   # as on the map: the last known position, even if older
        """SELECT ST_X(location::geometry), ST_Y(location::geometry), event_time FROM reading
           WHERE device_id = %s AND location IS NOT NULL ORDER BY event_time DESC LIMIT 1""",
        (a["device_id"],)).fetchone()
    if last is None:
        return None
    lon, lat, at = last
    return {"kind": "mobile", "lat": lat, "lon": lon, "position_time": at,
            "track": [[x, y] for x, y, _ in track]}


def limits(conn: psycopg.Connection, consignment_id: str | None) -> dict:
    """The temperature limits detection applied: the consignment's product, or for a store the strictest
    of all products (as detection does)."""
    cur = conn.cursor(row_factory=dict_row)
    if consignment_id:
        return cur.execute("""SELECT p.lower_limit, p.upper_limit, p.freeze_limit FROM consignment c
                              JOIN product p ON p.id = c.product_id WHERE c.id = %s""", (consignment_id,)).fetchone()
    return cur.execute("""SELECT max(lower_limit) AS lower_limit, min(upper_limit) AS upper_limit,
                                 max(freeze_limit) AS freeze_limit FROM product""").fetchone()


def incident_readings(conn: psycopg.Connection, v: Viewer, incident_id: str, before_min: int = 60,
                      after_min: int = 30, points: int = 1000) -> dict:
    """The readings behind an incident, with some context either side, averaged into at most `points`
    buckets."""
    inc = incident(conn, v, incident_id)
    if inc["device_serial"] is None or inc["event_start"] is None:
        return {"limits": None, "readings": []}
    start = inc["event_start"] - timedelta(minutes=before_min)
    end = (inc["event_end"] or datetime.now(inc["event_start"].tzinfo)) + timedelta(minutes=after_min)
    bucket = max(60, int((end - start).total_seconds() / points))
    rows = conn.cursor(row_factory=dict_row).execute("""
        SELECT time_bucket(make_interval(secs => %s), r.event_time) AS t, round(avg(r.temperature), 2) AS temp,
               bool_and(r.power_ok) AS power_ok, bool_or(r.door_open) AS door_open, max(r.received_at) AS received_at
        FROM reading r JOIN device d ON d.id = r.device_id
        WHERE d.serial_no = %s AND r.event_time BETWEEN %s AND %s
        GROUP BY 1 ORDER BY 1""", (bucket, inc["device_serial"], start, end)).fetchall()
    return {"limits": limits(conn, inc["consignment_id"]), "bucket_s": bucket, "readings": rows}


# Map ------------------------------------------------------------------------------------------------

def _facility_scope(v: Viewer) -> str:
    if v.role in OFFICERS:
        return "f.jurisdiction_id IN (SELECT id FROM area)"
    if v.role == "facility_oic":
        return "f.id = %(facility)s"
    return "false"


def _consignment_scope(v: Viewer) -> str:
    """A condition on consignment c (with origin facility f)."""
    if v.role in OFFICERS:
        return "f.jurisdiction_id IN (SELECT id FROM area)"
    if v.role == "facility_oic":
        return f"(c.origin_facility_id = %(facility)s OR c.id IN ({HANDLED}))"
    if v.role in CARRIERS:
        return f"c.id IN ({HANDLED})"
    return "false"


def _status(severity: str | None, last_received: datetime | None, stale: bool) -> str:
    """One status per unit, worst first. Shown with a symbol and a label as well as a colour."""
    if severity:
        return severity                      # critical | warning (an active incident)
    if last_received is None:
        return "no_data"
    return "stale" if stale else "ok"


def map_units(conn: psycopg.Connection, v: Viewer, track_points: int = 300) -> dict:
    """Facilities with their fixed units, and mobile units on active consignments with their tracks."""
    cur = conn.cursor(row_factory=dict_row)
    facilities = cur.execute(f"""
        {AREA}
        SELECT f.id::text, f.name, f.tier, f.power_source, f.ward, f.jurisdiction_id, j.name AS jurisdiction_name,
               CASE WHEN j.level = 'lga' THEN j.name END AS lga_name,
               CASE WHEN j.level = 'lga' THEN p.name WHEN j.level = 'state' THEN j.name END AS state_name,
               ST_Y(f.location::geometry) AS lat, ST_X(f.location::geometry) AS lon
        FROM facility f JOIN jurisdiction j ON j.id = f.jurisdiction_id
        LEFT JOIN jurisdiction p ON p.id = j.parent_id
        WHERE {_facility_scope(v)} ORDER BY f.name""", v.params).fetchall()
    units = cur.execute(f"""
        {AREA}
        SELECT d.id::text, d.serial_no, d.equipment_type, d.facility_id::text, d.reporting_interval_s,
               s.last_event_time, s.last_received_at, r.temperature, r.power_ok, r.door_open,
               s.last_received_at < now() - make_interval(secs => {STALE_INTERVALS} * d.reporting_interval_s)
                   AS stale,
               (SELECT CASE WHEN bool_or(i.severity = 'critical') THEN 'critical' ELSE max(i.severity) END
                FROM incident i WHERE i.device_id = d.id AND i.state = ANY(%(open)s)) AS severity,
               (SELECT count(*) FROM incident i WHERE i.device_id = d.id AND i.state = ANY(%(open)s))
                   AS open_incidents
        FROM device d JOIN facility f ON f.id = d.facility_id
        LEFT JOIN device_status s ON s.device_id = d.id
        LEFT JOIN LATERAL (SELECT temperature, power_ok, door_open FROM reading
                           WHERE device_id = d.id ORDER BY event_time DESC LIMIT 1) r ON true
        WHERE d.type = 'fixed' AND d.status = 'active' AND {_facility_scope(v)}
        ORDER BY d.serial_no""", {**v.params, "open": list(OPEN_STATES)}).fetchall()
    for u in units:
        u["status"] = _status(u["severity"], u["last_received_at"], bool(u["stale"]))
    mobile = cur.execute(f"""
        {AREA}
        SELECT d.id::text, d.serial_no, d.equipment_type, d.reporting_interval_s, d.sync_window_min,
               c.id::text AS consignment_id, c.consignment_no, c.destination, p.name AS product_name,
               s.last_event_time, s.last_received_at, r.temperature, r.event_time AS position_time,
               ST_Y(r.location::geometry) AS lat, ST_X(r.location::geometry) AS lon, da.valid_from AS assigned_at,
               s.last_received_at < now() - make_interval(secs => {STALE_INTERVALS} * d.reporting_interval_s)
                   AS stale,
               (SELECT CASE WHEN bool_or(i.severity = 'critical') THEN 'critical' ELSE max(i.severity) END
                FROM incident i WHERE i.consignment_id = c.id AND i.state = ANY(%(open)s)) AS severity
        FROM device_assignment da
        JOIN device d ON d.id = da.device_id
        JOIN consignment c ON c.id = da.consignment_id
        JOIN facility f ON f.id = c.origin_facility_id
        JOIN product p ON p.id = c.product_id
        LEFT JOIN device_status s ON s.device_id = d.id
        LEFT JOIN LATERAL (SELECT temperature, event_time, location FROM reading
                           WHERE device_id = d.id AND location IS NOT NULL
                           ORDER BY event_time DESC LIMIT 1) r ON true
        WHERE da.valid_to IS NULL AND {_consignment_scope(v)}
        ORDER BY c.consignment_no""", {**v.params, "open": list(OPEN_STATES)}).fetchall()
    for m in mobile:
        m["status"] = _status(m["severity"], m["last_received_at"], bool(m["stale"]))
        holder = custody.holder(conn, m["consignment_id"])
        m["holder_name"] = conn.execute("SELECT full_name FROM app_user WHERE id = %s",
                                        (holder,)).fetchone()[0] if holder else None
        m["track"] = [[lon, lat] for lon, lat in conn.execute("""
            SELECT ST_X(location::geometry), ST_Y(location::geometry) FROM (
                SELECT location, event_time FROM reading WHERE device_id = %s AND location IS NOT NULL
                  AND event_time >= %s ORDER BY event_time DESC LIMIT %s) t ORDER BY event_time""",
            (m["id"], m["assigned_at"], track_points)).fetchall()]
    by_facility: dict[str, list] = {}
    for u in units:
        by_facility.setdefault(u["facility_id"], []).append(u)
    rank = ["critical", "warning", "stale", "ok", "no_data"]
    for f in facilities:
        f["units"] = by_facility.get(f["id"], [])
        f["status"] = min((u["status"] for u in f["units"]), key=rank.index, default="no_data")
    return {"facilities": facilities, "mobile": mobile, "summary": summary(conn, v)}


def summary(conn: psycopg.Connection, v: Viewer, days: int = 7) -> dict:
    """The incident situation in the viewer's area, for the summary cards above the map: what is active
    now (by severity, and whether anyone has responded), and how many of the incidents raised in the
    last `days` days were responded to (acknowledged, or resolved) and resolved."""
    responded = "(i.acknowledged_at IS NOT NULL OR i.resolved_at IS NOT NULL)"
    recent = "i.created_at >= now() - make_interval(days => %(days)s)"
    return conn.cursor(row_factory=dict_row).execute(f"""
        {AREA}
        SELECT count(*) FILTER (WHERE i.state = ANY(%(open)s)) AS active,
               count(*) FILTER (WHERE i.state = ANY(%(open)s) AND i.severity = 'critical') AS critical,
               count(*) FILTER (WHERE i.state = ANY(%(open)s) AND i.severity = 'warning') AS warning,
               count(*) FILTER (WHERE i.state = ANY(%(open)s) AND NOT {responded}) AS awaiting,
               count(*) FILTER (WHERE {recent}) AS raised,
               count(*) FILTER (WHERE {recent} AND {responded}) AS responded,
               count(*) FILTER (WHERE {recent} AND i.resolved_at IS NOT NULL) AS resolved,
               %(days)s AS days
        FROM incident i LEFT JOIN device d ON d.id = i.device_id
        WHERE {_incident_scope(v)}""", {**v.params, "open": list(OPEN_STATES), "days": days}).fetchone()


# Performance -----------------------------------------------------------------------------------------

GROUP_KEYS = {
    # tier: the tier the incident had reached when it was acknowledged (or has reached, if not yet)
    "tier": ("coalesce(ack.details->>'tier_at_acknowledgement', i.current_tier)",
             "coalesce(ack.details->>'tier_at_acknowledgement', i.current_tier)"),
    "facility": ("coalesce(f.id::text, 'transit')", "coalesce(f.name, 'In transit (shipments)')"),
    "lga": ("j.id", ("CASE j.level WHEN 'lga' THEN j.name WHEN 'state' THEN j.name || ' State (state-level stores)'"
            " ELSE j.name || ' (national stores)' END")),
    "state": ("CASE WHEN j.level = 'lga' THEN j.parent_id ELSE j.id END",
              "CASE WHEN j.level = 'lga' THEN pj.name ELSE j.name END"),
    # officer: the incident's owner, the person responsible when it was created (E9, P16). Not whoever
    # acknowledged it: an officer who steps in after an escalation would otherwise carry the owner's delay.
    "officer": ("i.owner_id::text", "o.full_name"),
}
# Who may compare officers: supervisors only, so a facility officer is not shown colleagues' figures.
OFFICER_GROUP_ROLES = OFFICERS

PERF_METRICS = """
    count(*) AS incidents,
    count(*) FILTER (WHERE i.severity = 'critical') AS critical,
    count(i.acknowledged_at) AS acknowledged,
    round(avg(extract(epoch FROM i.acknowledged_at - i.created_at)) / 60, 1) AS mtta_min,
    count(i.resolved_at) AS resolved,
    round(avg(extract(epoch FROM i.resolved_at - i.created_at)) / 60, 1) AS mttr_min,
    count(*) FILTER (WHERE ack.details->>'tier_at_acknowledgement' = 'owner') AS before_escalation,
    count(*) FILTER (WHERE i.state = ANY(%(open)s)) AS open,
    count(*) FILTER (WHERE i.acknowledged_at IS NULL AND i.state = ANY(%(open)s)) AS unacknowledged,
    round(max(extract(epoch FROM now() - i.created_at))
          FILTER (WHERE i.acknowledged_at IS NULL AND i.state = ANY(%(open)s)) / 60, 1) AS longest_wait_min,
    count(*) FILTER (WHERE i.notification_failed) AS notification_failed
"""
PERF_FROM = """
    FROM incident i
    LEFT JOIN device d ON d.id = i.device_id
    LEFT JOIN facility f ON f.id = d.facility_id
    JOIN jurisdiction j ON j.id = i.jurisdiction_id
    LEFT JOIN jurisdiction pj ON pj.id = j.parent_id
    JOIN app_user o ON o.id = i.owner_id
    LEFT JOIN LATERAL (SELECT details FROM incident_action a WHERE a.incident_id = i.id
                       AND a.action = 'acknowledged' ORDER BY a.id LIMIT 1) ack ON true
"""


def _bucket(since: datetime, until: datetime) -> str:
    """Trend buckets: days for a week or two, weeks up to about four months, then months."""
    days = (until - since).days
    return "day" if days <= 14 else "week" if days <= 120 else "month"


def performance(conn: psycopg.Connection, v: Viewer, since: datetime, until: datetime, group: str,
                mode: str = "operational") -> dict:
    """Response metrics (FR10.3): mean time to acknowledge and to resolve, measured from incident creation
    as the escalation time limits are (Table 3.15), by tier, facility, LGA, state or responsible officer.
    The means cover only incidents that were acknowledged (resolved), so open unacknowledged incidents are
    counted separately, with the longest wait, or a group that ignores its incidents would look best.
    `before_escalation` counts incidents acknowledged while still with their owner. Also returns the
    figures for the whole area (`overall`) and over time (`trend`, by day, week or month), so the
    page can show whether response is getting better (E9, P15)."""
    if group == "officer" and v.role not in OFFICER_GROUP_ROLES:
        raise NotPermitted("only supervisors can compare officers")
    key, label = GROUP_KEYS[group]
    params = {**v.params, "since": since, "until": until, "mode": mode, "open": list(OPEN_STATES),
              "bucket": _bucket(since, until)}
    where = f"""WHERE {_incident_scope(v)} AND i.created_at >= %(since)s AND i.created_at < %(until)s
                  AND (%(mode)s = 'all' OR i.mode = %(mode)s)"""
    extra = (""", o.role AS role,
               array_agg(DISTINCT coalesce(f.name, 'Shipments in transit')) AS covers""" if group == "officer" else "")
    cur = conn.cursor(row_factory=dict_row)
    rows = cur.execute(f"""
        {AREA}
        SELECT {key} AS key, {label} AS label, {PERF_METRICS} {extra}
        {PERF_FROM} {where}
        GROUP BY 1, 2{", o.role" if group == "officer" else ""} ORDER BY mtta_min DESC NULLS LAST, label""",
        params).fetchall()
    overall = cur.execute(f"{AREA} SELECT {PERF_METRICS} {PERF_FROM} {where}", params).fetchone()
    sev_avg = lambda col, sev: (f"round(avg(extract(epoch FROM i.{col} - i.created_at)) "
                                f"FILTER (WHERE i.severity = '{sev}') / 60, 1)")
    trend = cur.execute(f"""
        {AREA}, b AS (
            SELECT date_trunc(%(bucket)s, i.created_at) AS start,
                   count(*) AS incidents,
                   count(*) FILTER (WHERE i.severity = 'critical') AS critical,
                   round(avg(extract(epoch FROM i.acknowledged_at - i.created_at)) / 60, 1) AS mtta_min,
                   round(avg(extract(epoch FROM i.resolved_at - i.created_at)) / 60, 1) AS mttr_min,
                   {sev_avg("acknowledged_at", "critical")} AS mtta_critical,
                   {sev_avg("resolved_at", "critical")} AS mttr_critical,
                   {sev_avg("acknowledged_at", "warning")} AS mtta_warning,
                   {sev_avg("resolved_at", "warning")} AS mttr_warning
            {PERF_FROM} {where} GROUP BY 1)
        SELECT s.start, coalesce(b.incidents, 0) AS incidents, coalesce(b.critical, 0) AS critical,
               b.mtta_min, b.mttr_min, b.mtta_critical, b.mttr_critical, b.mtta_warning, b.mttr_warning
        FROM generate_series(date_trunc(%(bucket)s, %(since)s::timestamptz),
                             date_trunc(%(bucket)s, %(until)s::timestamptz),
                             ('1 ' || %(bucket)s)::interval) AS s(start)
        LEFT JOIN b ON b.start = s.start ORDER BY s.start""", params).fetchall()
    # The same figures for the period before, so the page can say whether things got better or worse.
    previous = cur.execute(f"{AREA} SELECT {PERF_METRICS} {PERF_FROM} {where}",
                           {**params, "since": since - (until - since), "until": since}).fetchone()
    # The time allowed to acknowledge before an incident moves up from its owner (Table 3.15), by severity.
    limits = dict(conn.execute("""
        SELECT severity, delay_min FROM escalation_policy
        WHERE kind = 'acknowledge' AND stage = 1 AND mode = %s""",
        ("retrospective" if mode == "retrospective" else "operational",)).fetchall())
    return {"since": since, "until": until, "group": group, "mode": mode, "bucket": params["bucket"],
            "overall": overall, "previous": previous, "limits": limits, "trend": trend, "rows": rows}


# Consignments ----------------------------------------------------------------------------------------

def consignments(conn: psycopg.Connection, v: Viewer, include_completed: bool = False) -> list[dict]:
    """Consignments ("shipments" on the dashboard) the viewer may see, with custody, the logger's latest
    temperature and whether it is still reporting, and open incidents (E9, P19)."""
    rows = conn.cursor(row_factory=dict_row).execute(f"""
        {AREA}
        SELECT c.id::text, c.consignment_no, p.name AS product_name, c.quantity, f.name AS origin_name,
               c.destination, c.expected_transit_min, c.created_at, c.completed_at, dv.serial_no AS logger,
               p.lower_limit, p.upper_limit, p.freeze_limit,
               s.last_received_at, r.temperature, r.event_time AS reading_time,
               s.last_received_at < now() - make_interval(secs => {STALE_INTERVALS} * dv.reporting_interval_s)
                   AS stale,
               (SELECT CASE WHEN bool_or(i.severity = 'critical') THEN 'critical' ELSE max(i.severity) END
                FROM incident i WHERE i.consignment_id = c.id AND i.state = ANY(%(open)s)) AS severity,
               (SELECT count(*) FROM incident i WHERE i.consignment_id = c.id AND i.state = ANY(%(open)s))
                   AS open_incidents
        FROM consignment c JOIN facility f ON f.id = c.origin_facility_id JOIN product p ON p.id = c.product_id
        LEFT JOIN LATERAL (SELECT device_id, valid_to FROM device_assignment x WHERE x.consignment_id = c.id
                           ORDER BY x.valid_from DESC LIMIT 1) da ON true   -- a completed one keeps its logger
        LEFT JOIN device dv ON dv.id = da.device_id
        LEFT JOIN device_status s ON s.device_id = dv.id
        LEFT JOIN LATERAL (SELECT temperature, event_time FROM reading WHERE device_id = dv.id
                             AND (da.valid_to IS NULL OR event_time <= da.valid_to)
                           ORDER BY event_time DESC LIMIT 1) r ON true
        WHERE {_consignment_scope(v)} AND (%(all)s OR c.completed_at IS NULL)
        ORDER BY c.created_at DESC LIMIT 200""",
        {**v.params, "all": include_completed, "open": list(OPEN_STATES)}).fetchall()
    for c in rows:
        c.update(_custody(conn, v, c["id"]))
        c["status"] = (None if c["logger"] is None or c["completed_at"]
                       else _status(c["severity"], c["last_received_at"], bool(c["stale"])))
    return rows


# Breakdown of shipments for supervisors: an LGA officer by where they left from, a state officer by LGA,
# a national officer by state (E9, F08).
SHIPMENT_GROUPS = {
    "lga_officer": ("facility", "f.id::text", "f.name"),
    "state_officer": ("lga", "j.id", "CASE j.level WHEN 'lga' THEN j.name ELSE j.name || ' State (state stores)' END"),
    "national_officer": ("state", "CASE WHEN j.level = 'lga' THEN j.parent_id ELSE j.id END",
                         ("CASE WHEN j.level = 'lga' THEN pj.name WHEN j.level = 'state' THEN j.name"
                         " ELSE j.name || ' (national stores)' END")),
}
SHIPMENT_METRICS = f"""
    count(*) FILTER (WHERE c.completed_at IS NULL) AS in_transit,
    count(*) FILTER (WHERE h.state = 'pending') AS awaiting,
    count(*) FILTER (WHERE h.state = 'pending' AND h.window_ends_at < now()) AS overdue,
    count(*) FILTER (WHERE h.state = 'rejected') AS refused,
    count(*) FILTER (WHERE c.completed_at IS NULL AND EXISTS (
        SELECT 1 FROM incident i WHERE i.consignment_id = c.id AND i.state = ANY(%(open)s))) AS with_incidents,
    count(*) FILTER (WHERE c.completed_at IS NULL AND da.device_id IS NOT NULL AND (s.last_received_at IS NULL
        OR s.last_received_at < now() - make_interval(secs => {STALE_INTERVALS} * dv.reporting_interval_s)))
        AS silent,
    count(*) FILTER (WHERE c.completed_at >= now() - make_interval(days => %(days)s)) AS completed
"""
SHIPMENT_FROM = """
    FROM consignment c JOIN facility f ON f.id = c.origin_facility_id
    JOIN jurisdiction j ON j.id = f.jurisdiction_id LEFT JOIN jurisdiction pj ON pj.id = j.parent_id
    LEFT JOIN custody_handoff h ON h.consignment_id = c.id AND h.state IN ('pending', 'rejected')
    LEFT JOIN device_assignment da ON da.consignment_id = c.id AND da.valid_to IS NULL
    LEFT JOIN device dv ON dv.id = da.device_id
    LEFT JOIN device_status s ON s.device_id = dv.id
"""


def shipment_summary(conn: psycopg.Connection, v: Viewer, days: int = 7) -> dict:
    """Figures for the top of the Vaccines in transit page, for the shipments the viewer may see: how many
    are in transit, handed over and awaiting confirmation (and how many past their deadline), refused,
    with open incidents, with a logger that has stopped reporting, and completed in the last `days` days;
    how many are waiting for the viewer to confirm; and for supervisors, the same by area."""
    params = {**v.params, "open": list(OPEN_STATES), "days": days}
    where = f"WHERE {_consignment_scope(v)} AND (c.completed_at IS NULL OR c.completed_at >= now() - make_interval(days => %(days)s))"
    cur = conn.cursor(row_factory=dict_row)
    overall = cur.execute(f"""{AREA} SELECT {SHIPMENT_METRICS},
        count(*) FILTER (WHERE h.state = 'pending' AND h.to_user = %(user)s) AS for_you
        {SHIPMENT_FROM} {where}""", params).fetchone()
    group = SHIPMENT_GROUPS.get(v.role)
    rows = []
    if group:
        _, key, label = group
        rows = cur.execute(f"""{AREA} SELECT {key} AS key, {label} AS label, {SHIPMENT_METRICS}
            {SHIPMENT_FROM} {where} GROUP BY 1, 2
            ORDER BY overdue DESC, refused DESC, with_incidents DESC, in_transit DESC, label""", params).fetchall()
    return {"days": days, "overall": overall, "group": group[0] if group else None, "rows": rows}


def _custody(conn: psycopg.Connection, v: Viewer, consignment_id: str) -> dict:
    """Custody state, holder, pending handoff, and what the viewer may do (Section 3.4.4)."""
    st = custody.state(conn, consignment_id)
    holder = custody.holder(conn, consignment_id)
    h = conn.cursor(row_factory=dict_row).execute("""
        SELECT h.id::text, h.state, h.released_at, h.window_ends_at, h.from_user::text, h.to_user::text,
               fu.full_name AS from_name, tu.full_name AS to_name, tu.role AS to_role
        FROM custody_handoff h JOIN app_user fu ON fu.id = h.from_user JOIN app_user tu ON tu.id = h.to_user
        WHERE h.consignment_id = %s AND h.state IN ('pending', 'rejected')""", (consignment_id,)).fetchone()
    name = conn.execute("SELECT full_name, role FROM app_user WHERE id = %s", (holder,)).fetchone() if holder else None
    held = conn.execute("""SELECT valid_from FROM custody_interval WHERE consignment_id = %s AND valid_to IS NULL""",
                        (consignment_id,)).fetchone()
    return {"state": st, "holder_id": holder, "holder_name": name[0] if name else None,
            "holder_role": name[1] if name else None, "held_since": held[0] if held else None, "handoff": h,
            "can_release": st == "held" and holder == v.id,
            "can_complete": st == "held" and holder == v.id,
            "can_confirm": st == "in_handoff" and h is not None and h["to_user"] == v.id,
            "can_reject": st == "in_handoff" and h is not None and h["to_user"] == v.id,
            "can_cancel": st in ("in_handoff", "rejected") and h is not None and h["from_user"] == v.id}


def consignment(conn: psycopg.Connection, v: Viewer, consignment_id: str) -> dict:
    visible = [c for c in consignments(conn, v, include_completed=True) if c["id"] == consignment_id]
    if not visible:
        if conn.execute("SELECT 1 FROM consignment WHERE id = %s", (consignment_id,)).fetchone() is None:
            raise NotFound(f"no consignment {consignment_id}")
        raise NotPermitted("not permitted to view this consignment")
    c = visible[0]
    c["events"] = conn.cursor(row_factory=dict_row).execute("""
        SELECT e.seq, e.type, e.occurred_at, e.details->>'reason' AS reason, a.full_name AS actor_name,
               fu.full_name AS from_name, tu.full_name AS to_name, tu.role AS to_role, a.role AS actor_role
        FROM custody_event e LEFT JOIN app_user a ON a.id = e.actor_id
        LEFT JOIN app_user fu ON fu.id = e.from_user LEFT JOIN app_user tu ON tu.id = e.to_user
        WHERE e.consignment_id = %s ORDER BY e.seq""", (consignment_id,)).fetchall()
    # What someone checking on the vaccines wants to know (E9, P20): where they are, how cold they have
    # been, and anything that went wrong.
    c["location"] = location(conn, {"facility_id": None, "consignment_id": consignment_id})
    c["readings"] = consignment_readings(conn, consignment_id)
    c["incidents"] = conn.cursor(row_factory=dict_row).execute("""
        SELECT i.incident_no, i.type, i.severity, i.state, i.created_at, i.acknowledged_at
        FROM incident i WHERE i.consignment_id = %s ORDER BY i.created_at DESC""", (consignment_id,)).fetchall()
    return c


def consignment_readings(conn: psycopg.Connection, consignment_id: str, points: int = 400) -> list[dict]:
    """The temperature of a consignment's logger while it was assigned, averaged into at most `points`
    buckets."""
    a = conn.execute("""SELECT device_id, valid_from, coalesce(valid_to, now()) FROM device_assignment
                        WHERE consignment_id = %s ORDER BY valid_from DESC LIMIT 1""", (consignment_id,)).fetchone()
    if a is None:
        return []
    device, start, end = a
    bucket = max(60, int((end - start).total_seconds() / points))
    return conn.cursor(row_factory=dict_row).execute("""
        SELECT time_bucket(make_interval(secs => %s), event_time) AS t, round(avg(temperature), 2) AS temp
        FROM reading WHERE device_id = %s AND event_time BETWEEN %s AND %s AND temperature IS NOT NULL
        GROUP BY 1 ORDER BY 1""", (bucket, device, start, end)).fetchall()


RECEIVER_ROLES = (*CARRIERS, "facility_oic")


def receivers(conn: psycopg.Connection, v: Viewer) -> list[dict]:
    """People a consignment can be released to in the viewer's state: outreach leads and drivers, and
    health facility officers (E9, F08), so a shipment can go store -> facility -> outreach team, one
    handover at a time. Each comes with their facility, if any, so the right one can be chosen."""
    return conn.cursor(row_factory=dict_row).execute("""
        WITH RECURSIVE up AS (SELECT id, parent_id, level FROM jurisdiction WHERE id = %s
                              UNION ALL SELECT j.id, j.parent_id, j.level FROM jurisdiction j JOIN up ON j.id = up.parent_id),
             state AS (SELECT id FROM up WHERE level IN ('state', 'nation') ORDER BY level = 'nation' LIMIT 1),
             area AS (SELECT id FROM state UNION ALL SELECT j.id FROM jurisdiction j JOIN area a ON j.parent_id = a.id)
        SELECT u.id::text, u.username, u.full_name, u.role, f.name AS facility_name FROM app_user u
        LEFT JOIN facility f ON f.id = u.facility_id
        WHERE u.active AND u.role = ANY(%s) AND u.jurisdiction_id IN (SELECT id FROM area) AND u.id <> %s
        ORDER BY u.role = 'facility_oic', f.name NULLS FIRST, u.full_name""",
        (v.jurisdiction_id, list(RECEIVER_ROLES), v.id)).fetchall()


def loggers(conn: psycopg.Connection) -> list[dict]:
    """Mobile loggers not assigned to a consignment."""
    return conn.cursor(row_factory=dict_row).execute("""
        SELECT d.serial_no, d.equipment_type FROM device d
        WHERE d.type = 'mobile' AND d.status = 'active'
          AND NOT EXISTS (SELECT 1 FROM device_assignment da WHERE da.device_id = d.id AND da.valid_to IS NULL)
        ORDER BY d.serial_no""").fetchall()


def origins(conn: psycopg.Connection, v: Viewer) -> list[dict]:
    """Facilities a viewer can send consignments from."""
    return conn.cursor(row_factory=dict_row).execute(f"""
        {AREA}
        SELECT f.id::text, f.name, f.tier FROM facility f WHERE {_facility_scope(v)} ORDER BY f.name""",
        v.params).fetchall()
