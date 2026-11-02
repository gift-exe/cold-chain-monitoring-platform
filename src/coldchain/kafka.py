"""Kafka topics and client factories shared by the services."""
from confluent_kafka import Consumer, Producer
from confluent_kafka.admin import AdminClient, NewTopic

from coldchain.config import get_settings

READINGS = "readings"                    # accepted readings, keyed by device id
READINGS_REJECTED = "readings.rejected"  # rejected messages with the reason

TOPICS = {READINGS: 6, READINGS_REJECTED: 1}  # name: partitions


def producer() -> Producer:
    return Producer({
        "bootstrap.servers": get_settings().kafka_bootstrap,
        "enable.idempotence": True,   # no duplicates or reordering from producer retries
        "acks": "all",
        "linger.ms": 5,
    })


def consumer(group: str, topics: list[str]) -> Consumer:
    c = Consumer({
        "bootstrap.servers": get_settings().kafka_bootstrap,
        "group.id": group,
        "enable.auto.commit": False,  # offsets are committed only after the work is stored
        "auto.offset.reset": "earliest",
    })
    c.subscribe(topics)
    return c


def ensure_topics() -> None:
    admin = AdminClient({"bootstrap.servers": get_settings().kafka_bootstrap})
    existing = admin.list_topics(timeout=10).topics
    new = [NewTopic(name, num_partitions=n, replication_factor=1) for name, n in TOPICS.items() if name not in existing]
    for future in admin.create_topics(new).values() if new else []:
        try:
            future.result()
        except Exception as e:  # another service created it first
            if "TOPIC_ALREADY_EXISTS" not in str(e):
                raise
