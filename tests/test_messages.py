"""Unit tests for message validation (FR1.1-FR1.3, FR2.3, FR2.4). No services needed."""
import json
from datetime import UTC, datetime, timedelta

import pytest

from coldchain.messages import parse

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
TOPIC = "devices/DEV-1/readings"


def reading(seq=1, minutes_ago=0, **kw):
    r = {"seq_no": seq, "event_time": (NOW - timedelta(minutes=minutes_ago)).isoformat(), "temp": 5.0}
    r.update(kw)
    return r


def run(payload, topic=TOPIC):
    data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    return parse(topic, data, NOW)


def test_single_reading_accepted():
    ok, bad = run({"device_id": "DEV-1", **reading(power_ok=True, door_open=False, lat=12.0, lon=8.5, battery=90)})
    assert len(ok) == 1 and not bad
    assert ok[0].received_at == NOW and not ok[0].late


def test_batch_accepted_in_any_order():
    batch = {"device_id": "DEV-1", "readings": [reading(seq=s, minutes_ago=100 - s) for s in (3, 1, 2)]}
    ok, bad = run(batch)
    assert [a.reading.seq_no for a in ok] == [3, 1, 2] and not bad


def test_one_bad_reading_does_not_reject_the_batch():
    batch = {"device_id": "DEV-1", "readings": [reading(seq=1), {"seq_no": 2}, reading(seq=3)]}
    ok, bad = run(batch)
    assert [a.reading.seq_no for a in ok] == [1, 3]
    assert len(bad) == 1 and "event_time" in bad[0].reason


@pytest.mark.parametrize("payload,reason", [
    (b"{not json", "invalid JSON"),
    (b"[1, 2]", "not a JSON object"),
    ({"device_id": "DEV-1", "seq_no": 1, "temp": 5.0}, "event_time"),
    ({"device_id": "DEV-1", **reading(temp=95.0)}, "temp"),
    ({"device_id": "DEV-1", **reading(seq=-1)}, "seq_no"),
    ({"device_id": "DEV-1", **reading(lat=12.0)}, "lat and lon"),
    ({"device_id": "DEV-1", **reading(colour="red")}, "colour"),
    ({"device_id": "DEV-1", "seq_no": 1, "event_time": "2026-11-02T12:00:00", "temp": 5.0}, "timezone"),
    ({"device_id": "DEV-2", **reading()}, "does not match topic"),
    ({"device_id": "DEV-1", **reading(minutes_ago=-6)}, "future"),
])
def test_malformed_messages_rejected_with_reason(payload, reason):
    ok, bad = run(payload)
    assert not ok and len(bad) == 1
    assert reason in bad[0].reason


def test_reading_up_to_five_minutes_ahead_accepted():  # tolerated clock drift
    ok, _ = run({"device_id": "DEV-1", **reading(minutes_ago=-4)})
    assert len(ok) == 1


def test_reading_beyond_72_hours_flagged_late():
    ok, _ = run({"device_id": "DEV-1", "readings": [reading(seq=1, minutes_ago=72 * 60 - 1),
                                                    reading(seq=2, minutes_ago=72 * 60 + 1)]})
    assert [a.late for a in ok] == [False, True]


def test_unexpected_topic_rejected():
    _, bad = run({"device_id": "DEV-1", **reading()}, topic="devices/DEV-1/other")
    assert "unexpected topic" in bad[0].reason
