"""Administrative changes (FR11): each is checked against the role allowed to make it and recorded in the
hash-chained admin_action trail (FR11.2) in the same transaction.

Iteration I6 covers the changes the dashboard tasks need (Table 3.22: T7, T8): product temperature limits
and tolerances (national officers, Table 3.21) and users' phone numbers (administrators).
"""
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
    if not after["freeze_limit"] < after["upper_limit"] or not after["lower_limit"] < after["upper_limit"]:
        raise InvalidState("the upper limit must be above the lower and freezing limits")
    sets = ", ".join(f"{k} = %({k})s" for k in changes)
    cur.execute(f"UPDATE product SET {sets} WHERE id = %(id)s", {**changes, "id": product_id})
    record(conn, actor, "product_updated", "product", product_id, {k: before[k] for k in changes}, changes)
    return after


def users(conn: psycopg.Connection, actor: str) -> list[dict]:
    _require(conn, actor, ("admin",), "the user list")
    return conn.cursor(row_factory=dict_row).execute("""
        SELECT u.id::text, u.username, u.full_name, u.role, u.jurisdiction_id, j.name AS jurisdiction_name,
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
