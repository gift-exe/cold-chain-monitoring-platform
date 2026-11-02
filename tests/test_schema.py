"""I1 acceptance: the database enforces the constraints in the data dictionary (Table 3.18)."""
import psycopg
import pytest
from psycopg import errors

pytestmark = pytest.mark.integration

FIXED_DEVICE = "d0000000-0000-4000-8000-000000000001"
MOBILE_DEVICE = "d0000000-0000-4000-8000-000000000002"
PRODUCT = "5c1d0a00-0000-4000-8000-000000000001"
LGA_STORE = "8a3c1f52-2b7e-4c1d-9d6a-0f4e5b6c7d11"
LGA_OFFICER = "0b1e5a10-0000-4000-8000-000000000003"
OUTREACH_LEAD = "0b1e5a10-0000-4000-8000-000000000005"


def new_consignment(conn) -> str:
    return conn.execute(
        "INSERT INTO consignment (product_id, quantity, origin_facility_id, destination, expected_transit_min) "
        "VALUES (%s, 100, %s, 'Outreach site', 90) RETURNING id", (PRODUCT, LGA_STORE)).fetchone()[0]


def add_custody(conn, consignment, holder, start, end):
    conn.execute("INSERT INTO custody_interval (consignment_id, holder_id, valid_from, valid_to) "
                 "VALUES (%s, %s, %s, %s)", (consignment, holder, start, end))


# Custody (CR1-CR3) ----------------------------------------------------------

def test_consecutive_custody_intervals_allowed(owner_conn):
    c = new_consignment(owner_conn)
    add_custody(owner_conn, c, LGA_OFFICER, "2026-11-01 08:00Z", "2026-11-01 09:30Z")
    add_custody(owner_conn, c, OUTREACH_LEAD, "2026-11-01 09:30Z", None)  # handoff confirmed at 09:30


def test_overlapping_custody_rejected(owner_conn):
    c = new_consignment(owner_conn)
    add_custody(owner_conn, c, LGA_OFFICER, "2026-11-01 08:00Z", None)  # still held, interval open
    with pytest.raises(errors.ExclusionViolation):
        add_custody(owner_conn, c, OUTREACH_LEAD, "2026-11-01 09:30Z", None)


def test_custodian_at_event_start_is_unique(owner_conn):
    c = new_consignment(owner_conn)
    add_custody(owner_conn, c, LGA_OFFICER, "2026-11-01 08:00Z", "2026-11-01 09:30Z")
    add_custody(owner_conn, c, OUTREACH_LEAD, "2026-11-01 09:30Z", None)
    holders = owner_conn.execute(
        "SELECT holder_id FROM custody_interval WHERE consignment_id = %s "
        "AND tstzrange(valid_from, valid_to) @> %s::timestamptz", (c, "2026-11-01 09:00Z")).fetchall()
    assert [str(h[0]) for h in holders] == [LGA_OFFICER]


def test_overlapping_device_assignment_rejected(owner_conn):
    c1, c2 = new_consignment(owner_conn), new_consignment(owner_conn)
    owner_conn.execute("INSERT INTO device_assignment VALUES (%s, %s, '2026-11-01 08:00Z', '2026-11-02 08:00Z')",
                       (MOBILE_DEVICE, c1))
    with pytest.raises(errors.ExclusionViolation):
        owner_conn.execute("INSERT INTO device_assignment VALUES (%s, %s, '2026-11-01 20:00Z', NULL)",
                           (MOBILE_DEVICE, c2))


# Readings (FR1.4, FR2.4) ---------------------------------------------------

INSERT_READING = ("INSERT INTO reading (device_id, seq_no, event_time, received_at, temperature) "
                  "VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING")


def test_duplicate_reading_stored_once(app_conn):
    for _ in range(3):
        app_conn.execute(INSERT_READING, (FIXED_DEVICE, 42, "2026-11-01 10:00Z", "2026-11-01 10:00:05Z", 5.1))
    n = app_conn.execute("SELECT count(*) FROM reading WHERE device_id = %s AND seq_no = 42",
                         (FIXED_DEVICE,)).fetchone()[0]
    assert n == 1


def test_reading_from_the_future_rejected(app_conn):
    with pytest.raises(errors.CheckViolation):
        app_conn.execute(INSERT_READING, (FIXED_DEVICE, 43, "2026-11-01 10:10Z", "2026-11-01 10:00Z", 5.0))


def test_reading_72_hours_late_accepted(app_conn):
    app_conn.execute(INSERT_READING, (FIXED_DEVICE, 44, "2026-11-01 10:00Z", "2026-11-04 10:00Z", 5.0))


# Audit trail (FR7.2) --------------------------------------------------------

def make_incident(conn) -> str:
    ev = conn.execute("INSERT INTO integrity_event (device_id, type, event_start) "
                      "VALUES (%s, 'heat_excursion', now()) RETURNING id", (FIXED_DEVICE,)).fetchone()[0]
    return conn.execute(
        "INSERT INTO incident (incident_key, integrity_event_id, type, severity, mode, owner_id, device_id, "
        "jurisdiction_id, ownership, detected_at) "
        "VALUES (%s, %s, 'heat_excursion', 'critical', 'operational', %s, %s, 'NG-KN-GWL', 'test', now()) RETURNING id",
        (f"test:{ev}", ev, LGA_OFFICER, FIXED_DEVICE)).fetchone()[0]


def test_services_can_append_to_audit_trail(app_conn):
    inc = make_incident(app_conn)
    app_conn.execute("INSERT INTO incident_action (incident_id, action, channel) VALUES (%s, 'created', 'system')",
                     (inc,))


@pytest.mark.parametrize("statement", ["UPDATE incident_action SET channel = 'sms'", "DELETE FROM incident_action"])
def test_services_cannot_alter_audit_trail(app_conn, statement):
    inc = make_incident(app_conn)
    app_conn.execute("INSERT INTO incident_action (incident_id, action, channel) VALUES (%s, 'created', 'system')",
                     (inc,))
    with pytest.raises(errors.InsufficientPrivilege):
        app_conn.execute(statement)


def test_even_schema_owner_cannot_alter_audit_trail(owner_conn):
    inc = make_incident(owner_conn)
    owner_conn.execute("INSERT INTO incident_action (incident_id, action, channel) VALUES (%s, 'created', 'system')",
                       (inc,))
    with pytest.raises(errors.InsufficientPrivilege, match="append-only"):
        owner_conn.execute("UPDATE incident_action SET channel = 'sms'")


def test_custody_events_are_append_only(app_conn):
    c = new_consignment(app_conn)
    app_conn.execute("INSERT INTO custody_event (consignment_id, type, from_user) VALUES (%s, 'release', %s)",
                     (c, LGA_OFFICER))
    with pytest.raises(errors.InsufficientPrivilege):
        app_conn.execute("DELETE FROM custody_event")


# Incidents and users -------------------------------------------------------

def test_incident_key_prevents_duplicate_incidents(owner_conn):
    inc = make_incident(owner_conn)
    key = owner_conn.execute("SELECT incident_key FROM incident WHERE id = %s", (inc,)).fetchone()[0]
    ev = owner_conn.execute("INSERT INTO integrity_event (device_id, type, event_start) "
                            "VALUES (%s, 'heat_excursion', now()) RETURNING id", (FIXED_DEVICE,)).fetchone()[0]
    with pytest.raises(errors.UniqueViolation):
        owner_conn.execute("INSERT INTO incident (incident_key, integrity_event_id, type, severity, mode, owner_id, "
                           "device_id, jurisdiction_id, ownership, detected_at) VALUES (%s, %s, 'heat_excursion', "
                           "'critical', 'operational', %s, %s, 'NG-KN-GWL', 'test', now())",
                           (key, ev, LGA_OFFICER, FIXED_DEVICE))


def test_phone_number_unique_among_active_users(owner_conn):
    taken = owner_conn.execute("SELECT phone_number FROM app_user WHERE active LIMIT 1").fetchone()[0]
    with pytest.raises(errors.UniqueViolation):
        owner_conn.execute("INSERT INTO app_user (id, username, full_name, role, jurisdiction_id, phone_number) "
                           "VALUES (gen_random_uuid(), 'dup', 'Dup', 'driver', 'NG-KN', %s)", (taken,))


def test_fixed_device_needs_facility(owner_conn):
    with pytest.raises(psycopg.errors.CheckViolation):
        owner_conn.execute("INSERT INTO device (serial_no, type, equipment_type) VALUES ('X-1', 'fixed', 'fridge')")
