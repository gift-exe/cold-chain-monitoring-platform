"""Storage writer: stores readings from the event log in the database, by event time.

Readings are inserted in batches with ON CONFLICT DO NOTHING, so a reading delivered more than once is
stored once (FR1.4). After each batch commits, the writer publishes, for every device in the batch, the
event-time range of its new readings (readings.stored), which tells the detection service what to
re-evaluate. Kafka offsets are committed last, so a crash at any point causes harmless repetition rather
than a lost reading or a missed evaluation.

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
    INSERT INTO reading (device_id, seq_no, event_time, received_at, temperature, power_ok, door_open, alarm,
                         location, battery, late_flag)
    VALUES (%(device_id)s, %(seq_no)s, %(event_time)s, %(received_at)s, %(temperature)s, %(power_ok)s,
            %(door_open)s, %(alarm)s,
            CASE WHEN %(lon)s::float8 IS NULL THEN NULL
                 ELSE ST_SetSRID(ST_MakePoint(%(lon)s::float8, %(lat)s::float8), 4326)::geography END,
            %(battery)s, %(late_flag)s)
    ON CONFLICT DO NOTHING
"""
INSERT_REJECTED = """
    INSERT INTO rejected_message (received_at, topic, serial, reason, payload)
    VALUES (%(received_at)s, %(topic)s, %(serial)s, %(reason)s, %(payload)s)
"""
UPSERT_STATUS = """
    INSERT INTO device_status (device_id, last_event_time, last_received_at)
    VALUES (%(device_id)s, %(to)s, %(received_at)s)
    ON CONFLICT (device_id) DO UPDATE SET
        last_event_time  = greatest(device_status.last_event_time, excluded.last_event_time),
        last_received_at = greatest(device_status.last_received_at, excluded.last_received_at)
"""


def ranges(readings: list[dict]) -> list[dict]:
    """Per device: earliest and latest event time, and latest receipt time, in this batch."""
    out: dict[str, dict] = {}
    for r in readings:
        d = out.setdefault(r["device_id"], {"device_id": r["device_id"], "from": r["event_time"],
                                            "to": r["event_time"], "received_at": r["received_at"]})
        d["from"] = min(d["from"], r["event_time"])      # ISO 8601 in UTC sorts as text
        d["to"] = max(d["to"], r["event_time"])
        d["received_at"] = max(d["received_at"], r["received_at"])
    return list(out.values())


def _insert_readings(cur, readings: list[dict]) -> int:
    stored = 0
    for r in readings:  # executemany would not report per-row conflicts
        cur.execute(INSERT_READING, r)
        stored += cur.rowcount
    return stored


def _unstorable(reading: dict, error: Exception) -> dict:
    return {"received_at": reading["received_at"], "topic": kafka.READINGS, "serial": reading.get("serial"),
            "reason": f"could not be stored: {type(error).__name__}: {str(error).splitlines()[0]}",
            "payload": json.dumps(reading)}


def store(conn: psycopg.Connection, readings: list[dict], rejected: list[dict]) -> tuple[int, list[dict]]:
    """Store one batch in a single transaction. Returns the number of duplicate readings discarded and the
    readings actually committed.

    A reading that cannot be stored (for example, its device was deleted while the reading was in the
    event log) must not block every reading behind it, so if the batch fails, each reading is retried on
    its own and any that still fail are recorded in rejected_message with the reason.
    """
    try:
        with conn.transaction(), conn.cursor() as cur:
            stored = _insert_readings(cur, readings)
            if readings:
                cur.executemany(UPSERT_STATUS, ranges(readings))
            if rejected:
                cur.executemany(INSERT_REJECTED, rejected)
        return len(readings) - stored, readings
    except psycopg.DatabaseError as batch_error:
        log.warning("batch failed (%s); storing readings one at a time", batch_error)

    good, stored = [], 0
    with conn.transaction(), conn.cursor() as cur:
        for r in readings:
            try:
                with conn.transaction():        # savepoint: a failure undoes only this reading
                    stored += _insert_readings(cur, [r])
                good.append(r)
            except psycopg.DatabaseError as e:
                log.error("reading from %s seq %s not stored: %s", r.get("serial"), r["seq_no"], e)
                rejected.append(_unstorable(r, e))
        if good:
            cur.executemany(UPSERT_STATUS, ranges(good))
        if rejected:
            cur.executemany(INSERT_REJECTED, rejected)
    return len(good) - stored, good


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    kafka.ensure_topics()
    url = get_settings().db_url("app").replace("postgresql+psycopg://", "postgresql://")
    consumer = kafka.consumer("storage-writer", [kafka.READINGS, kafka.READINGS_REJECTED])
    producer = kafka.producer()
    with psycopg.connect(url, autocommit=True) as conn:  # each transaction() block commits
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
            duplicates, committed = store(conn, readings, rejected)
            # Announce every range, including duplicates: if a previous run crashed after the database
            # commit but before announcing, this is what makes detection see those readings.
            for rng in ranges(committed):
                producer.produce(kafka.READINGS_STORED, key=rng["device_id"], value=json.dumps(rng))
            if producer.flush(30) > 0:
                raise RuntimeError("could not publish stored ranges; not committing offsets")
            consumer.commit(asynchronous=False)
            if duplicates:
                log.info("discarded %d duplicate readings", duplicates)


if __name__ == "__main__":
    main()
