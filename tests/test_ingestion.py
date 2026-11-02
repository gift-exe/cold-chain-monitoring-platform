"""I2 acceptance (Table 3.2): simulated readings are stored once each; malformed and duplicate messages
are rejected with logged reasons; a 24-hour delayed batch is stored with correct event times.

These tests publish over MQTT and check the database, so they exercise the whole path:
broker -> ingestion bridge -> Kafka -> storage writer -> PostgreSQL. Needs `make dev`.
"""
import json
import random
import time
import uuid
from datetime import UTC, datetime, timedelta

import psycopg
import pytest

from coldchain.simulator.basic import SimDevice, connect, publish_buffered, publish_live, topic
from tests.conftest import _pg_url

pytestmark = pytest.mark.integration
TIMEOUT_S = 30


@pytest.fixture
def device():
    """A newly registered mobile device, removed with its readings after the test."""
    serial = f"TEST-{uuid.uuid4().hex[:10]}"
    with psycopg.connect(_pg_url("owner"), autocommit=True) as conn:
        dev_id = conn.execute("INSERT INTO device (serial_no, type, equipment_type) "
                              "VALUES (%s, 'mobile', 'vaccine_carrier') RETURNING id", (serial,)).fetchone()[0]
    time.sleep(1.1)  # let the bridge's device cache become eligible to reload (registry.REFRESH_AFTER_MISS_S)
    yield SimDevice(serial), dev_id
    with psycopg.connect(_pg_url("owner"), autocommit=True) as conn:
        conn.execute("DELETE FROM reading WHERE device_id = %s", (dev_id,))
        conn.execute("DELETE FROM device WHERE id = %s", (dev_id,))


@pytest.fixture(scope="module")
def mqtt_client():
    client = connect(f"test-{uuid.uuid4().hex[:8]}")
    yield client
    client.loop_stop()
    client.disconnect()


def wait_for(query: str, params: tuple, until) -> object:
    """Poll the database until query's value satisfies until (a value or a predicate), or TIMEOUT_S passes.
    Returns the last value, so the caller's assertion shows what was actually stored."""
    done = until if callable(until) else (lambda v: v == until)
    deadline = time.monotonic() + TIMEOUT_S
    with psycopg.connect(_pg_url("owner"), autocommit=True) as conn:
        while True:
            row = conn.execute(query, params).fetchone()
            value = row[0] if row else None
            if done(value) or time.monotonic() > deadline:
                return value
            time.sleep(0.3)


def count_readings(dev_id) -> tuple[str, tuple]:
    return "SELECT count(*) FROM reading WHERE device_id = %s", (dev_id,)


def test_live_readings_stored_once_each_despite_duplicates(mqtt_client, device):
    sim, dev_id = device
    readings = list(sim.readings(datetime.now(UTC) - timedelta(minutes=100), 100, 60, random.Random(1)))
    publish_live(mqtt_client, sim, iter(readings))
    publish_live(mqtt_client, sim, iter(readings[:50]))   # the device re-sends half of them

    assert wait_for(*count_readings(dev_id), 100) == 100
    time.sleep(2)  # give any stray duplicate time to arrive
    assert wait_for(*count_readings(dev_id), 100) == 100
    with psycopg.connect(_pg_url("owner")) as conn:
        seqs = [r[0] for r in conn.execute("SELECT seq_no FROM reading WHERE device_id = %s ORDER BY seq_no",
                                           (dev_id,))]
    assert seqs == list(range(1, 101))  # no gaps: nothing lost


def test_24_hour_delayed_batch_stored_with_event_times(mqtt_client, device):
    sim, dev_id = device
    start = datetime.now(UTC) - timedelta(hours=48)  # readings from 48 h to 24 h ago
    readings = list(sim.readings(start, 24 * 60, 60, random.Random(2)))
    random.Random(3).shuffle(readings)                   # uploaded out of order
    publish_buffered(mqtt_client, sim, iter(readings))

    assert wait_for(*count_readings(dev_id), 1440) == 1440
    with psycopg.connect(_pg_url("owner")) as conn:
        first, last, min_delay, any_late = conn.execute(
            "SELECT min(event_time), max(event_time), min(received_at - event_time), bool_or(late_flag) "
            "FROM reading WHERE device_id = %s", (dev_id,)).fetchone()
    assert abs((first - start).total_seconds()) < 1
    assert abs((last - (start + timedelta(minutes=1439))).total_seconds()) < 1
    assert min_delay >= timedelta(hours=24)      # receipt time recorded separately from event time
    assert any_late is False                     # within the 72 h horizon


def test_reading_beyond_72_hours_stored_and_flagged(mqtt_client, device):
    sim, dev_id = device
    old = {"seq_no": 1, "event_time": (datetime.now(UTC) - timedelta(hours=80)).isoformat(), "temp": 5.0}
    mqtt_client.publish(topic(sim.serial), json.dumps({"device_id": sim.serial, **old}), qos=1).wait_for_publish()
    assert wait_for("SELECT bool_and(late_flag) FROM reading WHERE device_id = %s", (dev_id,), True) is True


@pytest.mark.parametrize("payload,reason", [
    ('{"seq_no": 1, "temp": 5.0', "invalid JSON"),
    ('{"device_id": "%s", "seq_no": 1, "temp": 5.0}', "event_time"),
    ('{"device_id": "%s", "seq_no": 1, "event_time": "2099-01-01T00:00:00Z", "temp": 5.0}', "future"),
    ('{"device_id": "OTHER", "seq_no": 1, "event_time": "2026-11-01T00:00:00Z", "temp": 5.0}', "does not match"),
])
def test_malformed_messages_rejected_with_reason(mqtt_client, device, payload, reason):
    sim, dev_id = device
    body = payload % sim.serial if "%s" in payload else payload
    mqtt_client.publish(topic(sim.serial), body, qos=1).wait_for_publish()
    got = wait_for("SELECT reason FROM rejected_message WHERE serial = %s ORDER BY id DESC LIMIT 1",
                   (sim.serial,), lambda v: v is not None)
    assert got is not None and reason in got
    with psycopg.connect(_pg_url("owner")) as conn:
        assert conn.execute(*count_readings(dev_id)).fetchone()[0] == 0


def test_unknown_device_rejected(mqtt_client):
    serial = f"UNREGISTERED-{uuid.uuid4().hex[:8]}"
    msg = {"device_id": serial, "seq_no": 1, "event_time": datetime.now(UTC).isoformat(), "temp": 5.0}
    mqtt_client.publish(topic(serial), json.dumps(msg), qos=1).wait_for_publish()
    reason = wait_for("SELECT max(reason) FROM rejected_message WHERE serial = %s", (serial,),
                      "unknown or inactive device")
    assert reason == "unknown or inactive device"
