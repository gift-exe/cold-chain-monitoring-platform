"""I6 acceptance (Table 3.2): each role sees only its jurisdiction (Table 3.21); stale data is marked
(Section 3.4.7); response metrics are computed (FR10.3); administrative changes are restricted and
recorded (FR11). Read models tested in the database; each test is rolled back.
"""
import uuid
from datetime import timedelta

import pytest

from coldchain import admin, custody
from coldchain.api import views
from coldchain.errors import NotPermitted
from coldchain.incidents import store
from tests.conftest import HEALTH_FACILITY, PRODUCT, USERS
from tests.test_incidents import event, now

pytestmark = pytest.mark.integration
MIN = timedelta(minutes=1)
DALA_PHC = "8a3c1f52-2b7e-4c1d-9d6a-0f4e5b6c7e21"          # demo network (seeds/dev_z_demo_network.sql)


@pytest.fixture
def db(app_conn):
    return app_conn


def device(db, facility=HEALTH_FACILITY, interval_s=60) -> str:
    return db.execute("""INSERT INTO device (serial_no, facility_id, type, equipment_type, reporting_interval_s)
                         VALUES (%s, %s, 'fixed', 'test_fridge', %s) RETURNING id::text""",
                      (f"DASH-{uuid.uuid4().hex[:10]}", facility, interval_s)).fetchone()[0]


def new_incident(db, facility=HEALTH_FACILITY, **kw) -> str:
    return store.on_event(db, event(db, device(db, facility), **kw)).incident_id


def seen(db, who: str, state="active") -> set[str]:
    return {i["id"] for i in views.incidents(db, views.viewer(db, USERS[who]), state)}


def test_each_role_sees_only_its_jurisdiction(db):
    gwale, dala = new_incident(db), new_incident(db, DALA_PHC)
    for who, expected in [("lga", {gwale}), ("oic", {gwale}), ("state", {gwale, dala}),
                          ("national", {gwale, dala}), ("outreach", set()), ("driver", set()), ("admin", set())]:
        assert seen(db, who) & {gwale, dala} == expected, who


def test_facility_officer_sees_own_facility_only(db):
    other_gwale_phc = "8a3c1f52-2b7e-4c1d-9d6a-0f4e5b6c7e12"
    own, other = new_incident(db), new_incident(db, other_gwale_phc)
    assert own in seen(db, "oic") and other not in seen(db, "oic")
    assert other in seen(db, "lga")
    m = views.map_units(db, views.viewer(db, USERS["oic"]))
    assert [f["id"] for f in m["facilities"]] == [HEALTH_FACILITY]


def test_outreach_lead_sees_incidents_on_consignments_they_handled(db):
    t = now(db) - 30 * MIN
    cid = custody.create(db, USERS["lga"], PRODUCT, 40, HEALTH_FACILITY, "Outreach", 60, None, t)
    custody.release(db, USERS["lga"], cid, USERS["outreach"], t)
    custody.confirm(db, USERS["outreach"], cid, t + MIN)
    ev = db.execute("""INSERT INTO integrity_event (consignment_id, type, severity, event_start, detected_at)
                       VALUES (%s, 'heat_excursion', 'critical', %s, %s) RETURNING id::text""",
                    (cid, t + 5 * MIN, now(db))).fetchone()[0]
    inc = store.on_event(db, ev).incident_id
    assert inc in seen(db, "outreach") and inc not in seen(db, "driver")
    mine = {c["id"]: c for c in views.consignments(db, views.viewer(db, USERS["outreach"]))}
    assert mine[cid]["can_complete"] and mine[cid]["holder_id"] == USERS["outreach"]
    assert cid not in {c["id"] for c in views.consignments(db, views.viewer(db, USERS["driver"]))}


def test_incident_detail_offers_only_permitted_actions(db):
    inc = new_incident(db)
    owner = views.incident(db, views.viewer(db, USERS["oic"]), inc)
    lga = views.incident(db, views.viewer(db, USERS["lga"]), inc)
    assert (owner["can_act"], owner["can_review"]) == (True, False)
    assert lga["can_act"] is False                                    # not escalated to the LGA yet
    store.resolve(db, inc, USERS["oic"], "moved stock")
    assert views.incident(db, views.viewer(db, USERS["lga"]), inc)["can_review"]
    assert not views.incident(db, views.viewer(db, USERS["oic"]), inc)["can_review"]   # not their own
    with pytest.raises(NotPermitted):
        views.incident(db, views.viewer(db, USERS["driver"]), inc)


def test_active_list_puts_critical_first_then_oldest(db):
    t = now(db)
    warning = new_incident(db, severity="warning", start=t - 90 * MIN)
    old = new_incident(db, start=t - 60 * MIN)
    ids = [i["id"] for i in views.incidents(db, views.viewer(db, USERS["lga"])) if i["id"] in {warning, old}]
    assert ids == [old, warning]


def test_stale_and_silent_units_are_marked(db):
    fresh, stale, never = device(db), device(db), device(db)
    db.execute("""INSERT INTO device_status (device_id, last_event_time, last_received_at) VALUES
                  (%s, now(), now()), (%s, now() - interval '4 minutes', now() - interval '4 minutes')""",
               (fresh, stale))
    units = {u["id"]: u for f in views.map_units(db, views.viewer(db, USERS["lga"]))["facilities"]
             for u in f["units"]}
    assert (units[fresh]["status"], units[stale]["status"], units[never]["status"]) == ("ok", "stale", "no_data")


def test_facility_status_is_its_worst_unit(db):
    new_incident(db)
    gwale = next(f for f in views.map_units(db, views.viewer(db, USERS["lga"]))["facilities"]
                 if f["id"] == HEALTH_FACILITY)
    assert gwale["status"] == "critical"


def test_response_metrics_by_facility_and_tier(db):
    t = now(db)
    a, b = new_incident(db), new_incident(db)
    db.execute("UPDATE incident SET created_at = %s WHERE id = %s", (t - 30 * MIN, a))
    db.execute("UPDATE incident SET created_at = %s WHERE id = %s", (t - 20 * MIN, b))
    store.acknowledge(db, a, USERS["oic"])                     # 30 minutes after creation
    store.acknowledge(db, b, USERS["oic"])                     # 20 minutes
    store.resolve(db, a, USERS["oic"], "fixed")
    v = views.viewer(db, USERS["lga"])
    rows = views.performance(db, v, t - timedelta(hours=1), t + MIN, "facility")["rows"]
    gwale = next(r for r in rows if r["key"] == HEALTH_FACILITY)
    assert gwale["incidents"] >= 2 and gwale["acknowledged"] >= 2
    by_tier = {r["key"]: r for r in views.performance(db, v, t - timedelta(hours=1), t + MIN, "tier")["rows"]}
    assert "owner" in by_tier


def test_product_limits_changed_by_national_officer_only_and_recorded(db):
    with pytest.raises(NotPermitted):
        admin.update_product(db, USERS["lga"], PRODUCT, {"heat_tolerance_min": 45})
    with pytest.raises(NotPermitted):
        admin.update_product(db, USERS["admin"], PRODUCT, {"heat_tolerance_min": 45})
    after = admin.update_product(db, USERS["national"], PRODUCT, {"heat_tolerance_min": 45})
    assert after["heat_tolerance_min"] == 45
    rec = db.execute("""SELECT action, before, after, hash IS NOT NULL FROM admin_action
                        WHERE target_id = %s ORDER BY id DESC LIMIT 1""", (PRODUCT,)).fetchone()
    assert rec[0] == "product_updated" and rec[2] == {"heat_tolerance_min": 45} and rec[3]


def test_phone_changes_by_administrator_only_and_recorded(db):
    with pytest.raises(NotPermitted):
        admin.deactivate_phone(db, USERS["lga"], USERS["oic"])
    with pytest.raises(NotPermitted):                      # not NotFound: non-admins cannot probe usernames
        admin.deactivate_phone(db, USERS["lga"], "no.such.user")
    admin.deactivate_phone(db, USERS["admin"], USERS["oic"])
    assert db.execute("SELECT phone_deactivated_at IS NOT NULL FROM app_user WHERE id = %s",
                      (USERS["oic"],)).fetchone()[0]
    actions = [r[0] for r in db.execute("SELECT action FROM admin_action WHERE target_id = %s ORDER BY id",
                                        (USERS["oic"],))]
    assert actions[-1] == "phone_deactivated"
