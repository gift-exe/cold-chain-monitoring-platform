"""Basic device simulator: publishes readings over MQTT.

Normal operation is a refrigerator cycling between about 3.5 and 6.5 °C as its compressor switches on and
off, with sensor noise. Scenarios replace parts of this with scripted conditions (an excursion, a power
cut, a door left open). Calibrated equipment, power and climate models replace this in I7 (Section 3.3.5).

Each device reports one reading per interval. A device that is "offline" buffers its readings and uploads
them as batches when it reconnects, as outreach loggers do (FR1.2).

Examples:
    # two devices, one hour of readings, published as fast as possible
    uv run python -m coldchain.simulator.basic TEST-FIX-0001 TEST-MOB-0001 --minutes 60

    # a device that was offline for the last 24 hours uploads its buffer
    uv run python -m coldchain.simulator.basic TEST-MOB-0001 --minutes 1440 --offline

    # a 45-minute heat excursion starting 20 minutes in
    uv run python -m coldchain.simulator.basic TEST-FIX-0001 --minutes 90 --excursion 20:45:10.5
"""
import argparse
import json
import math
import random
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import paho.mqtt.client as mqtt

from coldchain.config import get_settings

BATCH_SIZE = 500  # readings per uploaded batch

# Optional per-reading overrides, given the reading's index: return None to keep normal behaviour
Override = Callable[[int], object]


@dataclass
class SimDevice:
    serial: str
    seq_no: int = 0
    low: float = 3.5               # compressor switches on
    high: float = 6.5              # compressor switches off
    cycle_minutes: float = 45.0    # one full cooling cycle
    noise_sd: float = 0.2
    phase: float = field(default_factory=lambda: random.random())

    def normal_temp(self, minute: float, rng: random.Random) -> float:
        """Triangle wave between low and high (warming slowly, cooling faster), plus noise."""
        x = (minute / self.cycle_minutes + self.phase) % 1.0
        level = x / 0.7 if x < 0.7 else (1 - x) / 0.3          # 70% of the cycle warming
        return self.low + (self.high - self.low) * level + rng.gauss(0, self.noise_sd)

    def readings(self, start: datetime, minutes: int, interval_s: int, rng: random.Random,
                 temp: Override | None = None, power_ok: Override | None = None,
                 door_open: Override | None = None, alarm: Override | None = None) -> Iterator[dict]:
        for k in range(math.floor(minutes * 60 / interval_s)):
            self.seq_no += 1
            minute = k * interval_s / 60
            t = temp(k) if temp else None
            r = {"seq_no": self.seq_no,
                 "event_time": (start + timedelta(seconds=k * interval_s)).isoformat(),
                 "temp": round(self.normal_temp(minute, rng) if t is None else t + rng.gauss(0, 0.05), 1),
                 "power_ok": True if power_ok is None or power_ok(k) is None else power_ok(k),
                 "door_open": False if door_open is None or door_open(k) is None else door_open(k)}
            if alarm and alarm(k) is not None:
                r["alarm"] = alarm(k)
            yield r


def between(first: int, last: int, value) -> Override:
    """Override for readings first..last-1 (by index); normal behaviour otherwise."""
    return lambda k: value if first <= k < last else None


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
    p.add_argument("--excursion", metavar="START:MINUTES:TEMP",
                   help="hold the temperature at TEMP for MINUTES, starting START minutes in")
    p.add_argument("--seed", type=int, default=1)
    args = p.parse_args()

    rng = random.Random(args.seed)
    temp = None
    if args.excursion:
        s, m, t = args.excursion.split(":")
        per_min = 60 / args.interval
        temp = between(int(int(s) * per_min), int((int(s) + int(m)) * per_min), float(t))
    start = datetime.now(UTC) - timedelta(minutes=args.minutes)  # the period ending now
    client = connect(f"sim-basic-{rng.randrange(10**6)}")
    for serial in args.serials:
        dev = SimDevice(serial, seq_no=args.start_seq, phase=rng.random())
        readings = dev.readings(start, args.minutes, args.interval, rng, temp=temp)
        n = (publish_buffered if args.offline else publish_live)(client, dev, readings)
        print(f"{serial}: published {n} readings ({'buffered upload' if args.offline else 'live'})")
    client.loop_stop()
    client.disconnect()


if __name__ == "__main__":
    main()
