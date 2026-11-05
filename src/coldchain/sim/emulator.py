"""Device emulator (Section 3.3.5): publishes the simulation's readings to the MQTT broker.

Readings are sent as the ingestion bridge expects them (FR1.1): one message per reading while a device is
connected, and buffered readings uploaded in batches when it reconnects. Devices are grouped into MQTT
connections (Table 3.7: 20,000 devices over at least 2,000 connections), all served by one network thread.

Two modes:
- fast: messages are published as soon as the simulation produces them, with back-pressure so that no more
  than `max_pending` messages wait for the broker's acknowledgement, and optionally at most `max_rate` a
  second;
- paced (real time, or a speed-up): each device's readings are spread over the reporting interval by a
  fixed per-device offset, so the broker sees a steady rate rather than a burst at every simulated minute.
"""
import heapq
import json
import selectors
import threading
import time
import zlib
from collections import Counter

import paho.mqtt.client as mqtt

BATCH_SIZE = 500     # readings per uploaded batch (as coldchain.simulator.basic)


class MqttSink:
    def __init__(self, host: str, port: int, serials: list[str], connections: int, client_prefix: str = "sim",
                 topic_prefix: str = "devices", paced_interval_s: float | None = None, max_pending: int = 20000,
                 max_rate: float | None = None):
        self.topic_prefix = topic_prefix
        self.n = max(1, min(connections, len(serials)))
        self.paced = paced_interval_s
        self.max_pending = max_pending
        # Fast mode only: at most this many messages a second. The broker acknowledges a message once it
        # has it, not once the bridge has it; faster than the bridge consumes, its queue for the bridge
        # overflows (max_queued_messages) and messages are dropped (found in I8).
        self.max_rate = max_rate
        self._next_send = time.monotonic()
        self.stats = Counter()
        self._pending = 0
        self._cond = threading.Condition()
        self._queue: list[tuple[float, int, str, bytes]] = []
        self._qseq = 0
        self._stop = False
        self.clients = []
        for i in range(self.n):
            c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"{client_prefix}-{i:05d}")
            c.max_inflight_messages_set(100)
            c.on_publish = self._acked
            c.connect(host, port, keepalive=120)
            c.socket().setblocking(False)
            self.clients.append(c)
        self._net = threading.Thread(target=self._network, daemon=True, name="mqtt-network")
        self._net.start()
        self._pacer = threading.Thread(target=self._drain, daemon=True, name="mqtt-pacer") if self.paced else None
        if self._pacer:
            self._pacer.start()

    # ---- network thread: one selector over every connection ----------------------------------------------
    def _network(self):
        sel = selectors.DefaultSelector()
        for c in self.clients:
            sel.register(c.socket(), selectors.EVENT_READ, c)
        last_misc = 0.0
        while not self._stop:
            for key, _ in sel.select(timeout=0.05):
                key.data.loop_read()
            for c in self.clients:
                if c.want_write():
                    c.loop_write()
            if time.monotonic() - last_misc > 1:
                for c in self.clients:
                    c.loop_misc()
                last_misc = time.monotonic()

    def _acked(self, client, userdata, mid, reason_code, properties):
        with self._cond:
            self._pending -= 1
            self.stats["acked"] += 1
            self._cond.notify_all()

    # ---- sending -----------------------------------------------------------------------------------------
    def _client(self, serial: str) -> mqtt.Client:
        return self.clients[zlib.crc32(serial.encode()) % self.n]

    def _publish(self, serial: str, payload: bytes):
        if self.max_rate:
            self._next_send = max(self._next_send + 1 / self.max_rate, time.monotonic() - 1)
            if (wait := self._next_send - time.monotonic()) > 0:
                time.sleep(wait)
        with self._cond:
            while self._pending >= self.max_pending:
                self._cond.wait(1)
            self._pending += 1
        self._client(serial).publish(f"{self.topic_prefix}/{serial}/readings", payload, qos=1)
        self.stats["messages"] += 1

    def send(self, serial: str, readings: list[dict]):
        self.stats["readings"] += len(readings)
        if len(readings) == 1:
            msgs = [{"device_id": serial, **readings[0]}]
        else:
            self.stats["batches"] += 1
            msgs = [{"device_id": serial, "readings": readings[i:i + BATCH_SIZE]}
                    for i in range(0, len(readings), BATCH_SIZE)]
        for m in msgs:
            payload = json.dumps(m, separators=(",", ":")).encode()
            if self.paced:
                offset = (zlib.crc32(serial.encode()) % 1000) / 1000 * self.paced
                with self._cond:
                    heapq.heappush(self._queue, (time.monotonic() + offset, self._qseq, serial, payload))
                    self._qseq += 1
                    self._cond.notify_all()
            else:
                self._publish(serial, payload)

    def _drain(self):
        while True:
            with self._cond:
                while not self._queue or self._queue[0][0] > time.monotonic():
                    if self._stop and not self._queue:
                        return
                    wait = (self._queue[0][0] - time.monotonic()) if self._queue else 0.5
                    self._cond.wait(max(0.001, min(wait, 0.5)))
                _, _, serial, payload = heapq.heappop(self._queue)
            self._publish(serial, payload)

    def close(self, timeout: float = 60):
        deadline = time.monotonic() + timeout
        while self._queue and time.monotonic() < deadline:     # paced messages still waiting to go
            time.sleep(0.1)
        deadline = time.monotonic() + timeout
        with self._cond:
            while self._pending > 0 and time.monotonic() < deadline:
                self._cond.wait(0.5)
        self._stop = True
        self._net.join(5)
        for c in self.clients:
            c.disconnect()
        self.stats["unacked"] = self._pending
