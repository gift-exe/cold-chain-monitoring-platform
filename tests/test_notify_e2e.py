"""I5 end to end through the running system and the SMS gateway emulator: an excursion is texted to its
owner, who acknowledges and resolves it by SMS; an undeliverable alert goes to the next tier at once; a
receiver confirms a consignment by SMS; forged callbacks are refused. Needs `make dev`.
"""
import random
import re
import time
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from coldchain.config import get_settings
from coldchain.simulator.basic import between, connect, publish_live
from tests.conftest import HEALTH_FACILITY, PRODUCT, USERS
from tests.test_incident_e2e import API, cleanup, register, token, wait_for  # noqa: F401 (fixtures)

pytestmark = pytest.mark.integration
GATEWAY = "http://localhost:8002"
NOTIFY = "http://localhost:8001"
PHONE = {"oic": "+2348000000004", "lga": "+2348000000003", "outreach": "+2348000000005"}
TIMEOUT_S = 45


@pytest.fixture
def gateway():
    with httpx.Client(base_url=GATEWAY, timeout=10) as g:
        yield g
        g.delete("/_sim/numbers")


def texts(gateway, phone: str, since: str, contains: str, n: int = 1, timeout=TIMEOUT_S) -> list[str]:
    deadline = time.monotonic() + timeout
    while True:
        found = [m["message"] for m in gateway.get("/_sim/messages", params={"to": phone, "since": since}).json()
                 if contains in m["message"]]
        if len(found) >= n or time.monotonic() > deadline:
            return found
        time.sleep(0.3)


def reply(gateway, phone: str, text: str) -> dict:
    r = gateway.post("/_sim/inbound", json={"sender": phone, "text": text})
    assert r.status_code == 200 and r.json()["callback_status"] == 200, r.text
    return r.json()


def excursion(cleanup) -> dict:  # noqa: F811
    sim, dev = register(cleanup, fixed=True)
    start = datetime.now(UTC).replace(second=0, microsecond=0) - timedelta(minutes=45)
    client = connect(f"test-sms-{uuid.uuid4().hex[:8]}")
    try:
        publish_live(client, sim, sim.readings(start, 45, 60, random.Random(5), temp=between(5, 45, 10.5)))
    finally:
        client.loop_stop()
        client.disconnect()
    inc = wait_for("SELECT * FROM incident WHERE device_id = %s", (dev,))
    assert inc is not None, "incident not created"
    return inc


def test_alert_acknowledged_and_resolved_by_sms(cleanup, gateway):  # noqa: F811
    since = datetime.now(UTC).isoformat()
    inc = excursion(cleanup)
    no = inc["incident_no"]
    (alert,) = texts(gateway, PHONE["oic"], since, f"You own #{no}.")
    assert len(alert) <= 160 and alert.startswith("CRIT HEAT ")
    code = re.search(rf"RES {no} (\w+) note", alert).group(1)

    sent = wait_for("SELECT * FROM notification WHERE incident_id = %s AND channel = 'sms' AND status = 'delivered'",
                    (inc["id"],))
    assert sent is not None, "no delivery report recorded"
    assert (sent["first_sent_at"] - inc["received_at"]).total_seconds() <= 30     # E4: receipt to dispatch

    reply(gateway, PHONE["oic"], f"ACK {no}")
    assert texts(gateway, PHONE["oic"], since, f"OK #{no} acknowledged")
    reply(gateway, PHONE["oic"], f"RES {no} {code} moved vaccines to backup ILR")
    assert texts(gateway, PHONE["oic"], since, f"OK #{no} resolved")
    final = wait_for("SELECT state, acknowledged_by, resolved_by FROM incident WHERE id = %s", (inc["id"],),
                     until=lambda r: r["state"] == "resolved")
    assert final["state"] == "resolved" and str(final["resolved_by"]) == USERS["oic"]
    channels = wait_for("""SELECT array_agg(action || ':' || channel ORDER BY id) AS a FROM incident_action
                           WHERE incident_id = %s""", (inc["id"],))["a"]
    assert {"acknowledged:sms", "resolved:sms", "delivery_report:system"} <= set(channels)


def test_rejected_alert_goes_to_the_next_tier_at_once(cleanup, gateway):  # noqa: F811
    gateway.put(f"/_sim/numbers/{PHONE['oic']}", json={"outcome": "rejected"})
    since = datetime.now(UTC).isoformat()
    inc = excursion(cleanup)
    no = inc["incident_no"]
    esc = texts(gateway, PHONE["lga"], since, f"ESC CRIT HEAT #{no} ")
    assert esc and "could not be delivered" in esc[0]
    flagged = wait_for("SELECT current_tier, notification_failed FROM incident WHERE id = %s", (inc["id"],),
                       until=lambda r: r["notification_failed"])
    assert (flagged["current_tier"], flagged["notification_failed"]) == ("lga", True)


def test_consignment_confirmed_by_sms(cleanup, gateway, token):  # noqa: F811
    since = datetime.now(UTC).isoformat()
    with httpx.Client(base_url=API, timeout=10) as api:
        r = api.post("/consignments", headers=token("test.facility"), json={
            "product_id": PRODUCT, "quantity": 80, "origin_facility_id": HEALTH_FACILITY,
            "destination": "Outreach: Dorayi", "expected_transit_min": 40})
        cid = r.json()["consignment_id"]
        cleanup[1].append(cid)
        assert api.post(f"/consignments/{cid}/release", headers=token("test.facility"),
                        json={"to_user": "test.outreach"}).status_code == 200
        (msg,) = texts(gateway, PHONE["outreach"], since, "On arrival reply REC")
        cno, code = re.search(r"REC (\d+) (\w+)\.", msg).groups()
        reply(gateway, PHONE["outreach"], f"REC {cno} {code}")
        assert texts(gateway, PHONE["outreach"], since, f"OK consignment {cno} received")
        state = api.get(f"/consignments/{cid}/custody", headers=token("test.facility")).json()
        assert (state["state"], state["holder"]) == ("held", USERS["outreach"])
        assert api.post(f"/consignments/{cid}/complete", headers=token("test.outreach")).status_code == 200


def test_callbacks_need_the_shared_secret():
    with httpx.Client(base_url=NOTIFY, timeout=10) as n:
        r = n.post("/sms/not-the-token/inbound", data={"id": "forged-1", "from": PHONE["oic"], "text": "ACK 1"})
        assert r.status_code == 404
        r = n.post("/sms/not-the-token/delivery", data={"id": "x", "status": "Success"})
        assert r.status_code == 404
        good = get_settings().sms_callback_token
        assert n.post(f"/sms/{good}/delivery", data={"id": "unknown-id", "status": "Success"}).status_code == 200


def test_dashboard_notifications_through_api(token):  # noqa: F811
    with httpx.Client(base_url=API, timeout=10) as api:
        r = api.get("/notifications", headers=token("test.facility"))
        assert r.status_code == 200
        items = r.json()
        assert items and all(i["body"] for i in items)
        assert api.post(f"/notifications/{items[0]['id']}/read", headers=token("test.facility")).status_code == 200
        assert api.post(f"/notifications/{items[0]['id']}/read", headers=token("test.lga")).status_code == 404
        assert api.post("/users/test.oic/phone/deactivate", headers=token("test.lga")).status_code == 403
