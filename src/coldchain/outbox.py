"""Transactional outbox: messages written in the same transaction as the change that causes them, then
relayed to Kafka. Delivery is at least once; consumers are idempotent (each message carries an id)."""
import json
import logging
from datetime import datetime

import psycopg
from confluent_kafka import Producer

log = logging.getLogger("outbox")


def _json(value):
    return value.isoformat() if isinstance(value, datetime) else str(value)


def enqueue(conn: psycopg.Connection, topic: str, key: str | None, payload: dict) -> None:
    conn.execute("INSERT INTO outbox (topic, key, payload) VALUES (%s, %s, %s)",
                 (topic, key, json.dumps(payload, default=_json)))


def relay(conn: psycopg.Connection, producer: Producer, batch: int = 500) -> int:
    """Publish unsent messages in order and mark them sent. Returns the number relayed."""
    with conn.transaction():
        rows = conn.execute("""
            SELECT id, topic, key, payload::text FROM outbox WHERE sent_at IS NULL
            ORDER BY id LIMIT %s FOR UPDATE SKIP LOCKED""", (batch,)).fetchall()
        for _, topic, key, payload in rows:
            producer.produce(topic, key=key, value=payload)
        if rows and producer.flush(30) > 0:
            raise RuntimeError("could not relay outbox messages")
        if rows:
            conn.execute("UPDATE outbox SET sent_at = now() WHERE id = ANY(%s)", ([r[0] for r in rows],))
    return len(rows)
