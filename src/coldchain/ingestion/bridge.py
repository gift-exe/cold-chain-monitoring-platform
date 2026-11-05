"""Ingestion bridge: receives device messages over MQTT, validates them and publishes them to Kafka.

No reading is lost between the broker and the event log: messages are received with QoS 1 on a
persistent session, and each MQTT message is acknowledged only after Kafka has confirmed every record
derived from it. If the bridge stops first, the broker delivers the message again; any resulting
duplicates are discarded when stored (FR1.4).

Run: uv run python -m coldchain.ingestion.bridge
"""
import json
import logging
import threading
from datetime import UTC, datetime, timedelta

import paho.mqtt.client as mqtt

from coldchain import kafka
from coldchain.config import get_settings
from coldchain.ingestion.registry import DeviceRegistry
from coldchain.messages import Rejected, parse

log = logging.getLogger("ingestion.bridge")

SUBSCRIPTION = "devices/+/readings"


class Bridge:
    def __init__(self) -> None:
        s = get_settings()
        kafka.ensure_topics()
        self.registry = DeviceRegistry()
        self.late_horizon = timedelta(hours=s.late_horizon_h) if s.late_horizon_h > 0 else None
        self.producer = kafka.producer()
        self._pending: dict[int, int] = {}   # MQTT message id -> Kafka records awaiting confirmation
        self._failed: set[int] = set()
        self._lock = threading.Lock()
        self.mqtt = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=s.bridge_client_id,
                                clean_session=False, manual_ack=True)
        self.mqtt.on_connect = self._on_connect
        self.mqtt.on_message = self._on_message
        self.mqtt.connect(s.mqtt_host, s.mqtt_port, keepalive=30)

    # MQTT -------------------------------------------------------------------

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        log.info("connected to MQTT broker (%s); subscribing to %s", reason_code, SUBSCRIPTION)
        client.subscribe(SUBSCRIPTION, qos=1)

    def _on_message(self, client, userdata, msg):
        received_at = datetime.now(UTC)  # receipt time (FR1.5)
        accepted, rejected = parse(msg.topic, msg.payload, received_at, self.late_horizon)

        records = []
        for a in accepted:
            device_uuid = self.registry.lookup(a.serial)
            if device_uuid is None:
                rejected.append(Rejected("unknown or inactive device", json.dumps(a.to_record("")), a.serial))
            else:
                records.append((kafka.READINGS, device_uuid, a.to_record(device_uuid)))
        for r in rejected:
            log.warning("rejected message on %s: %s", msg.topic, r.reason)
            records.append((kafka.READINGS_REJECTED, r.serial or "", {
                "topic": msg.topic, "serial": r.serial, "reason": r.reason, "payload": r.payload[:10000],
                "received_at": received_at.isoformat()}))

        if not records:
            client.ack(msg.mid, msg.qos)
            return
        with self._lock:
            self._pending[msg.mid] = len(records)
        for topic, key, value in records:
            self.producer.produce(topic, key=key, value=json.dumps(value),
                                  on_delivery=lambda err, _m, mid=msg.mid, qos=msg.qos: self._delivered(err, mid, qos))
        self.producer.poll(0)

    # Kafka confirmations --------------------------------------------------

    def _delivered(self, err, mid: int, qos: int) -> None:
        with self._lock:
            if err is not None:
                log.error("Kafka delivery failed for MQTT message %s: %s", mid, err)
                self._failed.add(mid)
            self._pending[mid] -= 1
            done = self._pending[mid] == 0
            if done:
                del self._pending[mid]
                failed = mid in self._failed
                self._failed.discard(mid)
        if done and not failed:
            self.mqtt.ack(mid, qos)  # only now may the broker forget the message

    def _poll_kafka(self) -> None:
        while True:
            self.producer.poll(0.1)

    def run(self) -> None:
        threading.Thread(target=self._poll_kafka, daemon=True, name="kafka-poll").start()
        self.mqtt.loop_forever()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    Bridge().run()


if __name__ == "__main__":
    main()
