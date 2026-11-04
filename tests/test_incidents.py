"""Incidents in the database: creation, ownership, actions, review, recurrence and escalation steps
(Sections 3.4.2 to 3.4.4, Tables 3.15 and 3.17). Each test runs in a transaction that is rolled back.
"""
import json
import uuid
from datetime import timedelta

import pytest

from coldchain.errors import InvalidState, NotPermitted
from coldchain.incidents import store
from tests.conftest import HEALTH_FACILITY, USERS

pytestmark = pytest.mark.integration
MIN = timedelta(minutes=1)


@pytest.fixture
def db(app_conn):
    return app_conn


def now(conn):
    return conn.execute("SELECT now()").fetchone()[0]   # constant within the test's transaction


def fixed_device(conn) -> str:
    return conn.execute("""INSERT INTO device (serial_no, facility_id, type, equipment_type)
                           VALUES (%s, %s, 'fixed', 'test_fridge') RETURNING id::text""",
                        (f"INC-{uuid.uuid4().hex[:10]}", HEALTH_FACILITY)).fetchone()[0]


def event(conn, device_id, type_="heat_excursion", severity="critical", start=None, end=None, detected=None) -> str:
    t = now(conn)
    return conn.execute("""
        INSERT INTO integrity_event (device_id, type, severity, event_start, event_end, detected_at)
        VALUES (%s, %s, %s, %s, %s, %s) RETURNING id::text""",
        (device_id, type_, severity, start or t - 40 * MIN, end, detected or t)).fetchone()[0]


def incident(conn, incident_id) -> dict:
    cur = conn.execute("SELECT * FROM incident WHERE id = %s", (incident_id,))
    return dict(zip([c.name for c in cur.description], cur.fetchone(), strict=True))


def actions(conn, incident_id) -> list[str]:
    return [r[0] for r in conn.execute("SELECT action FROM incident_action WHERE incident_id = %s ORDER BY id",
                                       (incident_id,)).fetchall()]


def notices(conn, incident_id) -> list[dict]:
    return [json.loads(r[0]) if isinstance(r[0], str) else r[0] for r in conn.execute(
        "SELECT payload FROM outbox WHERE topic = 'notifications' AND key = %s ORDER BY id", (incident_id,))]


def new_incident(conn, **kw) -> str:
    return store.on_event(conn, event(conn, fixed_device(conn), **kw)).incident_id


# Creation ----------------------------------------------------------------------------------------------

def test_facility_excursion_owned_by_officer_in_charge(db):
    ev = event(db, fixed_device(db))
    out = store.on_event(db, ev)
    inc = incident(db, out.incident_id)
    assert out.changed
    assert str(inc["owner_id"]) == USERS["oic"] and inc["ownership"].startswith("FR5.2")
    assert (inc["mode"], inc["severity"], inc["state"], inc["jurisdiction_id"]) == \
        ("operational", "critical", "open", "NG-KN-GWL")
    assert actions(db, out.incident_id) == ["created"]


def test_replayed_event_creates_no_second_incident(db):
    ev = event(db, fixed_device(db))
    first = store.on_event(db, ev)
    again = store.on_event(db, ev)
    assert again.incident_id == first.incident_id and not again.changed
    assert db.execute("SELECT count(*) FROM incident WHERE integrity_event_id = %s", (ev,)).fetchone()[0] == 1



def test_second_event_with_the_same_start_joins_the_existing_incident(db):
    dev = fixed_device(db)
    start = now(db) - 60 * MIN
    first = store.on_event(db, event(db, dev, start=start))
    later = store.on_event(db, event(db, dev, start=start, end=start + 20 * MIN))
    assert later.incident_id == first.incident_id
    assert db.execute("SELECT count(*) FROM integrity_event WHERE incident_id = %s",
                      (first.incident_id,)).fetchone()[0] == 2
    assert actions(db, first.incident_id) == ["created", "updated"]

def test_event_ended_15_minutes_before_detection_is_retrospective_and_copied_to_lga(db):
    t = now(db)
    inc = incident(db, new_incident(db, start=t - 3 * 60 * MIN, end=t - 15 * MIN, detected=t))
    assert inc["mode"] == "retrospective" and inc["condition_cleared"]
    assert [str(c) for c in inc["cc_ids"]] == [USERS["lga"]]


def test_event_ended_just_before_detection_is_operational(db):
    t = now(db)
    assert incident(db, new_incident(db, start=t - 60 * MIN, end=t - 14 * MIN, detected=t))["mode"] == "operational"


def test_power_loss_becoming_critical_upgrades_incident(db):
    ev = event(db, fixed_device(db), "power_loss", "warning")
    inc_id = store.on_event(db, ev).incident_id
    db.execute("UPDATE integrity_event SET severity = 'critical' WHERE id = %s", (ev,))
    assert store.on_event(db, ev).changed
    assert incident(db, inc_id)["severity"] == "critical"
    assert actions(db, inc_id) == ["created", "updated"]


def test_condition_clearing_is_recorded_and_incident_stays_open(db):
    ev = event(db, fixed_device(db))
    inc_id = store.on_event(db, ev).incident_id
    db.execute("UPDATE integrity_event SET event_end = now() WHERE id = %s", (ev,))
    store.on_event(db, ev)
    inc = incident(db, inc_id)
    assert inc["condition_cleared"] and inc["state"] == "open"   # still needs ACK, RES and closure
    assert actions(db, inc_id)[-1] == "condition_cleared"


def test_local_alarm_is_recorded_on_its_excursion_not_raised_alone(db):
    dev = fixed_device(db)
    t = now(db)
    alarm = event(db, dev, "local_alarm", "warning", start=t - 35 * MIN, end=t - 35 * MIN)
    assert store.on_event(db, alarm) is None           # no excursion yet: no incident of its own
    inc_id = store.on_event(db, event(db, dev, start=t - 40 * MIN)).incident_id
    assert db.execute("SELECT incident_id::text FROM integrity_event WHERE id = %s", (alarm,)).fetchone()[0] == inc_id
    late_alarm = event(db, dev, "local_alarm", "warning", start=t - 5 * MIN, end=t - 5 * MIN)
    store.on_event(db, late_alarm)
    assert actions(db, inc_id) == ["created", "linked", "linked"]


# Escalation steps --------------------------------------------------------------------------------------

def test_owner_notified_then_escalated_to_lga_after_15_minutes(db):
    inc_id = new_incident(db)
    t0 = incident(db, inc_id)["ack_due_from"]
    nxt, finished = store.apply_due(db, inc_id, t0)
    assert not finished and nxt == t0 + 15 * MIN
    assert incident(db, inc_id)["state"] == "notified"
    alert = notices(db, inc_id)[0]
    assert (alert["kind"], alert["recipients"], alert["urgency"]) == ("alert", [USERS["oic"]], "sms")

    store.apply_due(db, inc_id, t0 + 16 * MIN)
    inc = incident(db, inc_id)
    assert (inc["state"], inc["current_tier"]) == ("escalated", "lga")
    esc = notices(db, inc_id)[1]
    assert (esc["kind"], esc["tier"], esc["recipients"], esc["owner_id"]) == \
        ("escalation", "lga", [USERS["lga"]], USERS["oic"])


def test_steps_missed_during_an_outage_are_applied_in_order(db):
    inc_id = new_incident(db)
    t0 = incident(db, inc_id)["ack_due_from"]
    store.apply_due(db, inc_id, t0 + 70 * MIN)
    assert actions(db, inc_id) == ["created", "notified", "escalated", "escalated"]
    assert [n["tier"] for n in notices(db, inc_id)] == ["owner", "lga", "state"]


def test_warning_notifies_on_dashboard_only(db):
    inc_id = new_incident(db, type_="door_open", severity="warning")
    store.apply_due(db, inc_id, incident(db, inc_id)["ack_due_from"])
    assert notices(db, inc_id)[0]["urgency"] == "dashboard"


# Actions by people -------------------------------------------------------------------------------------

def test_acknowledgement_by_owner_and_repeat_has_no_effect(db):
    inc_id = new_incident(db)
    assert store.acknowledge(db, inc_id, USERS["oic"]).changed
    assert not store.acknowledge(db, inc_id, USERS["oic"]).changed
    inc = incident(db, inc_id)
    assert inc["state"] == "acknowledged" and str(inc["acknowledged_by"]) == USERS["oic"]
    assert actions(db, inc_id) == ["created", "acknowledged"]


def test_officer_may_act_only_once_escalated_to_their_tier(db):
    inc_id = new_incident(db)
    with pytest.raises(NotPermitted):
        store.acknowledge(db, inc_id, USERS["lga"])
    with pytest.raises(NotPermitted):
        store.acknowledge(db, inc_id, USERS["driver"])
    store.apply_due(db, inc_id, incident(db, inc_id)["ack_due_from"] + 16 * MIN)
    assert store.acknowledge(db, inc_id, USERS["lga"]).changed   # late, after escalation: accepted
    inc = incident(db, inc_id)
    assert inc["current_tier"] == "lga"                          # the escalation stands


def test_resolution_needs_a_note_and_acknowledges_if_needed(db):
    inc_id = new_incident(db)
    with pytest.raises(InvalidState):
        store.resolve(db, inc_id, USERS["oic"], "  ")
    store.resolve(db, inc_id, USERS["oic"], "moved stock to backup fridge")
    assert actions(db, inc_id) == ["created", "acknowledged", "resolved"]
    assert incident(db, inc_id)["state"] == "resolved"


def test_closure_by_reviewing_officer_who_did_not_resolve(db):
    inc_id = new_incident(db)
    store.apply_due(db, inc_id, incident(db, inc_id)["ack_due_from"] + 16 * MIN)
    with pytest.raises(InvalidState):                    # no transition from open to closed
        store.close(db, inc_id, USERS["lga"])
    store.resolve(db, inc_id, USERS["lga"], "repaired thermostat")
    with pytest.raises(NotPermitted):                    # the resolver cannot review their own work
        store.close(db, inc_id, USERS["lga"])
    with pytest.raises(NotPermitted):                    # the owner is not a reviewing officer
        store.close(db, inc_id, USERS["oic"])
    store.close(db, inc_id, USERS["state"], "checked")
    assert incident(db, inc_id)["state"] == "closed"
    with pytest.raises(InvalidState):
        store.acknowledge(db, inc_id, USERS["oic"])


def test_owner_may_close_a_warning(db):
    inc_id = new_incident(db, type_="door_open", severity="warning")
    store.resolve(db, inc_id, USERS["oic"], "door closed")
    store.close(db, inc_id, USERS["oic"])
    assert incident(db, inc_id)["state"] == "closed"


def test_reviewer_reopening_restarts_resolution_clock(db):
    inc_id = new_incident(db)
    store.resolve(db, inc_id, USERS["oic"], "door closed")
    store.reopen(db, inc_id, USERS["lga"], "fridge still warm on inspection")
    inc = incident(db, inc_id)
    assert (inc["state"], inc["reopen_count"], inc["resolve_stage"]) == ("acknowledged", 1, 0)
    assert inc["resolve_due_from"] is not None and inc["resolved_at"] is None


def test_recurrence_within_60_minutes_reopens_and_renotifies(db):
    dev = fixed_device(db)
    t = now(db)
    inc_id = store.on_event(db, event(db, dev, start=t - 90 * MIN, end=t - 50 * MIN)).incident_id
    store.apply_due(db, inc_id, incident(db, inc_id)["ack_due_from"])
    store.resolve(db, inc_id, USERS["oic"], "door closed")
    again = event(db, dev, start=t + 30 * MIN)          # 30 minutes after resolution
    out = store.on_event(db, again)
    assert out.incident_id == inc_id and out.changed
    inc = incident(db, inc_id)
    assert (inc["state"], inc["reopen_count"], inc["ack_stage"]) == ("open", 1, -1)
    store.apply_due(db, inc_id, inc["ack_due_from"])
    assert [n["kind"] for n in notices(db, inc_id)] == ["alert", "review", "alert"]


def test_condition_after_60_minutes_is_a_new_incident(db):
    dev = fixed_device(db)
    t = now(db)
    inc_id = store.on_event(db, event(db, dev, start=t - 90 * MIN, end=t - 50 * MIN)).incident_id
    store.resolve(db, inc_id, USERS["oic"], "door closed")
    assert store.on_event(db, event(db, dev, start=t + 61 * MIN)).incident_id != inc_id


def test_every_change_is_hash_chained(db):
    inc_id = new_incident(db)
    store.acknowledge(db, inc_id, USERS["oic"])
    rows = db.execute("SELECT prev_hash, hash FROM incident_action WHERE incident_id = %s ORDER BY id",
                      (inc_id,)).fetchall()
    assert all(len(h) == 32 for _, h in rows)
    assert rows[1][0] == rows[0][1]
