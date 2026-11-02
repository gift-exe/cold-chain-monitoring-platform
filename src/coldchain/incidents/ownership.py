"""Who owns an incident, and who is notified at each tier (FR5.2, FR5.3, Table 3.16).

An event on a logger travelling with a consignment is owned by whoever held the consignment when the
event began (CR6), which for a retrospective event may be an earlier custodian. During a handoff the
releaser is still the custodian (CR2, CR3) and the intended receiver is copied. If nobody held the
consignment at that time, the LGA officer for its last known location owns the incident and a custody
gap is raised (CR7). An event on a fixed device with no consignment is owned by the facility's
officer-in-charge, or, if the facility has none, by its LGA officer.
"""
from dataclasses import dataclass, field
from datetime import datetime

import psycopg

# Officers notified at each escalation tier, from the incident's jurisdiction upwards
TIER_ROLES = {"lga": ("lga_officer", "lga"), "state": ("state_officer", "state"),
              "national": ("national_officer", "nation")}
LEVELS = ("lga", "state", "nation")


@dataclass(frozen=True)
class Ownership:
    owner_id: str
    rule: str                     # which rule chose the owner, recorded on the incident
    jurisdiction_id: str          # where escalations are routed
    consignment_id: str | None = None
    cc_ids: list[str] = field(default_factory=list)
    custody_gap: bool = False     # CR7: nobody held the consignment at the event's start


def officers(conn: psycopg.Connection, jurisdiction_id: str, tier: str) -> list[str]:
    """Active officers of a tier for a jurisdiction. If the jurisdiction has no such level (an incident at
    a state store has no LGA) or the post is vacant, the next level up is used."""
    _, level = TIER_ROLES[tier]
    for role, lvl in list(TIER_ROLES.values())[LEVELS.index(level):]:
        rows = conn.execute("""
            WITH RECURSIVE up AS (
                SELECT id, level, parent_id FROM jurisdiction WHERE id = %s
                UNION ALL
                SELECT j.id, j.level, j.parent_id FROM jurisdiction j JOIN up ON j.id = up.parent_id)
            SELECT u.id::text FROM up JOIN app_user u ON u.jurisdiction_id = up.id
            WHERE up.level = %s AND u.role = %s AND u.active ORDER BY u.username""",
            (jurisdiction_id, lvl, role)).fetchall()
        if rows:
            return [r[0] for r in rows]
    return []


def recipients(conn: psycopg.Connection, tier: str, owner_id: str, jurisdiction_id: str) -> list[str]:
    return [owner_id] if tier == "owner" else officers(conn, jurisdiction_id, tier)


def custodian_at(conn: psycopg.Connection, consignment_id: str, t: datetime) -> str | None:
    row = conn.execute("""
        SELECT holder_id::text FROM custody_interval
        WHERE consignment_id = %s AND tstzrange(valid_from, valid_to) @> %s::timestamptz""",
        (consignment_id, t)).fetchone()
    return row[0] if row else None


def receiver_in_handoff_at(conn: psycopg.Connection, consignment_id: str, t: datetime) -> str | None:
    row = conn.execute("""
        SELECT to_user::text FROM custody_handoff
        WHERE consignment_id = %s AND released_at <= %s AND (ended_at IS NULL OR ended_at > %s)""",
        (consignment_id, t, t)).fetchone()
    return row[0] if row else None


def assignment_at(conn: psycopg.Connection, device_id: str, t: datetime) -> str | None:
    row = conn.execute("""
        SELECT consignment_id::text FROM device_assignment
        WHERE device_id = %s AND tstzrange(valid_from, valid_to) @> %s::timestamptz""", (device_id, t)).fetchone()
    return row[0] if row else None


def located_jurisdiction(conn: psycopg.Connection, consignment_id: str) -> str:
    """Jurisdiction of a consignment's last known location (FR5.3): the most specific boundary containing
    the last location reported by any logger assigned to it, else the jurisdiction of its origin."""
    row = conn.execute("""
        SELECT j.id FROM (
            SELECT r.location FROM reading r JOIN device_assignment da ON da.device_id = r.device_id
                 AND tstzrange(da.valid_from, da.valid_to) @> r.event_time
            WHERE da.consignment_id = %s AND r.location IS NOT NULL
            ORDER BY r.event_time DESC LIMIT 1) last
        JOIN jurisdiction j ON j.boundary IS NOT NULL AND ST_Covers(j.boundary, last.location)
        ORDER BY array_position(ARRAY['lga', 'state', 'nation'], j.level) LIMIT 1""",
        (consignment_id,)).fetchone()
    if row:
        return row[0]
    return conn.execute("""
        SELECT f.jurisdiction_id FROM consignment c JOIN facility f ON f.id = c.origin_facility_id
        WHERE c.id = %s""", (consignment_id,)).fetchone()[0]


def _unheld(conn: psycopg.Connection, consignment_id: str) -> Ownership:
    jur = located_jurisdiction(conn, consignment_id)
    lga = officers(conn, jur, "lga")
    return Ownership(lga[0], "CR7: no custodian at event start; LGA officer for last known location", jur,
                     consignment_id, custody_gap=True)


def for_consignment(conn: psycopg.Connection, consignment_id: str, t: datetime,
                    rule: str = "CR6: custodian at event start") -> Ownership:
    holder = custodian_at(conn, consignment_id, t)
    if holder is None:
        return _unheld(conn, consignment_id)
    jur = located_jurisdiction(conn, consignment_id)
    receiver = receiver_in_handoff_at(conn, consignment_id, t)
    if receiver:
        return Ownership(holder, "CR2: releaser during handoff; receiver copied", jur, consignment_id, [receiver])
    return Ownership(holder, rule, jur, consignment_id)


def for_custody_gap(conn: psycopg.Connection, consignment_id: str, t: datetime) -> Ownership:
    """CR4, CR5: a custody gap is owned by the releaser (still the custodian) and copied to the LGA
    officer; CR7 gaps (nobody held it) go to the LGA officer."""
    holder = custodian_at(conn, consignment_id, t)
    if holder is None:
        return _unheld(conn, consignment_id)
    jur = located_jurisdiction(conn, consignment_id)
    return Ownership(holder, "CR4/CR5: releaser; LGA officer copied", jur, consignment_id,
                     [o for o in officers(conn, jur, "lga") if o != holder])


def for_device(conn: psycopg.Connection, device_id: str, t: datetime) -> Ownership:
    consignment = assignment_at(conn, device_id, t)
    if consignment:
        return for_consignment(conn, consignment, t)
    facility, jur = conn.execute("""
        SELECT f.id::text, f.jurisdiction_id FROM device d LEFT JOIN facility f ON f.id = d.facility_id
        WHERE d.id = %s""", (device_id,)).fetchone()
    if facility is None:   # an unassigned mobile logger: nobody is accountable for its contents
        admin = conn.execute("SELECT id::text, jurisdiction_id FROM app_user WHERE role = 'admin' AND active "
                             "ORDER BY username LIMIT 1").fetchone()
        return Ownership(admin[0], "unassigned mobile logger: administrator", admin[1])
    oic = conn.execute("""
        SELECT id::text FROM app_user WHERE role = 'facility_oic' AND facility_id = %s AND active
        ORDER BY username LIMIT 1""", (facility,)).fetchone()
    if oic:
        return Ownership(oic[0], "FR5.2: facility officer-in-charge", jur)
    lga = officers(conn, jur, "lga")
    return Ownership(lga[0], "FR5.2: facility has no officer-in-charge; LGA officer", jur)
