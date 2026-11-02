"""Basic device simulator: publishes steady in-range readings over MQTT.

Each device reports one reading per interval. A device that is "offline" buffers its readings and
uploads them as batches when it reconnects, as outreach loggers do (FR1.2).

Examples:
    # two devices, one hour of readings, published as fast as possible
    uv run python -m coldchain.simulator.basic TEST-FIX-0001 TEST-MOB-0001 --minutes 60

    # a device that was offline for the last 24 hours uploads its buffer
    uv run python -m coldchain.simulator.basic TEST-MOB-0001 --minutes 1440 --offline
"""
import argparse
import json
import random
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import paho.mqtt.client as mqtt

from coldchain.config import get_settings

BATCH_SIZE = 500  # readings per uploaded batch


@dataclass
class SimDevice:
    serial: str
    seq_no: int = 0
    set_point: float = 5.0
    noise_sd: float = 0.2

    def readings(self, start: datetime, minutes: int, interval_s: int, rng: random.Random) -> Iterator[dict]:
        for k in range(minutes * 60 // interval_s):
            self.seq_no += 1
            yield {"seq_no": self.seq_no,
                   "event_time": (start + timedelta(seconds=k * interval_s)).isoformat(),
                   "temp": round(self.set_point + rng.gauss(0, self.noise_sd), 1),
                   "power_ok": True, "door_open": False}


def topic(serial: str) -> str:
    return f"devices/{serial}/readings"


def connect(client_id: str) -> mqtt.Client:
    s = get_settings()
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
    client.connect(s.mqtt_host, s.mqtt_port)
    client.loop_start()
    return client


def publish_live(client: mqtt.Client, device: SimDevice, readings: Iterator[dict]) -> int:
    n = 0
    for r in readings:
        client.publish(topic(device.serial), json.dumps({"device_id": device.serial, **r}), qos=1).wait_for_publish()
        n += 1
    return n


def publish_buffered(client: mqtt.Client, device: SimDevice, readings: Iterator[dict]) -> int:
    buf = list(readings)
    for i in range(0, len(buf), BATCH_SIZE):
        msg = {"device_id": device.serial, "readings": buf[i:i + BATCH_SIZE]}
        client.publish(topic(device.serial), json.dumps(msg), qos=1).wait_for_publish()
    return len(buf)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("serials", nargs="+", help="device serial numbers (must be registered)")
    p.add_argument("--minutes", type=int, default=60, help="simulated duration")
    p.add_argument("--interval", type=int, default=60, help="seconds between readings")
    p.add_argument("--offline", action="store_true", help="buffer all readings and upload them in batches")
    p.add_argument("--start-seq", type=int, default=0, help="first sequence number minus one")
    p.add_argument("--seed", type=int, default=1)
    args = p.parse_args()

    rng = random.Random(args.seed)
    start = datetime.now(UTC) - timedelta(minutes=args.minutes)  # the period ending now
    client = connect(f"sim-basic-{rng.randrange(10**6)}")
    for serial in args.serials:
        dev = SimDevice(serial, seq_no=args.start_seq)
        readings = dev.readings(start, args.minutes, args.interval, rng)
        n = (publish_buffered if args.offline else publish_live)(client, dev, readings)
        print(f"{serial}: published {n} readings ({'buffered upload' if args.offline else 'live'})")
    client.loop_stop()
    client.disconnect()


if __name__ == "__main__":
    main()
