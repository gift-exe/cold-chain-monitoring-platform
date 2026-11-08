"""Administrative changes (FR11): each is checked against the role allowed to make it and recorded in the
hash-chained admin_action trail (FR11.2) in the same transaction.

Administrators manage users (with their roles, jurisdictions and facilities), jurisdictions, facilities,
devices and phone numbers; national officers manage product limits and the escalation policy (Table 3.21).
Users sign in through the identity server, which only authenticates them: their role and jurisdiction are
taken from this database, so a change made here takes effect at their next request. (Creating the sign-in
account itself for a new dashboard user is done in the identity server; SMS-only users need none.)
"""
import re
import uuid

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from coldchain.errors import InvalidState, NotFound, NotPermitted

PRODUCT_FIELDS = ("lower_limit", "upper_limit", "freeze_limit", "heat_tolerance_min", "freeze_tolerance_min",
                  "freeze_sensitive")


def _require(conn: psycopg.Connection, actor: str, roles: tuple[str, ...], what: str) -> None:
    row = conn.execute("SELECT role FROM app_user WHERE id = %s AND active", (actor,)).fetchone()
    if row is None or row[0] not in roles:
        raise NotPermitted(f"{what} is for {' and '.join(r.replace('_', ' ') + 's' for r in roles)}")


def record(conn: psycopg.Connection, actor: str, action: str, target_type: str, target_id: str,
           before: dict | None, after: dict | None) -> None:
    conn.execute("""INSERT INTO admin_action (actor_id, action, target_type, target_id, before, after)
                    VALUES (%s, %s, %s, %s, %s, %s)""",
                 (actor, action, target_type, target_id, Jsonb(_plain(before)), Jsonb(_plain(after))))


def _plain(d: dict | None) -> dict | None:
    return None if d is None else {k: (float(v) if hasattr(v, "as_tuple") else v) for k, v in d.items()}


def products(conn: psycopg.Connection) -> list[dict]:
    return conn.cursor(row_factory=dict_row).execute(
        f"SELECT id::text, name, {', '.join(PRODUCT_FIELDS)} FROM product ORDER BY name").fetchall()


def _check_limits(p: dict) -> None:
    if not p["freeze_limit"] < p["upper_limit"] or not p["lower_limit"] < p["upper_limit"]:
        raise InvalidState("the upper limit must be above the lower and freezing limits")


def create_product(conn: psycopg.Connection, actor: str, p: dict) -> dict:
    """Add a product with its limits and tolerances (E9, P23). Detection applies it to consignments of the
    product from now on, and to stores as part of the strictest values across all products."""
    _require(conn, actor, ("national_officer",), "adding products")
    p = {"lower_limit": 2.0, "upper_limit": 8.0, "freeze_limit": 0.0, "heat_tolerance_min": 30,
         "freeze_tolerance_min": 5, "freeze_sensitive": True,
         **{k: v for k, v in p.items() if v is not None}, "id": str(uuid.uuid4())}
    p["name"] = p["name"].strip()
    _check_limits(p)
    _write(conn, f"""INSERT INTO product (id, name, {', '.join(PRODUCT_FIELDS)})
                     VALUES (%(id)s, %(name)s, {', '.join(f'%({k})s' for k in PRODUCT_FIELDS)})""",
           p, "product", f"there is already a product called {p['name']}")
    record(conn, actor, "product_created", "product", p["id"], None, p)
    return p


def update_product(conn: psycopg.Connection, actor: str, product_id: str, changes: dict) -> dict:
    """Change a product's limits or tolerances. Detection uses the new values from its next evaluation."""
    _require(conn, actor, ("national_officer",), "changing product limits")
    cur = conn.cursor(row_factory=dict_row)
    before = cur.execute(f"SELECT {', '.join(PRODUCT_FIELDS)} FROM product WHERE id = %s FOR UPDATE",
                         (product_id,)).fetchone()
    if before is None:
        raise NotFound(f"no product {product_id}")
    changes = {k: v for k, v in changes.items() if k in PRODUCT_FIELDS and v is not None and v != before[k]}
    if not changes:
        return before
    after = {**before, **changes}
    _check_limits(after)
    sets = ", ".join(f"{k} = %({k})s" for k in changes)
    cur.execute(f"UPDATE product SET {sets} WHERE id = %(id)s", {**changes, "id": product_id})
    record(conn, actor, "product_updated", "product", product_id, {k: before[k] for k in changes}, changes)
    return after


def users(conn: psycopg.Connection, actor: str) -> list[dict]:
    _require(conn, actor, ("admin",), "the user list")
    return conn.cursor(row_factory=dict_row).execute("""
        SELECT u.id::text, u.username, u.full_name, u.role, u.jurisdiction_id, u.facility_id::text, j.name AS jurisdiction_name,
               f.name AS facility_name, u.phone_number, u.phone_deactivated_at, u.active
        FROM app_user u JOIN jurisdiction j ON j.id = u.jurisdiction_id LEFT JOIN facility f ON f.id = u.facility_id
        ORDER BY u.role, u.full_name""").fetchall()


def _phone(conn: psycopg.Connection, ref: str) -> dict:
    """The user referred to by id or username, locked. Called after the permission check, so that only
    administrators learn whether a user exists."""
    row = conn.cursor(row_factory=dict_row).execute(
        """SELECT id::text, phone_number, phone_deactivated_at FROM app_user
           WHERE id::text = %s OR username = %s FOR UPDATE""", (ref, ref)).fetchone()
    if row is None:
        raise NotFound(f"no user {ref}")
    return row


def deactivate_phone(conn: psycopg.Connection, actor: str, ref: str) -> None:
    """A lost phone: every command from the number is rejected from now on (Section 3.4.6)."""
    _require(conn, actor, ("admin",), "changing phone numbers")
    before = _phone(conn, ref)
    user_id = before["id"]
    if before["phone_deactivated_at"] is not None:
        return
    conn.execute("UPDATE app_user SET phone_deactivated_at = now() WHERE id = %s", (user_id,))
    record(conn, actor, "phone_deactivated", "app_user", user_id, {"phone_number": before["phone_number"]},
           {"phone_number": before["phone_number"], "deactivated": True})


def set_phone(conn: psycopg.Connection, actor: str, ref: str, phone_number: str) -> None:
    """Register a (new) number for a user; it is active at once."""
    _require(conn, actor, ("admin",), "changing phone numbers")
    before = _phone(conn, ref)
    user_id = before["id"]
    try:
        with conn.transaction():
            conn.execute("UPDATE app_user SET phone_number = %s, phone_deactivated_at = NULL WHERE id = %s",
                         (phone_number, user_id))
    except psycopg.errors.UniqueViolation:
        raise InvalidState("the number belongs to another active user") from None
    record(conn, actor, "phone_set", "app_user", user_id,
           {"phone_number": before["phone_number"], "deactivated": before["phone_deactivated_at"] is not None},
           {"phone_number": phone_number, "deactivated": False})


def system_status(conn: psycopg.Connection, actor: str) -> dict:
    """What an administrator watches (Table 3.21): are devices reporting, is anything stuck."""
    _require(conn, actor, ("admin",), "system status")
    def q(sql: str):
        return conn.execute(sql).fetchone()[0]
    return {
        "devices_active": q("SELECT count(*) FROM device WHERE status = 'active'"),
        "devices_reporting": q("""SELECT count(*) FROM device d JOIN device_status s ON s.device_id = d.id
                                  WHERE d.status = 'active'
                                    AND s.last_received_at >= now() - make_interval(secs => 3 * d.reporting_interval_s)"""),
        "last_reading_received_at": q("SELECT max(last_received_at) FROM device_status"),
        "sms_waiting": q("SELECT count(*) FROM notification WHERE channel = 'sms' AND status IN ('queued', 'retrying')"),
        "sms_undelivered_24h": q("""SELECT count(*) FROM notification WHERE channel = 'sms'
                                    AND status IN ('undelivered', 'rejected') AND created_at > now() - interval '1 day'"""),
        "outbox_unsent": q("SELECT count(*) FROM outbox WHERE sent_at IS NULL"),
        "open_incidents": q("SELECT count(*) FROM incident WHERE state NOT IN ('resolved', 'closed')"),
        "rejected_messages_24h": q("SELECT count(*) FROM rejected_message WHERE received_at > now() - interval '1 day'"),
    }


# Users, jurisdictions, facilities and devices (FR11.1) ------------------------------------------------

ROLES = ("national_officer", "state_officer", "lga_officer", "facility_oic", "outreach_lead", "driver", "admin")
LEVEL_FOR_ROLE = {"national_officer": "nation", "state_officer": "state", "lga_officer": "lga"}
TIERS = ("state_store", "lga_store", "health_facility", "outreach_base", "zonal_store", "national_store")
POWER = ("grid", "solar", "generator", "grid_generator", "none")
PHONE = re.compile(r"^\+[1-9][0-9]{7,14}$")


def _one(conn: psycopg.Connection, sql: str, params) -> dict | None:
    return conn.cursor(row_factory=dict_row).execute(sql, params).fetchone()


def _level(conn: psycopg.Connection, jurisdiction_id: str) -> str:
    row = conn.execute("SELECT level FROM jurisdiction WHERE id = %s", (jurisdiction_id,)).fetchone()
    if row is None:
        raise InvalidState(f"no jurisdiction {jurisdiction_id}")
    return row[0]


def _check_user(conn: psycopg.Connection, u: dict) -> None:
    if u["role"] not in ROLES:
        raise InvalidState(f"unknown role {u['role']}")
    level = _level(conn, u["jurisdiction_id"])
    if u["role"] in LEVEL_FOR_ROLE and LEVEL_FOR_ROLE[u["role"]] != level:
        raise InvalidState(f"a {u['role'].replace('_', ' ')} belongs to a jurisdiction at {LEVEL_FOR_ROLE[u['role']]} level")
    if u["role"] == "facility_oic" and not u.get("facility_id"):
        raise InvalidState("an officer-in-charge needs a facility")
    if u.get("facility_id"):
        row = conn.execute("SELECT jurisdiction_id FROM facility WHERE id = %s", (u["facility_id"],)).fetchone()
        if row is None:
            raise InvalidState("no such facility")
        if level == "lga" and row[0] != u["jurisdiction_id"]:
            raise InvalidState("the facility is not in the user's jurisdiction")
    if u.get("phone_number") and not PHONE.match(u["phone_number"]):
        raise InvalidState("phone numbers are in international format, e.g. +2348031234567")


def _write(conn: psycopg.Connection, sql: str, params: dict, what: str, taken: str | None = None):
    try:
        with conn.transaction():
            return conn.execute(sql, params)
    except psycopg.errors.UniqueViolation:
        raise InvalidState(taken or f"{what} already exists or the phone number belongs to another active user") from None


USER_FIELDS = ("full_name", "role", "jurisdiction_id", "facility_id", "active")


def create_user(conn: psycopg.Connection, actor: str, u: dict) -> dict:
    _require(conn, actor, ("admin",), "managing users")
    u = {"facility_id": None, "phone_number": None, **u, "id": str(uuid.uuid4())}
    _check_user(conn, u)
    _write(conn, """INSERT INTO app_user (id, username, full_name, role, jurisdiction_id, facility_id, phone_number)
                    VALUES (%(id)s, %(username)s, %(full_name)s, %(role)s, %(jurisdiction_id)s, %(facility_id)s,
                            %(phone_number)s)""", u, f"user {u['username']}")
    record(conn, actor, "user_created", "app_user", u["id"], None, u)
    return u


def update_user(conn: psycopg.Connection, actor: str, ref: str, changes: dict) -> dict:
    _require(conn, actor, ("admin",), "managing users")
    before = _one(conn, f"SELECT id::text, {', '.join(USER_FIELDS)}, phone_number FROM app_user "
                        "WHERE id::text = %s OR username = %s FOR UPDATE", (ref, ref))
    if before is None:
        raise NotFound(f"no user {ref}")
    before["facility_id"] = before["facility_id"] and str(before["facility_id"])
    changes = {k: v for k, v in changes.items() if k in USER_FIELDS and v != before[k]}
    if not changes:
        return before
    if before["id"] == actor and ("active" in changes or "role" in changes):
        raise InvalidState("administrators cannot deactivate themselves or change their own role")
    after = {**before, **changes}
    _check_user(conn, after)
    sets = ", ".join(f"{k} = %({k})s" for k in changes)
    _write(conn, f"UPDATE app_user SET {sets} WHERE id = %(id)s", {**changes, "id": before["id"]}, "user")
    record(conn, actor, "user_updated", "app_user", before["id"], {k: before[k] for k in changes}, changes)
    return after


def jurisdictions(conn: psycopg.Connection) -> list[dict]:
    return conn.cursor(row_factory=dict_row).execute(
        "SELECT id, level, name, parent_id FROM jurisdiction ORDER BY id").fetchall()


def create_jurisdiction(conn: psycopg.Connection, actor: str, j: dict) -> dict:
    _require(conn, actor, ("admin",), "managing jurisdictions")
    parent_level = {"state": "nation", "lga": "state"}.get(j["level"])
    if parent_level is None or _level(conn, j["parent_id"]) != parent_level:
        raise InvalidState("a state belongs to the nation and an LGA to a state")
    if not re.fullmatch(re.escape(j["parent_id"]) + r"-[A-Z0-9]{2,4}", j["id"]):
        raise InvalidState(f"the code extends the parent's, e.g. {j['parent_id']}-ABC")
    _write(conn, "INSERT INTO jurisdiction (id, level, name, parent_id) VALUES (%(id)s, %(level)s, %(name)s, %(parent_id)s)",
           j, f"jurisdiction {j['id']}")
    record(conn, actor, "jurisdiction_created", "jurisdiction", j["id"], None, j)
    return j


def rename_jurisdiction(conn: psycopg.Connection, actor: str, jid: str, name: str) -> None:
    _require(conn, actor, ("admin",), "managing jurisdictions")
    before = _one(conn, "SELECT name FROM jurisdiction WHERE id = %s FOR UPDATE", (jid,))
    if before is None:
        raise NotFound(f"no jurisdiction {jid}")
    conn.execute("UPDATE jurisdiction SET name = %s WHERE id = %s", (name, jid))
    record(conn, actor, "jurisdiction_renamed", "jurisdiction", jid, before, {"name": name})


FACILITY_FIELDS = ("name", "tier", "jurisdiction_id", "parent_facility_id", "power_source", "lat", "lon", "ward")
FACILITY_SELECT = """SELECT f.id::text, f.name, f.tier, f.jurisdiction_id, f.parent_facility_id::text, f.power_source, f.ward,
                            ST_Y(f.location::geometry) AS lat, ST_X(f.location::geometry) AS lon FROM facility f"""


def facilities(conn: psycopg.Connection) -> list[dict]:
    return conn.cursor(row_factory=dict_row).execute(FACILITY_SELECT + " ORDER BY f.jurisdiction_id, f.name").fetchall()


def _check_facility(conn: psycopg.Connection, f: dict) -> None:
    if f["tier"] not in TIERS or f["power_source"] not in POWER:
        raise InvalidState("unknown tier or power source")
    _level(conn, f["jurisdiction_id"])
    if not (-90 <= float(f["lat"]) <= 90 and -180 <= float(f["lon"]) <= 180):
        raise InvalidState("the location is not a valid latitude and longitude")
    if f.get("parent_facility_id") and conn.execute("SELECT 1 FROM facility WHERE id = %s",
                                                    (f["parent_facility_id"],)).fetchone() is None:
        raise InvalidState("no such parent facility")


def create_facility(conn: psycopg.Connection, actor: str, f: dict) -> dict:
    _require(conn, actor, ("admin",), "managing facilities")
    f = {"parent_facility_id": None, "ward": None, **f, "id": str(uuid.uuid4())}
    _check_facility(conn, f)
    conn.execute("""INSERT INTO facility (id, name, tier, jurisdiction_id, parent_facility_id, location, power_source, ward)
                    VALUES (%(id)s, %(name)s, %(tier)s, %(jurisdiction_id)s, %(parent_facility_id)s,
                            ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326)::geography, %(power_source)s, %(ward)s)""", f)
    record(conn, actor, "facility_created", "facility", f["id"], None, f)
    return f


def update_facility(conn: psycopg.Connection, actor: str, fid: str, changes: dict) -> dict:
    _require(conn, actor, ("admin",), "managing facilities")
    before = _one(conn, FACILITY_SELECT + " WHERE f.id = %s", (fid,))
    if before is None:
        raise NotFound(f"no facility {fid}")
    changes = {k: v for k, v in changes.items() if k in FACILITY_FIELDS and v is not None and v != before[k]}
    if not changes:
        return before
    after = {**before, **changes}
    _check_facility(conn, after)
    conn.execute("""UPDATE facility SET name = %(name)s, tier = %(tier)s, jurisdiction_id = %(jurisdiction_id)s,
                        parent_facility_id = %(parent_facility_id)s, power_source = %(power_source)s, ward = %(ward)s,
                        location = ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326)::geography
                    WHERE id = %(id)s""", after)
    record(conn, actor, "facility_updated", "facility", fid, {k: before[k] for k in changes}, changes)
    return after


DEVICE_FIELDS = ("facility_id", "type", "equipment_type", "has_door_sensor", "has_power_signal",
                 "reporting_interval_s", "sync_window_min", "status")
DEVICE_SELECT = f"SELECT id::text, serial_no, {', '.join(DEVICE_FIELDS)} FROM device"


def devices(conn: psycopg.Connection) -> list[dict]:
    rows = conn.cursor(row_factory=dict_row).execute(DEVICE_SELECT + " ORDER BY serial_no").fetchall()
    for r in rows:
        r["facility_id"] = r["facility_id"] and str(r["facility_id"])
    return rows


def _check_device(d: dict) -> None:
    if d["type"] not in ("fixed", "mobile") or d["status"] not in ("active", "inactive", "retired"):
        raise InvalidState("unknown device type or status")
    if d["type"] == "fixed" and not d.get("facility_id"):
        raise InvalidState("a fixed device belongs to a facility")
    if not (int(d["reporting_interval_s"]) > 0 and int(d["sync_window_min"]) > 0):
        raise InvalidState("intervals must be positive")


def register_device(conn: psycopg.Connection, actor: str, d: dict) -> dict:
    """A new logger. Ingestion accepts its readings once it is registered and active (FR1.3)."""
    _require(conn, actor, ("admin",), "managing devices")
    d = {"facility_id": None, "has_door_sensor": False, "has_power_signal": False, "reporting_interval_s": 60,
         "sync_window_min": 1440, "status": "active", **d}
    _check_device(d)
    d["id"] = _write(conn, f"""INSERT INTO device (serial_no, {', '.join(DEVICE_FIELDS)})
                               VALUES (%(serial_no)s, {', '.join(f'%({k})s' for k in DEVICE_FIELDS)})
                               RETURNING id::text""", d, f"device {d['serial_no']}").fetchone()[0]
    record(conn, actor, "device_registered", "device", d["id"], None, d)
    return d


def update_device(conn: psycopg.Connection, actor: str, ref: str, changes: dict) -> dict:
    """Move, reconfigure, deactivate or retire a device. Its readings and incidents are kept."""
    _require(conn, actor, ("admin",), "managing devices")
    before = _one(conn, DEVICE_SELECT + " WHERE id::text = %s OR serial_no = %s FOR UPDATE", (ref, ref))
    if before is None:
        raise NotFound(f"no device {ref}")
    before["facility_id"] = before["facility_id"] and str(before["facility_id"])
    changes = {k: v for k, v in changes.items() if k in DEVICE_FIELDS and v is not None and v != before[k]}
    if not changes:
        return before
    after = {**before, **changes}
    _check_device(after)
    sets = ", ".join(f"{k} = %({k})s" for k in changes)
    conn.execute(f"UPDATE device SET {sets} WHERE id = %(id)s", {**changes, "id": before["id"]})
    record(conn, actor, "device_updated", "device", before["id"], {k: before[k] for k in changes}, changes)
    return after


# Escalation policy (Table 3.15; national officers, Table 3.21) -----------------------------------------

def policy(conn: psycopg.Connection) -> list[dict]:
    return conn.cursor(row_factory=dict_row).execute("""
        SELECT id, severity, mode, kind, stage, tier, delay_min FROM escalation_policy
        ORDER BY severity, mode, kind, stage""").fetchall()


def update_policy(conn: psycopg.Connection, actor: str, policy_id: int, delay_min: int) -> dict:
    """Change one time limit. Limits of the same clock must keep increasing with the stage, because they are
    cumulative (Table 3.15). New limits apply to every open incident from its next step."""
    _require(conn, actor, ("national_officer",), "changing the escalation policy")
    before = _one(conn, "SELECT * FROM escalation_policy WHERE id = %s FOR UPDATE", (policy_id,))
    if before is None:
        raise NotFound(f"no policy step {policy_id}")
    if delay_min < 0 or (before["stage"] > 0 and before["kind"] != "reminder" and delay_min == 0):
        raise InvalidState("time limits after the first are positive")
    same = conn.execute("""SELECT stage, delay_min FROM escalation_policy
                           WHERE severity = %s AND mode = %s AND kind = %s AND id <> %s""",
                        (before["severity"], before["mode"], before["kind"], policy_id)).fetchall()
    for stage, delay in same:
        if (stage < before["stage"] and delay >= delay_min) or (stage > before["stage"] and delay <= delay_min):
            raise InvalidState("time limits are cumulative: each must be later than the one before it and "
                               "earlier than the one after it")
    conn.execute("UPDATE escalation_policy SET delay_min = %s WHERE id = %s", (delay_min, policy_id))
    record(conn, actor, "policy_updated", "escalation_policy", str(policy_id), {"delay_min": before["delay_min"]},
           {"delay_min": delay_min})
    return {**before, "delay_min": delay_min}
