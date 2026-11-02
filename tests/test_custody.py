"""Custody handoffs and incident ownership in transit (Table 3.16, CR1-CR9; test E6 scenarios: normal,
incomplete, rejected and delayed handoffs, and retrospective events). Each test is rolled back.

Scenario: the officer-in-charge of a health facility (OIC) releases a consignment, with its logger, to
an outreach lead for an outreach session. Expected transit 60 minutes, so the confirmation window ends
90 minutes after release (CR4).
"""
import uuid
from datetime import timedelta

import pytest
from psycopg import errors

from coldchain import custody
from coldchain.detection.store import sweep_silence
from coldchain.errors import InvalidState, NotPermitted
from coldchain.incidents import ownership, store
from tests.conftest import HEALTH_FACILITY, PRODUCT, USERS

pytestmark = pytest.mark.integration
MIN = timedelta(minutes=1)
OIC, OUTREACH, LGA = USERS["oic"], USERS["outreach"], USERS["lga"]


@pytest.fixture
def db(app_conn):
    return app_conn


@pytest.fixture
def t0(db):
    return db.execute("SELECT now() - interval '6 hours'").fetchone()[0]


def logger(conn, sync_window_min=1440) -> str:
    return conn.execute("""INSERT INTO device (serial_no, type, equipment_type, sync_window_min)
                           VALUES (%s, 'mobile', 'vaccine_carrier', %s) RETURNING id::text""",
                        (f"CUS-{uuid.uuid4().hex[:10]}", sync_window_min)).fetchone()[0]


def consignment(conn, t, device=None) -> str:
    return custody.create(conn, OIC, PRODUCT, 200, HEALTH_FACILITY, "Outreach: Kofar Ruwa", 60, device, t)


def gap_events(conn, cid) -> list[tuple]:
    return conn.execute("""SELECT event_start, event_end, details->>'reason' FROM integrity_event
                           WHERE consignment_id = %s AND type = 'custody_gap' ORDER BY event_start""",
                        (cid,)).fetchall()


def excursion(conn, device, start) -> dict:
    ev = conn.execute("""INSERT INTO integrity_event (device_id, type, severity, event_start, detected_at)
                         VALUES (%s, 'heat_excursion', 'critical', %s, now()) RETURNING id::text""",
                      (device, start)).fetchone()[0]
    inc = store.on_event(conn, ev).incident_id
    return dict(zip(("owner", "cc", "ownership", "consignment"), conn.execute(
        "SELECT owner_id::text, cc_ids::text[], ownership, consignment_id::text FROM incident WHERE id = %s",
        (inc,)).fetchone(), strict=True))


# Handoffs ----------------------------------------------------------------------------------------------

def test_normal_handoff_transfers_custody_when_confirmation_received(db, t0):
    """CR1-CR3: the releaser holds the consignment until the receiver confirms; transfer takes effect at
    the time the confirmation is received."""
    cid = consignment(db, t0)
    assert custody.state(db, cid) == "held" and custody.holder(db, cid) == OIC
    h = custody.release(db, OIC, cid, OUTREACH, t0 + 10 * MIN)
    assert h.window_ends_at == t0 + 10 * MIN + 90 * MIN
    assert custody.state(db, cid) == "in_handoff" and custody.holder(db, cid) == OIC
    custody.confirm(db, OUTREACH, cid, t0 + 70 * MIN)
    assert custody.state(db, cid) == "held"
    assert ownership.custodian_at(db, cid, t0 + 69 * MIN) == OIC
    assert ownership.custodian_at(db, cid, t0 + 70 * MIN) == OUTREACH
    assert [r[0] for r in db.execute("SELECT type FROM custody_event WHERE consignment_id = %s ORDER BY seq",
                                     (cid,))] == ["create", "release", "confirm"]
    assert gap_events(db, cid) == []


def test_only_holder_releases_and_only_receiver_confirms(db, t0):
    cid = consignment(db, t0)
    with pytest.raises(NotPermitted):
        custody.release(db, OUTREACH, cid, LGA, t0)
    with pytest.raises(NotPermitted):                 # administrators do not hold consignments
        custody.release(db, OIC, cid, USERS["admin"], t0)
    custody.release(db, OIC, cid, OUTREACH, t0 + MIN)
    with pytest.raises(InvalidState):                 # one handoff at a time
        custody.release(db, OIC, cid, LGA, t0 + 2 * MIN)
    with pytest.raises(NotPermitted):
        custody.confirm(db, USERS["driver"], cid, t0 + 3 * MIN)


def test_incomplete_handoff_raises_custody_gap_when_window_expires(db, t0):
    """CR4: not confirmed within transit time + 30 minutes: custody-gap warning to the releaser, copied to
    the LGA officer."""
    cid = consignment(db, t0)
    h = custody.release(db, OIC, cid, OUTREACH, t0)
    assert custody.expire(db, h.id, h.window_ends_at - MIN) is None      # too early
    gap = custody.expire(db, h.id, h.window_ends_at)
    assert gap_events(db, cid) == [(h.window_ends_at, None, "handoff not confirmed within its window")]
    assert custody.expire(db, h.id, h.window_ends_at + MIN) is None      # raised once
    inc = store.on_event(db, gap).incident_id
    owner, cc, severity = db.execute("SELECT owner_id::text, cc_ids::text[], severity FROM incident WHERE id = %s",
                                     (inc,)).fetchone()
    assert (owner, cc, severity) == (OIC, [LGA], "warning")


def test_delayed_confirmation_ends_the_gap_and_transfers_at_confirmation(db, t0):
    cid = consignment(db, t0)
    h = custody.release(db, OIC, cid, OUTREACH, t0)
    custody.expire(db, h.id, h.window_ends_at)
    custody.confirm(db, OUTREACH, cid, h.window_ends_at + 20 * MIN)
    assert gap_events(db, cid)[0][1] == h.window_ends_at + 20 * MIN
    assert ownership.custodian_at(db, cid, h.window_ends_at + 19 * MIN) == OIC
    delayed = db.execute("SELECT details->>'delayed' FROM custody_event WHERE consignment_id = %s AND type = 'confirm'",
                         (cid,)).fetchone()[0]
    assert delayed == "true"


def test_rejected_handoff_keeps_custody_with_releaser_and_raises_gap(db, t0):
    """CR5: the receiver refuses (e.g. it arrived warm): custody stays with the releaser, the reason is
    recorded and a custody gap is raised; it ends when the releaser takes the consignment back."""
    cid = consignment(db, t0)
    custody.release(db, OIC, cid, OUTREACH, t0)
    with pytest.raises(InvalidState):
        custody.reject(db, OUTREACH, cid, " ", t0 + 50 * MIN)
    custody.reject(db, OUTREACH, cid, "carrier warm on arrival", t0 + 50 * MIN)
    assert custody.state(db, cid) == "rejected" and custody.holder(db, cid) == OIC
    assert gap_events(db, cid) == [(t0 + 50 * MIN, None, "handoff rejected")]
    with pytest.raises(InvalidState):                 # settle the rejection before releasing again
        custody.release(db, OIC, cid, LGA, t0 + 60 * MIN)
    custody.cancel(db, OIC, cid, "returned to facility fridge", t0 + 80 * MIN)
    assert custody.state(db, cid) == "held"
    assert gap_events(db, cid)[0][1] == t0 + 80 * MIN
    custody.release(db, OIC, cid, LGA, t0 + 90 * MIN)


def test_completion_ends_custody_and_logger_assignment(db, t0):
    dev = logger(db)
    cid = consignment(db, t0, dev)
    custody.complete(db, OIC, cid, t0 + 5 * 60 * MIN)
    assert custody.state(db, cid) == "completed"
    assert ownership.assignment_at(db, dev, t0 + 5 * 60 * MIN) is None
    assert ownership.assignment_at(db, dev, t0 + 60 * MIN) == cid


def test_logger_assigned_to_one_consignment_at_a_time(db, t0):
    """CR8."""
    dev = logger(db)
    consignment(db, t0, dev)
    with pytest.raises(InvalidState):
        consignment(db, t0 + MIN, dev)


def test_custody_log_is_append_only_and_hash_chained(db, t0):
    """CR9."""
    cid = consignment(db, t0)
    custody.release(db, OIC, cid, OUTREACH, t0)
    rows = db.execute("SELECT prev_hash, hash FROM custody_event WHERE consignment_id = %s ORDER BY seq",
                      (cid,)).fetchall()
    assert rows[1][0] == rows[0][1]
    with pytest.raises(errors.InsufficientPrivilege):
        db.execute("UPDATE custody_event SET type = 'confirm' WHERE consignment_id = %s", (cid,))


# Ownership of events in transit --------------------------------------------------------------------------

def test_event_during_handoff_owned_by_releaser_with_receiver_copied(db, t0):
    """CR2, CR6."""
    dev = logger(db)
    cid = consignment(db, t0, dev)
    custody.release(db, OIC, cid, OUTREACH, t0 + 10 * MIN)
    inc = excursion(db, dev, t0 + 30 * MIN)
    assert (inc["owner"], inc["cc"], inc["consignment"]) == (OIC, [OUTREACH], cid)
    assert inc["ownership"].startswith("CR2")


def test_event_after_confirmation_owned_by_receiver(db, t0):
    dev = logger(db)
    cid = consignment(db, t0, dev)
    custody.release(db, OIC, cid, OUTREACH, t0)
    custody.confirm(db, OUTREACH, cid, t0 + 60 * MIN)
    inc = excursion(db, dev, t0 + 2 * 60 * MIN)
    assert (inc["owner"], inc["cc"]) == (OUTREACH, [])
    assert inc["ownership"].startswith("CR6")


def test_retrospective_event_owned_by_custodian_at_its_start_not_at_detection(db, t0):
    """CR6: detected now, after the transfer, but it began before the transfer: the releaser owns it."""
    dev = logger(db)
    cid = consignment(db, t0, dev)
    custody.release(db, OIC, cid, OUTREACH, t0 + 10 * MIN)
    custody.confirm(db, OUTREACH, cid, t0 + 60 * MIN)
    inc = excursion(db, dev, t0 + 5 * MIN)        # began before release; detected hours later
    assert inc["owner"] == OIC


def test_event_with_no_custodian_goes_to_lga_officer_and_raises_custody_gap(db, t0):
    """CR7: the logger was assigned before anyone recorded custody."""
    dev = logger(db)
    cid = consignment(db, t0)
    db.execute("INSERT INTO device_assignment (device_id, consignment_id, valid_from) VALUES (%s, %s, %s)",
               (dev, cid, t0 - 60 * MIN))
    inc = excursion(db, dev, t0 - 30 * MIN)
    assert inc["owner"] == LGA and inc["ownership"].startswith("CR7")
    assert gap_events(db, cid) == [(t0 - 30 * MIN, t0 - 30 * MIN, "no custodian at the start of an event")]


# Device silence for mobile loggers -----------------------------------------------------------------------

def test_assigned_mobile_logger_silent_after_its_sync_window(db, t0):
    quiet = logger(db, sync_window_min=60)
    unassigned = logger(db, sync_window_min=60)
    consignment(db, t0, quiet)
    started = {e["device_id"] for e in sweep_silence(db) if e["change"] == "created"}
    assert quiet in started and unassigned not in started
