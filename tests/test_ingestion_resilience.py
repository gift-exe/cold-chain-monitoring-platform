"""NFR6: no reading is lost when an ingestion service restarts while readings are arriving.

Restarts containers, so it is excluded from `make test`; run with `make test-resilience`.
Readings are identified by sequence number, so a lost reading shows up as a gap (Section 3.2.4).
"""
import random
import subprocess
import threading
import time
import uuid
from datetime import UTC, datetime, timedelta

import psycopg
import pytest

from coldchain.simulator.basic import SimDevice, connect, publish_live
from tests.conftest import _pg_url

pytestmark = [pytest.mark.integration, pytest.mark.resilience]
N = 3000


@pytest.mark.parametrize("container", ["coldchain-ingestion-bridge-1", "coldchain-storage-writer-1"])
def test_no_reading_lost_when_service_restarts(container):
    serial = f"RESTART-{uuid.uuid4().hex[:8]}"
    with psycopg.connect(_pg_url("owner"), autocommit=True) as conn:
        dev = conn.execute("INSERT INTO device (serial_no, type, equipment_type) VALUES (%s, 'mobile', 'test') "
                           "RETURNING id", (serial,)).fetchone()[0]
    time.sleep(1.5)
    try:
        sim, client = SimDevice(serial), connect(f"resilience-{uuid.uuid4().hex[:8]}")
        readings = sim.readings(datetime.now(UTC) - timedelta(minutes=N), N, 60, random.Random(1))
        publisher = threading.Thread(target=publish_live, args=(client, sim, readings))
        publisher.start()
        time.sleep(0.5)                                   # restart while readings are flowing
        subprocess.run(["podman", "restart", container], check=True, capture_output=True)
        publisher.join()
        client.loop_stop()
        client.disconnect()

        with psycopg.connect(_pg_url("owner"), autocommit=True) as conn:
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                stored = conn.execute("SELECT count(*) FROM reading WHERE device_id = %s", (dev,)).fetchone()[0]
                if stored == N:
                    break
                time.sleep(1)
            missing = conn.execute(
                "SELECT count(*) FROM generate_series(1, %s) s "
                "WHERE s NOT IN (SELECT seq_no FROM reading WHERE device_id = %s)", (N, dev)).fetchone()[0]
        assert missing == 0, f"{missing} of {N} readings lost after restarting {container}"
        assert stored == N
    finally:
        with psycopg.connect(_pg_url("owner"), autocommit=True) as conn:
            conn.execute("DELETE FROM reading WHERE device_id = %s", (dev,))
            conn.execute("DELETE FROM device WHERE id = %s", (dev,))
