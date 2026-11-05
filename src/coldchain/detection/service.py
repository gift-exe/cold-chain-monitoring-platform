"""Detection service.

Consumes readings.stored (published by the storage writer after each commit). For each device with new
readings it reloads the stored readings around them, re-runs the rules in event-time order and reconciles
the results with the integrity events already stored; created or changed events are published to
integrity-events for the incident service. Because the rules are recomputed from stored data, readings
arriving late or out of order, and replays of the same readings, give the same result (FR2, FR5.1).

A background loop detects device silence, which is the absence of readings and so cannot be triggered by
their arrival.

Run: uv run python -m coldchain.detection.service
"""
import json
import logging
import threading
import time
from datetime import datetime

import psycopg

from coldchain import kafka
from coldchain.config import get_settings
from coldchain.detection import anomaly, rules, store

log = logging.getLogger("detection.service")


def _json(value):
    return value.isoformat() if isinstance(value, datetime) else str(value)


class Detector:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.url = self.settings.db_url("app").replace("postgresql+psycopg://", "postgresql://")
        self.producer = kafka.producer()
        self.models = anomaly.load_models()

    def publish(self, events: list[dict]) -> None:
        for e in events:
            self.producer.produce(kafka.INTEGRITY_EVENTS, key=e["device_id"], value=json.dumps(e, default=_json))
            log.info("%s %s %s (%s) device %s from %s to %s", e["change"], e["severity"], e["type"], e["id"],
                     e["device_id"], e["event_start"], e["event_end"] or "ongoing")

    def evaluate(self, conn: psycopg.Connection, device_id: str, t_from: datetime, t_to: datetime) -> list[dict]:
        dev = store.device_info(conn, device_id)
        if dev is None:
            return []
        default = store.default_limits(conn)
        with conn.transaction():
            readings, lo, hi = store.load_context(conn, dev, t_from, t_to, default)
            found = rules.detect(readings, dev.interval, dev.has_power_signal, dev.has_door_sensor)
            changed = store.reconcile(conn, device_id, found, lo, hi)
            changed += anomaly.evaluate(conn, self.models, dev, t_from, t_to)
        return changed

    def run_stored_consumer(self) -> None:
        consumer = kafka.consumer("detection", [kafka.READINGS_STORED])
        with psycopg.connect(self.url, autocommit=True) as conn:  # each transaction() block commits
            while True:
                msgs = consumer.consume(num_messages=500, timeout=0.2)
                if not msgs:
                    continue
                pending: dict[str, list[datetime]] = {}   # merge ranges per device within the batch
                for m in msgs:
                    if m.error():
                        log.error("Kafka error: %s", m.error())
                        continue
                    r = json.loads(m.value())
                    lo, hi = datetime.fromisoformat(r["from"]), datetime.fromisoformat(r["to"])
                    cur = pending.setdefault(r["device_id"], [lo, hi])
                    cur[0], cur[1] = min(cur[0], lo), max(cur[1], hi)
                for device_id, (lo, hi) in pending.items():
                    self.publish(self.evaluate(conn, device_id, lo, hi))
                if self.producer.flush(30) > 0:
                    raise RuntimeError("could not publish integrity events; not committing offsets")
                consumer.commit(asynchronous=False)

    def run_silence_sweep(self) -> None:
        with psycopg.connect(self.url, autocommit=True) as conn:  # each transaction() block commits
            while True:
                try:
                    self.publish(store.sweep_silence(conn))
                    self.producer.flush(30)
                except Exception:
                    log.exception("silence sweep failed")
                time.sleep(self.settings.detection_sweep_s)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    kafka.ensure_topics()
    d = Detector()
    if d.settings.silence_sweep:
        threading.Thread(target=d.run_silence_sweep, daemon=True, name="silence-sweep").start()
    else:
        log.warning("device-silence sweep is off (evaluation replay)")
    d.run_stored_consumer()


if __name__ == "__main__":
    main()
