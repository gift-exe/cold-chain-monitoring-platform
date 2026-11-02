"""Storage writer: stores readings from the event log in the database, by event time.

Readings are inserted in batches with ON CONFLICT DO NOTHING, so a reading delivered more than once is
stored once (FR1.4). Kafka offsets are committed only after the database transaction commits, so a crash
between the two causes a harmless re-insert rather than a lost reading.

Run: uv run python -m coldchain.ingestion.writer
"""
import json
import logging

import psycopg

from coldchain import kafka
from coldchain.config import get_settings

log = logging.getLogger("ingestion.writer")

BATCH_SIZE = 2000
BATCH_WAIT_S = 0.2

INSERT_READING = """
    INSERT INTO reading (device_id, seq_no, event_time, received_at, temperature, power_ok, door_open,
                         location, battery, late_flag)
    VALUES (%(device_id)s, %(seq_no)s, %(event_time)s, %(received_at)s, %(temperature)s, %(power_ok)s,
            %(door_open)s,
            CASE WHEN %(lon)s::float8 IS NULL THEN NULL
                 ELSE ST_SetSRID(ST_MakePoint(%(lon)s::float8, %(lat)s::float8), 4326)::geography END,
            %(battery)s, %(late_flag)s)
    ON CONFLICT DO NOTHING
"""
INSERT_REJECTED = """
    INSERT INTO rejected_message (received_at, topic, serial, reason, payload)
    VALUES (%(received_at)s, %(topic)s, %(serial)s, %(reason)s, %(payload)s)
"""


def store(conn: psycopg.Connection, readings: list[dict], rejected: list[dict]) -> int:
    """Store one batch in a single transaction. Returns the number of duplicate readings discarded."""
    stored = 0
    with conn.transaction(), conn.cursor() as cur:
        for r in readings:  # executemany would not report per-row conflicts
            cur.execute(INSERT_READING, r)
            stored += cur.rowcount
        if rejected:
            cur.executemany(INSERT_REJECTED, rejected)
    return len(readings) - stored


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    kafka.ensure_topics()
    url = get_settings().db_url("app").replace("postgresql+psycopg://", "postgresql://")
    consumer = kafka.consumer("storage-writer", [kafka.READINGS, kafka.READINGS_REJECTED])
    with psycopg.connect(url) as conn:
        while True:
            msgs = consumer.consume(num_messages=BATCH_SIZE, timeout=BATCH_WAIT_S)
            if not msgs:
                continue
            readings, rejected = [], []
            for m in msgs:
                if m.error():
                    log.error("Kafka error: %s", m.error())
                    continue
                (readings if m.topic() == kafka.READINGS else rejected).append(json.loads(m.value()))
            duplicates = store(conn, readings, rejected)
            consumer.commit(asynchronous=False)
            if duplicates:
                log.info("discarded %d duplicate readings", duplicates)
            log.debug("stored %d readings, %d rejected messages", len(readings) - duplicates, len(rejected))


if __name__ == "__main__":
    main()
