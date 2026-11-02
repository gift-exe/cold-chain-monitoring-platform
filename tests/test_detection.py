"""I3 acceptance (Table 3.2): each event type detected in scripted cases through the running system,
including retrospective cases; no detection from incomplete data.

Readings are published over MQTT and pass through the bridge, Kafka, the storage writer and the detection
service; the tests then check the integrity events stored. Needs `make dev`.
"""
import json
import random
import time
import uuid
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from confluent_kafka import Consumer

from coldchain import kafka
from coldchain.config import get_settings
from coldchain.simulator.basic import SimDevice, between, connect, publish_buffered, publish_live
from tests.conftest import _pg_url

pytestmark = pytest.mark.integration
TIMEOUT_S = 40
FACILITY = "8a3c1f52-2b7e-4c1d-9d6a-0f4e5b6c7d01"
MIN = timedelta(minutes=1)


@pytest.fixture
def register():
    """Register test devices; remove them, their readings and events afterwards."""
    created = []

    def make(fixed=False, power=False, door=False, interval_s=60):
        serial = f"DET-{uuid.uuid4().hex[:10]}"
        with psycopg.connect(_pg_url("owner"), autocommit=True) as conn:
            dev_id = conn.execute(
                "INSERT INTO device (serial_no, facility_id, type, equipment_type, has_power_signal, "
                "has_door_sensor, reporting_interval_s) VALUES (%s, %s, %s, 'test_fridge', %s, %s, %s) RETURNING id",
                (serial, FACILITY if fixed else None, "fixed" if fixed else "mobile", power, door, interval_s),
            ).fetchone()[0]
        created.append(dev_id)
        return SimDevice(serial, phase=0.0), str(dev_id)

    time.sleep(0)  # registration happens inside tests; bridge cache reload is handled by wait below
    yield make
    with psycopg.connect(_pg_url("owner"), autocommit=True) as conn:
        for d in created:
            conn.execute("DELETE FROM integrity_event WHERE device_id = %s", (d,))
            conn.execute("DELETE FROM reading WHERE device_id = %s", (d,))
            conn.execute("DELETE FROM device WHERE id = %s", (d,))


@pytest.fixture(scope="module")
def mqtt():
    client = connect(f"test-det-{uuid.uuid4().hex[:8]}")
    time.sleep(1.1)  # devices registered just before publishing must be loadable by the bridge's cache
    yield client
    client.loop_stop()
    client.disconnect()


def events(dev_id, kind=None) -> list[dict]:
    with psycopg.connect(_pg_url("owner")) as conn:
        cur = conn.cursor(row_factory=psycopg.rows.dict_row)
        q = "SELECT * FROM integrity_event WHERE device_id = %s" + (" AND type = %s" if kind else "")
        return cur.execute(q + " ORDER BY event_start", (dev_id, kind) if kind else (dev_id,)).fetchall()


def wait_events(dev_id, kind, until=lambda evs: len(evs) > 0, timeout=TIMEOUT_S) -> list[dict]:
    deadline = time.monotonic() + timeout
    while True:
        evs = events(dev_id, kind)
        if until(evs) or time.monotonic() > deadline:
            return evs
        time.sleep(0.5)


def settle(dev_id, n_readings):
    """Wait until all readings are stored and detection has had time to process them."""
    deadline = time.monotonic() + TIMEOUT_S
    with psycopg.connect(_pg_url("owner"), autocommit=True) as conn:
        while conn.execute("SELECT count(*) FROM reading WHERE device_id = %s", (dev_id,)).fetchone()[0] < n_readings:
            assert time.monotonic() < deadline, "readings not stored in time"
            time.sleep(0.3)
    time.sleep(3)


def ago(minutes) -> datetime:
    return datetime.now(UTC).replace(second=0, microsecond=0) - timedelta(minutes=minutes)


# Heat excursion: live, replayed, and corrected by late data ------------------------

def test_live_heat_excursion_detected_once_even_if_replayed(mqtt, register):
    sim, dev = register()
    time.sleep(1.1)
    start = ago(90)
    readings = list(sim.readings(start, 90, 60, random.Random(1), temp=between(20, 60, 10.5)))
    publish_live(mqtt, sim, iter(readings))
    evs = wait_events(dev, "heat_excursion")
    assert len(evs) == 1
    e = evs[0]
    assert e["severity"] == "critical" and e["event_start"] == start + 20 * MIN and e["event_end"] == start + 60 * MIN
    assert 10.3 <= float(e["peak_value"]) <= 10.7

    publish_live(mqtt, sim, iter(readings))   # the whole history arrives again
    settle(dev, 90)
    assert len(events(dev, "heat_excursion")) == 1


def test_late_readings_reveal_earlier_start_of_same_event(mqtt, register):
    sim, dev = register()
    time.sleep(1.1)
    start = ago(120)
    readings = list(sim.readings(start, 120, 60, random.Random(2), temp=between(30, 90, 11.0)))
    # Readings 50-119 arrive first: 40 hot minutes (50-89), enough for an event starting at minute 50
    publish_live(mqtt, sim, iter(readings[50:]))
    first = wait_events(dev, "heat_excursion")
    assert first and first[0]["event_start"] == start + 50 * MIN
    # The buffered earlier readings arrive: the same event now starts at minute 30
    publish_buffered(mqtt, sim, iter(readings[:50]))
    evs = wait_events(dev, "heat_excursion", until=lambda e: e and e[0]["event_start"] == start + 30 * MIN)
    assert len(evs) == 1 and evs[0]["id"] == first[0]["id"]
    assert evs[0]["event_start"] == start + 30 * MIN and evs[0]["updated_at"] > evs[0]["detected_at"]


# Retrospective and incomplete data --------------------------------------------------

def test_retrospective_freeze_from_24_hour_old_upload(mqtt, register):
    sim, dev = register()
    time.sleep(1.1)
    start = ago(26 * 60)
    readings = sim.readings(start, 120, 60, random.Random(3), temp=between(40, 52, -1.5))
    publish_buffered(mqtt, sim, readings)
    evs = wait_events(dev, "freeze")
    assert len(evs) == 1
    e = evs[0]
    assert e["event_start"] == start + 40 * MIN and e["event_end"] == start + 52 * MIN
    assert e["detected_at"] - e["event_end"] > timedelta(hours=23)   # detected long after it ended


def test_no_event_inferred_across_missing_data(mqtt, register):
    sim, dev = register()
    time.sleep(1.1)
    start = ago(200)
    readings = list(sim.readings(start, 200, 60, random.Random(4), temp=between(20, 160, 10.5)))
    # Only 20 hot minutes either side of a 2-hour hole are received: no 30-minute run is observed
    kept = readings[:40] + readings[140:]
    publish_buffered(mqtt, sim, iter(kept))
    settle(dev, len(kept))
    assert events(dev, "heat_excursion") == []


# Power, door, alarm: need the matching input ------------------------------------------

def test_power_loss_and_door_open_detected_with_inputs(mqtt, register):
    sim, dev = register(power=True, door=True)
    time.sleep(1.1)
    readings = sim.readings(ago(60), 60, 60, random.Random(5), power_ok=between(10, 25, False),
                            door_open=between(35, 42, True))
    publish_buffered(mqtt, sim, readings)
    assert wait_events(dev, "power_loss")[0]["severity"] == "warning"
    assert len(wait_events(dev, "door_open")) == 1


def test_power_and_door_not_reported_without_inputs(mqtt, register):
    sim, dev = register(power=False, door=False)
    time.sleep(1.1)
    readings = sim.readings(ago(60), 60, 60, random.Random(6), power_ok=between(10, 25, False),
                            door_open=between(35, 42, True))
    publish_buffered(mqtt, sim, readings)
    settle(dev, 60)
    assert events(dev, "power_loss") == [] and events(dev, "door_open") == []


def test_local_alarm_recorded(mqtt, register):
    sim, dev = register()
    time.sleep(1.1)
    readings = sim.readings(ago(30), 30, 60, random.Random(7), alarm=between(10, 12, True))
    publish_buffered(mqtt, sim, readings)
    assert wait_events(dev, "local_alarm")[0]["severity"] == "critical"


# Device silence ------------------------------------------------------------------------

def test_device_silence_starts_and_ends(mqtt, register):
    sim, dev = register(fixed=True, interval_s=2)   # silent after 3 intervals = 6 s
    time.sleep(1.1)
    publish_live(mqtt, sim, sim.readings(datetime.now(UTC) - timedelta(seconds=10), 10 / 60, 2, random.Random(8)))
    evs = wait_events(dev, "device_silence")
    assert len(evs) == 1 and evs[0]["event_end"] is None and evs[0]["severity"] == "warning"
    publish_live(mqtt, sim, sim.readings(datetime.now(UTC), 2 / 60, 2, random.Random(9)))
    evs = wait_events(dev, "device_silence", until=lambda e: e and e[0]["event_end"] is not None)
    assert evs[0]["event_end"] is not None and len(evs) == 1


# Anomaly warnings ------------------------------------------------------------------------

def test_anomaly_warning_with_explanation(mqtt, register):
    sim, dev = register()
    time.sleep(1.1)
    start = ago(32 * 60).replace(minute=0)
    # 30 hours of normal cycling, then an hour with the compressor stuck (flat 6 °C, still in range)
    readings = sim.readings(start, 32 * 60, 60, random.Random(10), temp=between(30 * 60, 31 * 60, 6.0))
    publish_buffered(mqtt, sim, readings)
    evs = wait_events(dev, "anomaly", until=lambda e: any(x["event_start"] == start + timedelta(hours=30) for x in e))
    hit = [e for e in evs if e["event_start"] == start + timedelta(hours=30)]
    assert hit and hit[0]["severity"] == "warning"
    assert hit[0]["details"]["explanation"].startswith("Unusual pattern:")
    assert not events(dev, "heat_excursion")   # an anomaly is never a critical incident


# Published for the incident service --------------------------------------------------------

def test_integrity_events_published_to_kafka(mqtt, register):
    consumer = Consumer({"bootstrap.servers": get_settings().kafka_bootstrap,
                         "group.id": f"test-{uuid.uuid4().hex[:8]}", "auto.offset.reset": "latest"})
    consumer.subscribe([kafka.INTEGRITY_EVENTS])
    consumer.poll(5)   # join the group before publishing
    sim, dev = register()
    time.sleep(1.1)
    publish_buffered(mqtt, sim, sim.readings(ago(60), 60, 60, random.Random(11), temp=between(5, 45, 12.0)))
    deadline, seen = time.monotonic() + TIMEOUT_S, None
    while seen is None and time.monotonic() < deadline:
        m = consumer.poll(1)
        if m and not m.error():
            v = json.loads(m.value())
            if v["device_id"] == dev and v["type"] == "heat_excursion":
                seen = v
    consumer.close()
    assert seen is not None and seen["change"] == "created" and seen["severity"] == "critical"
