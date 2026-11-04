"""Notifications in the database (FR9.1, FR9.3, Table 3.20): requests become SMS and dashboard
notifications; SMS are sent, retried, timed out, digested; delivery failures escalate. Each test is rolled
back. The gateway is replaced by a fake that answers as Africa's Talking does, so time can be controlled
(the emulator is used in the end-to-end tests).
"""
import json
from datetime import timedelta

import httpx
import psycopg
import pytest

from coldchain.config import get_settings
from coldchain.incidents import store as incidents
from coldchain.notify import codes, gateway, store
from tests.conftest import HEALTH_FACILITY, PRODUCT, USERS
from tests.test_incidents import actions, incident, new_incident, notices

pytestmark = pytest.mark.integration
MIN = timedelta(minutes=1)
PHONES = {"oic": "+2348000000004", "lga": "+2348000000003", "outreach": "+2348000000005"}


class FakeGateway:
    """Answers sends like Africa's Talking; `refuse` makes a number invalid at send time."""

    def __init__(self):
        self.sent: list[dict] = []
        self.refuse: set[str] = set()

    def handler(self, request: httpx.Request) -> httpx.Response:
        form = dict(httpx.QueryParams(request.content.decode()))
        self.sent.append(form)
        n = len(self.sent)
        if form["to"] in self.refuse:
            rec = {"statusCode": 403, "number": form["to"], "status": "InvalidPhoneNumber", "messageId": "None"}
        else:
            rec = {"statusCode": 101, "number": form["to"], "status": "Success", "messageId": f"ATXid_{n}"}
        return httpx.Response(201, json={"SMSMessageData": {"Message": "Sent", "Recipients": [rec]}})

    def to(self, phone) -> list[str]:
        return [m["message"] for m in self.sent if m["to"] == phone]


@pytest.fixture
def gw(monkeypatch):
    monkeypatch.setattr(get_settings(), "sms_redirect_to", None)     # whatever .env says
    monkeypatch.setattr(get_settings(), "sms_trust_sent", False)
    g = FakeGateway()
    g.client = httpx.Client(transport=httpx.MockTransport(g.handler))
    return g


@pytest.fixture
def db(app_conn):
    return app_conn


def now(db):
    return db.execute("SELECT now()").fetchone()[0]


def alerted(db, **kw) -> tuple[str, dict]:
    """A new critical incident with its owner alert requested (the policy's first step)."""
    inc = new_incident(db, **kw)
    incidents.apply_due(db, inc, now(db))
    return inc, notices(db, inc)[-1]


def sms(db, incident_id=None, **where) -> list[dict]:
    cond = " AND ".join(f"{k} = %s" for k in where)
    cur = db.execute(f"""SELECT * FROM notification WHERE channel = 'sms' {'AND incident_id = %s' if incident_id else ''}
                         {'AND ' + cond if cond else ''} ORDER BY created_at, id""",
                     ([incident_id] if incident_id else []) + list(where.values()))
    return [dict(zip([c.name for c in cur.description], r, strict=True)) for r in cur.fetchall()]


def report(db, n: dict, status, reason=None, at=None):
    return store.on_delivery_report(db, n["gateway_message_id"], status, reason, at or now(db))


# Requests ------------------------------------------------------------------------------------------------

def test_alert_goes_to_the_owner_by_sms_and_dashboard_and_is_sent_once(db, gw):
    inc, req = alerted(db)
    store.expand(db, req, now(db))
    store.expand(db, req, now(db))                   # the same request delivered twice by Kafka
    rows = db.execute("SELECT channel, kind, recipient_id::text, status FROM notification WHERE incident_id = %s "
                      "ORDER BY channel", (inc,)).fetchall()
    assert rows == [("dashboard", "alert", USERS["oic"], "posted"), ("sms", "alert", USERS["oic"], "queued")]
    attempted, _ = store.dispatch(db, now(db), gw.client)
    assert attempted == 1
    (text,) = gw.to(PHONES["oic"])
    no = incident(db, inc)["incident_no"]
    assert text.startswith("CRIT HEAT Gwale Test Health Facility test fridge:") and f"RES {no} " in text
    stored = sms(db, inc)[0]
    assert stored["status"] == "sent" and "****" in stored["body"] and stored["body"] != text   # code masked
    code = text.split()[-2]
    assert codes.check(db, USERS["oic"], code, now(db), incident_id=inc) is None            # the code works


def test_warning_is_dashboard_only_and_retrospective_waits_for_the_digest(db, gw):
    _, warn = alerted(db, severity="warning")
    store.expand(db, warn, now(db))
    retro_inc, retro = alerted(db, end=now(db) - 30 * MIN)        # ended 30 minutes before detection
    store.expand(db, retro, now(db))
    assert sms(db, retro_inc)[0]["status"] == "held"
    assert db.execute("SELECT count(*) FROM notification WHERE request_id = %s AND channel = 'sms'",
                      (warn["id"],)).fetchone()[0] == 0
    assert store.dispatch(db, now(db), gw.client)[0] == 0


def test_retrospective_digest_lists_the_days_incidents_in_one_sms(db, gw):
    t = now(db)
    numbers = []
    for _ in range(3):
        inc, req = alerted(db, end=t - 30 * MIN)
        store.expand(db, req, t)
        numbers.append(incident(db, inc)["incident_no"])
    store.sweep(db, t)                                            # not yet due
    assert store.dispatch(db, t, gw.client)[0] == 0
    due = store.next_digest(t)
    store.sweep(db, due)
    store.dispatch(db, due, gw.client)
    texts = gw.to(PHONES["oic"])
    assert len(texts) == 1 and texts[0].startswith("RETRO") and all(f"#{n}" in texts[0] for n in numbers)
    # the LGA officer is copied on retrospective incidents and gets a digest too
    assert len(gw.to(PHONES["lga"])) == 1


# Delivery outcomes (Table 3.20) ------------------------------------------------------------------------

def test_delivered_sms_recorded_in_audit_trail(db, gw):
    inc, req = alerted(db)
    store.expand(db, req, now(db))
    store.dispatch(db, now(db), gw.client)
    report(db, sms(db, inc)[0], "Buffered")
    assert sms(db, inc)[0]["status"] == "sent"                    # still in progress
    report(db, sms(db, inc)[0], "Success")
    assert sms(db, inc)[0]["status"] == "delivered"
    assert actions(db, inc)[-1] == "delivery_report"


def test_temporary_failure_retried_three_times_over_five_minutes_then_next_tier(db, gw):
    inc, req = alerted(db)
    t = now(db)
    store.expand(db, req, t)
    sent_at = []
    for minute in range(7):
        at = t + minute * MIN
        before = len(gw.sent)
        store.dispatch(db, at, gw.client)
        if len(gw.sent) > before:
            sent_at.append(minute)
            assert report(db, sms(db, inc)[0], "Failed", "DeliveryFailure", at) is not None
    assert sent_at == [0, 1, 3, 5]                               # first attempt and three retries
    n = sms(db, inc)[0]
    assert (n["status"], n["attempts"]) == ("undelivered", 4)
    i = incident(db, inc)
    assert (i["current_tier"], i["notification_failed"]) == ("lga", True)
    esc = notices(db, inc)[-1]
    assert (esc["kind"], esc["tier"], esc["recipients"], esc["reason"]) == \
        ("escalation", "lga", [USERS["lga"]], "undelivered")
    assert {"notification_failed", "escalated"} <= set(actions(db, inc))
    # the LGA officer's SMS says why
    store.expand(db, esc, t + 6 * MIN)
    store.dispatch(db, t + 6 * MIN, gw.client)
    assert "could not be delivered" in gw.to(PHONES["lga"])[0]


def test_rejected_number_not_retried_next_tier_and_administrator_told(db, gw):
    gw.refuse.add(PHONES["oic"])
    inc, req = alerted(db)
    store.expand(db, req, now(db))
    store.dispatch(db, now(db), gw.client)
    assert len(gw.to(PHONES["oic"])) == 1
    assert sms(db, inc)[0]["status"] == "rejected"
    assert incident(db, inc)["current_tier"] == "lga"
    admin = db.execute("""SELECT body FROM notification WHERE recipient_id = %s AND kind = 'admin'
                          AND channel = 'dashboard' ORDER BY created_at DESC LIMIT 1""", (USERS["admin"],)).fetchone()
    assert admin and "rejected" in admin[0]


def test_no_final_status_within_ten_minutes_treated_as_undelivered(db, gw):
    inc, req = alerted(db)
    t = now(db)
    store.expand(db, req, t)
    store.dispatch(db, t, gw.client)
    store.sweep(db, t + 9 * MIN)
    assert sms(db, inc)[0]["status"] == "sent"
    touched = store.sweep(db, t + 10 * MIN)
    assert touched == [inc] and sms(db, inc)[0]["status"] == "undelivered"
    assert incident(db, inc)["current_tier"] == "lga"
    report(db, sms(db, inc)[0], "Success", at=t + 11 * MIN)       # a late report changes nothing
    assert sms(db, inc)[0]["status"] == "undelivered"


def test_failure_after_acknowledgement_only_flags_the_incident(db, gw):
    gw.refuse.add(PHONES["oic"])
    inc, req = alerted(db)
    incidents.acknowledge(db, inc, USERS["oic"])                   # on the dashboard, before the SMS went
    store.expand(db, req, now(db))
    store.dispatch(db, now(db), gw.client)
    i = incident(db, inc)
    assert (i["current_tier"], i["notification_failed"]) == ("owner", True)


def test_user_without_usable_number_counts_as_undeliverable(db, gw):
    db.execute("UPDATE app_user SET phone_deactivated_at = now() WHERE id = %s", (USERS["oic"],))
    inc, req = alerted(db)
    touched = store.expand(db, req, now(db))
    assert touched == [inc] and sms(db, inc)[0]["failure_reason"] == "phone number deactivated"
    assert gw.sent == [] and incident(db, inc)["current_tier"] == "lga"


def test_development_redirect_sends_every_sms_to_one_phone(gw, monkeypatch):
    monkeypatch.setattr(get_settings(), "sms_redirect_to", PHONES["oic"])
    assert gateway.send(PHONES["lga"], "test", gw.client).outcome == "sent"
    assert gw.sent[-1]["to"] == PHONES["oic"]


def test_development_trust_sent_counts_accepted_sms_as_delivered(db, gw, monkeypatch):
    monkeypatch.setattr(get_settings(), "sms_trust_sent", True)
    inc, req = alerted(db)
    store.expand(db, req, now(db))
    store.dispatch(db, now(db), gw.client)
    assert sms(db, inc)[0]["status"] == "delivered"
    report(db, sms(db, inc)[0], "Failed", "DeliveryFailure")      # what the sandbox reports
    assert sms(db, inc)[0]["status"] == "delivered" and incident(db, inc)["current_tier"] == "owner"


# Found in sandbox testing ------------------------------------------------------------------------------

def test_acknowledgement_reply_carries_a_working_code(db, gw):
    from coldchain.notify import commands
    inc, req = alerted(db)
    no = incident(db, inc)["incident_no"]
    store.expand(db, req, now(db))
    store.dispatch(db, now(db), gw.client)
    alert_code = gw.to(PHONES["oic"])[-1].split()[-2]
    commands.handle(db, "ack-1", PHONES["oic"], f"ACK {no}", now(db))
    store.dispatch(db, now(db), gw.client)
    text = gw.to(PHONES["oic"])[-1]
    assert text.startswith(f"OK #{no} acknowledged. When fixed: RES {no} ") and "****" not in text
    assert "****" in sms(db, inc, kind="reply")[0]["body"]                     # stored masked
    code = text.split()[-2]
    assert code != alert_code
    with db.transaction():                              # either code works: the alert's still does...
        r = commands.handle(db, "res-1", PHONES["oic"], f"RES {no} {alert_code} done", now(db))
        assert r.outcome == "accepted"
        raise psycopg.Rollback()
    r = commands.handle(db, "res-2", PHONES["oic"], f"RES {no} {code} moved stock", now(db))   # ...and the new
    assert r.outcome == "accepted"
    assert db.execute("SELECT count(*) FROM response_code WHERE incident_id = %s AND used_at IS NULL "
                      "AND voided_at IS NULL", (inc,)).fetchone()[0] == 0        # using one voided the other


def test_resolution_asks_the_lga_officer_to_review_on_the_dashboard(db, gw):
    inc, _ = alerted(db)
    incidents.resolve(db, inc, USERS["oic"], "moved stock")
    req = notices(db, inc)[-1]
    assert (req["kind"], req["recipients"], req["urgency"]) == ("review", [USERS["lga"]], "dashboard")
    store.expand(db, req, now(db))
    rows = db.execute("SELECT channel, body FROM notification WHERE request_id = %s", (req["id"],)).fetchall()
    assert len(rows) == 1 and rows[0][0] == "dashboard" and rows[0][1].startswith("Resolved, awaiting your review")


# Custody --------------------------------------------------------------------------------------------------

def test_receiver_texted_a_confirmation_code_and_releaser_told_if_it_fails(db, gw):
    from coldchain import custody
    t = now(db)
    cid = custody.create(db, USERS["oic"], PRODUCT, 50, HEALTH_FACILITY, "Outreach", 30, None, t)
    custody.release(db, USERS["oic"], cid, USERS["outreach"], t)
    req = json.loads(db.execute("""SELECT payload::text FROM outbox WHERE topic = 'notifications' AND key = %s
                                   ORDER BY id DESC LIMIT 1""", (cid,)).fetchone()[0])
    assert (req["kind"], req["recipients"]) == ("handoff", [USERS["outreach"]])
    gw.refuse.add(PHONES["outreach"])
    store.expand(db, req, t)
    store.dispatch(db, t, gw.client)
    (text,) = gw.to(PHONES["outreach"])
    no = db.execute("SELECT consignment_no FROM consignment WHERE id = %s", (cid,)).fetchone()[0]
    assert f"REC {no} " in text and f"REJ {no} " in text
    store.dispatch(db, t, gw.client)
    assert "Could not reach" in gw.to(PHONES["oic"])[0]
