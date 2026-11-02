"""Regression test: a reading that cannot be stored must not block the readings behind it.

Defect found in I3: a test deleted its device while the device's readings were still in Kafka; the
foreign-key error failed the whole batch, the writer restarted and retried the same batch indefinitely,
and no further readings were stored.
"""
import uuid
from datetime import UTC, datetime

import pytest

from coldchain.ingestion.writer import store

pytestmark = pytest.mark.integration
FIXED_DEVICE = "d0000000-0000-4000-8000-000000000001"


def reading(device_id, seq):
    now = datetime.now(UTC).isoformat()
    return {"device_id": device_id, "serial": "X", "seq_no": seq, "event_time": now, "received_at": now,
            "temperature": 5.0, "power_ok": True, "door_open": False, "alarm": None, "lat": None, "lon": None,
            "battery": None, "late_flag": False}


def test_unstorable_reading_rejected_and_rest_stored(app_conn):
    seq = int(uuid.uuid4().int % 10**9) + 10**9
    good, orphan = reading(FIXED_DEVICE, seq), reading(str(uuid.uuid4()), 1)   # orphan: no such device
    rejected: list[dict] = []
    duplicates, committed = store(app_conn, [good, orphan], rejected)

    assert duplicates == 0 and committed == [good]
    assert len(rejected) == 1 and rejected[0]["reason"].startswith("could not be stored: ForeignKeyViolation")
    n = app_conn.execute("SELECT count(*) FROM reading WHERE device_id = %s AND seq_no = %s",
                         (FIXED_DEVICE, seq)).fetchone()[0]
    assert n == 1
