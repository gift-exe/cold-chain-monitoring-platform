"""I4 end to end through the running system: readings over MQTT become an integrity event (detection), an
incident with the right owner, a notification request and an escalation workflow (incident service and
Temporal); people then act through the API with Keycloak tokens. Needs `make dev`.
"""
import asyncio
import json
import random
import time
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import psycopg
import pytest
from confluent_kafka import Consumer
from temporalio.client import Client, WorkflowExecutionStatus

from coldchain import kafka
from coldchain.config import get_settings
from coldchain.simulator.basic import SimDevice, between, connect, publish_live
from tests.conftest import HEALTH_FACILITY, PRODUCT, USERS, _pg_url, close_test_incidents
from tests.test_auth import password_grant

pytestmark = pytest.mark.integration
API = "http://localhost:8000"
TIMEOUT_S = 45


@pytest.fixture(scope="module")
def token(realm_users):
    passwords = {u["username"]: u["credentials"][0]["value"] for u in realm_users}
    cache = {}

    def get(username: str) -> dict:
        if username not in cache:
            r = password_grant("coldchain-dev-cli", username, passwords[username])
            cache[username] = {"Authorization": f"Bearer {r.json()['access_token']}"}
        return cache[username]
    return get


@pytest.fixture
def cleanup():
    devices, consignments = [], []
    yield devices, consignments
    close_test_incidents(devices, consignments)


def register(cleanup, fixed: bool) -> tuple[SimDevice, str]:
    serial = f"E2E-{uuid.uuid4().hex[:10]}"
    with psycopg.connect(_pg_url("owner"), autocommit=True) as conn:
        dev = conn.execute("""INSERT INTO device (serial_no, facility_id, type, equipment_type)
                              VALUES (%s, %s, %s, 'test_fridge') RETURNING id::text""",
                           (serial, HEALTH_FACILITY if fixed else None, "fixed" if fixed else "mobile")).fetchone()[0]
    cleanup[0].append(dev)
    time.sleep(1.1)   # the bridge's device cache reloads at most once a second
    return SimDevice(serial, phase=0.0), dev


def wait_for(query: str, args, until=lambda row: row is not None, timeout=TIMEOUT_S):
    deadline = time.monotonic() + timeout
    with psycopg.connect(_pg_url("app"), autocommit=True) as conn:
        while True:
            cur = conn.cursor(row_factory=psycopg.rows.dict_row)
            row = cur.execute(query, args).fetchone()
            if until(row) or time.monotonic() > deadline:
                return row
            time.sleep(0.5)


def notifications_for(incident_id: str, n: int) -> list[dict]:
    c = Consumer({"bootstrap.servers": get_settings().kafka_bootstrap, "group.id": f"test-{uuid.uuid4().hex}",
                  "auto.offset.reset": "earliest", "enable.auto.commit": False})
    c.subscribe([kafka.NOTIFICATIONS])
    found, deadline = [], time.monotonic() + TIMEOUT_S
    while len(found) < n and time.monotonic() < deadline:
        for m in c.consume(500, 1.0):
            if not m.error() and m.key() and m.key().decode() == incident_id:
                found.append(json.loads(m.value()))
    c.close()
    return found


async def workflow_status(incident_id: str) -> WorkflowExecutionStatus:
    client = await Client.connect(get_settings().temporal_address)
    return (await client.get_workflow_handle(f"incident-{incident_id}").describe()).status


def test_live_excursion_to_closure(cleanup, token):
    sim, dev = register(cleanup, fixed=True)
    start = datetime.now(UTC).replace(second=0, microsecond=0) - timedelta(minutes=45)
    readings = sim.readings(start, 45, 60, random.Random(7), temp=between(5, 45, 10.5))   # still hot now
    client = connect(f"test-e2e-{uuid.uuid4().hex[:8]}")
    try:
        publish_live(client, sim, readings)
    finally:
        client.loop_stop()
        client.disconnect()

    inc = wait_for("SELECT * FROM incident WHERE device_id = %s", (dev,),
                   until=lambda r: r is not None and r["state"] == "notified")
    assert inc is not None and inc["state"] == "notified", "incident not created and notified in time"
    assert (str(inc["owner_id"]), inc["mode"], inc["severity"]) == (USERS["oic"], "operational", "critical")
    # Section 3.4.4: the five timestamps (event start from the event; receipt, detection, notification here)
    assert inc["received_at"] <= inc["detected_at"] <= inc["notified_at"]
    alert = notifications_for(str(inc["id"]), 1)
    assert alert and (alert[0]["kind"], alert[0]["recipients"], alert[0]["urgency"]) == \
        ("alert", [USERS["oic"]], "sms")

    no = inc["incident_no"]
    with httpx.Client(base_url=API, timeout=10) as api:
        assert api.post(f"/incidents/{no}/acknowledge").status_code == 401
        assert api.post(f"/incidents/{no}/acknowledge", headers=token("test.driver")).status_code == 403
        r = api.post(f"/incidents/{no}/acknowledge", headers=token("test.facility"))
        assert r.status_code == 200 and r.json()["changed"]
        r = api.post(f"/incidents/{no}/resolve", headers=token("test.facility"),
                     json={"note": "moved vaccines to backup fridge; technician called"})
        assert r.status_code == 200
        assert api.post(f"/incidents/{no}/close", headers=token("test.facility")).status_code == 403
        assert api.post(f"/incidents/{no}/close", headers=token("test.lga"), json={"note": "verified"}).status_code == 200
        detail = api.get(f"/incidents/{no}", headers=token("test.lga")).json()
    assert detail["state"] == "closed"
    people_and_policy = [a["action"] for a in detail["actions"] if a["action"] != "delivery_report"]   # SMS (I5)
    assert people_and_policy == ["created", "notified", "acknowledged", "resolved", "closed"]

    deadline = time.monotonic() + 15
    while (status := asyncio.run(workflow_status(str(inc["id"])))) != WorkflowExecutionStatus.COMPLETED:
        assert time.monotonic() < deadline, f"workflow still {status}"
        time.sleep(0.5)


def test_rejected_handoff_through_api_raises_custody_gap_incident(cleanup, token):
    sim, _ = register(cleanup, fixed=False)
    with httpx.Client(base_url=API, timeout=10) as api:
        r = api.post("/consignments", headers=token("test.facility"), json={
            "product_id": PRODUCT, "quantity": 120, "origin_facility_id": HEALTH_FACILITY,
            "destination": "Outreach: Kofar Ruwa", "expected_transit_min": 45, "device_serial": sim.serial})
        assert r.status_code == 201, r.text
        cid = r.json()["consignment_id"]
        cleanup[1].append(cid)
        r = api.post(f"/consignments/{cid}/release", headers=token("test.facility"), json={"to_user": "test.outreach"})
        assert r.status_code == 200, r.text
        assert api.post(f"/consignments/{cid}/confirm", headers=token("test.driver")).status_code == 403
        r = api.post(f"/consignments/{cid}/reject", headers=token("test.outreach"), json={"reason": "carrier warm"})
        assert r.status_code == 200
        state = api.get(f"/consignments/{cid}/custody", headers=token("test.facility")).json()
        assert (state["state"], state["holder"]) == ("rejected", USERS["oic"])

        inc = wait_for("SELECT * FROM incident WHERE consignment_id = %s AND type = 'custody_gap'", (cid,))
        assert inc is not None, "custody-gap incident not created"
        assert (str(inc["owner_id"]), [str(c) for c in inc["cc_ids"]], inc["severity"]) == \
            (USERS["oic"], [USERS["lga"]], "warning")

        assert api.post(f"/consignments/{cid}/cancel", headers=token("test.facility"),
                        json={"reason": "returned to facility fridge"}).status_code == 200
        cleared = wait_for("SELECT condition_cleared FROM incident WHERE id = %s", (inc["id"],),
                           until=lambda r: r["condition_cleared"])
        assert cleared["condition_cleared"]
        assert api.post(f"/consignments/{cid}/complete", headers=token("test.facility")).status_code == 200


def test_audit_verification_through_api(token):
    with httpx.Client(base_url=API, timeout=60) as api:
        assert api.get("/audit/verify", headers=token("test.facility")).status_code == 403
        r = api.get("/audit/verify", headers=token("test.admin"))
    assert r.status_code == 200
    assert {c["table"]: c["intact"] for c in r.json()} == {"incident_action": True, "custody_event": True}
