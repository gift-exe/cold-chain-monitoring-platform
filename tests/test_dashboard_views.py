"""I6 acceptance (Table 3.2): each role sees only its jurisdiction (Table 3.21); stale data is marked
(Section 3.4.7); response metrics are computed (FR10.3); administrative changes are restricted and
recorded (FR11). Read models tested in the database; each test is rolled back.
"""
import uuid
from datetime import timedelta

import pytest

from coldchain import admin, custody
from coldchain.api import views
from coldchain.errors import InvalidState, NotPermitted
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
    f = m["facilities"][0]
    assert (f["ward"], f["lga_name"], f["state_name"]) == ("Sani Mainagge", "Gwale", "Kano")


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
    assert (mine[cid]["open_incidents"], mine[cid]["severity"], mine[cid]["status"]) == (1, "critical", None)
    assert mine[cid]["holder_role"] == "outreach_lead" and mine[cid]["held_since"] is not None
    detail = views.consignment(db, views.viewer(db, USERS["outreach"]), cid)
    assert [i["type"] for i in detail["incidents"]] == ["heat_excursion"]
    assert detail["location"] is None and detail["readings"] == []          # no logger with it


def test_shipment_goes_store_to_facility_to_outreach_team_in_one_record(db):
    t = now(db) - 30 * MIN
    lga = views.viewer(db, USERS["lga"])
    assert {"facility_oic", "outreach_lead"} <= {r["role"] for r in views.receivers(db, lga)}
    oic = next(r for r in views.receivers(db, lga) if r["id"] == USERS["oic"])
    assert oic["facility_name"]                                # chosen by facility, not just by name
    cid = custody.create(db, USERS["lga"], PRODUCT, 40, HEALTH_FACILITY, "Outreach", 60, None, t)
    custody.release(db, USERS["lga"], cid, USERS["oic"], t)                 # store -> health facility
    custody.confirm(db, USERS["oic"], cid, t + MIN)
    custody.release(db, USERS["oic"], cid, USERS["outreach"], t + 2 * MIN)  # facility -> outreach team
    custody.confirm(db, USERS["outreach"], cid, t + 3 * MIN)
    d = views.consignment(db, views.viewer(db, USERS["outreach"]), cid)
    assert [e["type"] for e in d["events"]] == ["create", "release", "confirm", "release", "confirm"]
    assert d["holder_id"] == USERS["outreach"]
    assert cid in {c["id"] for c in views.consignments(db, views.viewer(db, USERS["oic"]))}


def test_shipment_figures_and_breakdown_by_area(db):
    t = now(db)
    def figures(who):
        return views.shipment_summary(db, views.viewer(db, USERS[who]))
    before, before_state = figures("lga")["overall"], figures("state")["overall"]
    a = custody.create(db, USERS["lga"], PRODUCT, 40, HEALTH_FACILITY, "Outreach", 60, None, t)
    custody.release(db, USERS["lga"], a, USERS["oic"], t - 120 * MIN)       # deadline long past
    b = custody.create(db, USERS["lga"], PRODUCT, 40, HEALTH_FACILITY, "Outreach", 60, None, t)
    custody.release(db, USERS["lga"], b, USERS["outreach"], t)
    after = figures("lga")["overall"]
    delta = {k: after[k] - before[k] for k in ("in_transit", "awaiting", "overdue")}
    assert delta == {"in_transit": 2, "awaiting": 2, "overdue": 1}
    assert figures("oic")["overall"]["for_you"] >= 1 and figures("oic")["group"] is None
    state = figures("state")
    assert state["group"] == "lga" and state["overall"]["in_transit"] - before_state["in_transit"] == 2
    gwale = next(r for r in state["rows"] if r["label"] == "Gwale")
    assert gwale["awaiting"] >= 2
    assert figures("national")["group"] == "state" and figures("lga")["group"] == "facility"


def test_shipment_details_show_its_temperature_and_where_it_is(db):
    t = now(db) - 20 * MIN
    dev = db.execute("""INSERT INTO device (serial_no, type, equipment_type, reporting_interval_s)
                        VALUES (%s, 'mobile', 'vaccine_carrier', 60) RETURNING id::text""",
                     (f"DASH-M-{uuid.uuid4().hex[:8]}",)).fetchone()[0]
    serial = db.execute("SELECT serial_no FROM device WHERE id = %s", (dev,)).fetchone()[0]
    cid = custody.create(db, USERS["lga"], PRODUCT, 40, HEALTH_FACILITY, "Outreach", 60, dev, t)
    for k in range(5):
        db.execute("""INSERT INTO reading (device_id, seq_no, event_time, received_at, temperature, location)
                      VALUES (%s, %s, %s, %s, %s, ST_MakePoint(8.5 + %s * 0.001, 12.0)::geography)""",
                   (dev, k, t + k * MIN, t + k * MIN, 5 + k * 0.5, k))
    db.execute("""INSERT INTO device_status (device_id, last_event_time, last_received_at)
                  VALUES (%s, now(), now())""", (dev,))
    lga = views.viewer(db, USERS["lga"])
    row = next(c for c in views.consignments(db, lga) if c["id"] == cid)
    assert row["logger"] == serial and float(row["temperature"]) == 7.0 and row["status"] == "ok"
    d = views.consignment(db, lga, cid)
    assert d["location"]["kind"] == "mobile" and len(d["location"]["track"]) == 5
    assert len(d["readings"]) == 5 and float(d["readings"][-1]["temp"]) == 7.0


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


def test_incident_details_say_where_it_is(db):
    inc = views.incident(db, views.viewer(db, USERS["lga"]), new_incident(db))
    assert inc["location"]["kind"] == "facility" and inc["location"]["lat"] is not None
    assert inc["facility_ward"] == "Sani Mainagge"


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


def test_summary_counts_active_incidents_and_responses_in_the_area(db):
    def counts(who):
        return views.summary(db, views.viewer(db, USERS[who]))
    before, before_state = counts("lga"), counts("state")
    critical = new_incident(db)
    new_incident(db, severity="warning")
    new_incident(db, DALA_PHC)                                        # another LGA: state sees it, LGA not
    store.acknowledge(db, critical, USERS["oic"])
    resolved = new_incident(db)
    store.resolve(db, resolved, USERS["oic"], "moved stock")
    after = counts("lga")
    delta = {k: after[k] - before[k] for k in ("active", "critical", "warning", "awaiting", "raised",
                                               "responded", "resolved")}
    assert delta == {"active": 2, "critical": 1, "warning": 1, "awaiting": 1, "raised": 3, "responded": 2,
                     "resolved": 1}
    assert counts("state")["active"] - before_state["active"] == 3
    assert counts("admin")["active"] == 0
    assert "summary" in views.map_units(db, views.viewer(db, USERS["lga"]))


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


def test_response_metrics_overall_trend_and_by_responsible_officer(db):
    t = now(db)
    a = new_incident(db)
    new_incident(db)
    db.execute("UPDATE incident SET created_at = %s WHERE id = %s", (t - 30 * MIN, a))
    store.acknowledge(db, a, USERS["oic"])                     # acknowledged by its owner, before escalation
    v = views.viewer(db, USERS["lga"])
    since, until = t - timedelta(days=30), t + MIN
    p = views.performance(db, v, since, until, "officer")
    owner = db.execute("SELECT owner_id::text FROM incident WHERE id = %s", (a,)).fetchone()[0]
    row = next(r for r in p["rows"] if r["key"] == owner)
    assert row["incidents"] >= 2 and row["before_escalation"] >= 1 and row["covers"]
    assert p["overall"]["incidents"] >= 2 and p["overall"]["acknowledged"] >= 1
    assert p["bucket"] == "week" and len(p["trend"]) >= 4
    assert sum(w["incidents"] for w in p["trend"]) == p["overall"]["incidents"]
    assert p["trend"][-1]["incidents"] >= 2
    with pytest.raises(NotPermitted):                          # officers compared by supervisors only
        views.performance(db, views.viewer(db, USERS["oic"]), since, until, "officer")
    assert views.performance(db, v, t - timedelta(days=7), until, "facility")["bucket"] == "day"
    assert p["limits"] == {"critical": 15, "warning": 1440}   # Table 3.15: owner's time before escalation
    assert p["previous"]["incidents"] >= 0
    assert p["trend"][-1]["critical"] >= 2 and "mtta_critical" in p["trend"][-1]


def test_incident_list_narrowed_to_an_area_or_owner(db):
    gwale, dala = new_incident(db), new_incident(db, DALA_PHC)
    state = views.viewer(db, USERS["state"])
    ids = lambda **kw: {i["id"] for i in views.incidents(db, state, **kw)}
    gwale_lga = db.execute("SELECT jurisdiction_id FROM incident WHERE id = %s", (gwale,)).fetchone()[0]
    assert gwale in ids(area=gwale_lga) and dala not in ids(area=gwale_lga)
    kano = db.execute("SELECT parent_id FROM jurisdiction WHERE id = %s", (gwale_lga,)).fetchone()[0]
    assert {gwale, dala} <= ids(area=kano)                     # a state includes its LGAs
    owner = db.execute("SELECT owner_id::text FROM incident WHERE id = %s", (gwale,)).fetchone()[0]
    assert gwale in ids(owner=owner) and dala not in ids(owner=owner)
    lga = views.viewer(db, USERS["lga"])                       # narrows the scope, never widens it
    assert dala not in {i["id"] for i in views.incidents(db, lga, area=kano)}


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


def test_product_added_by_national_officer_only_and_recorded(db):
    new = {"name": "Test vaccine E9", "lower_limit": 2, "upper_limit": 8, "freeze_limit": 0.5,
           "heat_tolerance_min": 60, "freeze_tolerance_min": 10, "freeze_sensitive": False}
    for who in ("lga", "admin"):
        with pytest.raises(NotPermitted):
            admin.create_product(db, USERS[who], new)
    p = admin.create_product(db, USERS["national"], new)
    assert next(x for x in admin.products(db) if x["id"] == p["id"])["heat_tolerance_min"] == 60
    assert db.execute("SELECT action FROM admin_action WHERE target_id = %s", (p["id"],)).fetchone()[0] \
        == "product_created"
    with pytest.raises(InvalidState):                          # names are unique
        admin.create_product(db, USERS["national"], new)
    with pytest.raises(InvalidState):                          # upper limit above the others
        admin.create_product(db, USERS["national"], {**new, "name": "Other", "upper_limit": 1})


def test_text_messages_sent_and_received_shown_to_administrator_only(db):
    db.execute("""INSERT INTO notification (request_id, kind, channel, recipient_id, phone_number, body, status)
                  VALUES (gen_random_uuid(), 'alert', 'sms', %s, '+2348000000004', 'E9 test alert', 'delivered')""",
               (USERS["oic"],))
    db.execute("""INSERT INTO sms_inbound (gateway_id, from_number, text, user_id, command, outcome)
                  VALUES (%s, '+2348000000004', 'ACK 1', %s, 'ACK', 'accepted')""", (uuid.uuid4().hex, USERS["oic"]))
    with pytest.raises(NotPermitted):
        admin.sms_log(db, USERS["lga"])
    log = admin.sms_log(db, USERS["admin"])
    sent = next(m for m in log if m["text"] == "E9 test alert")
    received = next(m for m in log if m["text"] == "ACK 1")
    assert (sent["direction"], sent["full_name"], received["direction"]) == ("sent", "Test Facility OIC", "received")


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
