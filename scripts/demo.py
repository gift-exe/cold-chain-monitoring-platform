"""Demo feeder for the dashboard: keeps the demo network (seeds/dev_z_demo_network.sql) reporting, with a
few scripted conditions so that every status appears on the map. Development only; the calibrated
simulator is iteration I7.

On start it uploads the last three hours of readings for every demo logger, then sends one reading per
logger per minute until stopped. Relative to the start time:
- Dala Demo PHC 1: heat excursion for the last 45 minutes, continuing (critical);
- Nassarawa Demo PHC 2: below freezing for the last 15 minutes, continuing (critical);
- Gwale Demo PHC 2: mains power off for the last 25 minutes, temperature still normal;
- Gwale Demo PHC 3: stopped reporting 20 minutes ago (stale, then device silence);
- an outreach team from the Gwale LGA store, logger DEMO-MOB-0001, travelling west with GPS positions.
Restarting starts the scripted conditions again from the new start time.

Run: uv run python scripts/demo.py      (or: make demo)
"""
import json
import random
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import psycopg

from coldchain.config import get_settings
from coldchain.simulator.basic import SimDevice, connect, topic

ROOT = Path(__file__).resolve().parents[1]
HISTORY = timedelta(hours=3)
INTERVAL = timedelta(minutes=1)
GWALE_STORE = "8a3c1f52-2b7e-4c1d-9d6a-0f4e5b6c7d11"
PRODUCT = "5c1d0a00-0000-4000-8000-000000000001"
ROUTE = [(8.4930, 11.9870), (8.4800, 11.9800), (8.4620, 11.9740), (8.4450, 11.9690), (8.4300, 11.9610)]
TRIP = timedelta(minutes=120)        # time to travel the route
TRIP_START = timedelta(minutes=40)   # the team left this long before the feeder started


def scenario(serial: str, t0: datetime):
    """For a logger, a function of event time giving (temperature override, power_ok, silent)."""
    def at(t: datetime):
        m = (t - t0).total_seconds() / 60
        if serial == "DEMO-FIX-E21" and m >= -45:
            return 10.4, True, False
        if serial == "DEMO-FIX-E32" and m >= -15:
            return -1.6, True, False
        if serial == "DEMO-FIX-E12" and m >= -25:
            return None, False, False
        if serial == "DEMO-FIX-E13" and m >= -20:
            return None, True, True
        return None, True, False
    return at


def position(t: datetime, left: datetime) -> tuple[float, float]:
    """Linear interpolation along ROUTE by elapsed time, with a little GPS noise."""
    f = min(max((t - left) / TRIP, 0.0), 1.0) * (len(ROUTE) - 1)
    i = min(int(f), len(ROUTE) - 2)
    (x0, y0), (x1, y1) = ROUTE[i], ROUTE[i + 1]
    k = f - i
    return x0 + (x1 - x0) * k + random.gauss(0, 0.0002), y0 + (y1 - y0) * k + random.gauss(0, 0.0002)


def reading(dev: SimDevice, t: datetime, rng: random.Random, temp: float | None, power_ok: bool,
            lonlat: tuple[float, float] | None = None) -> dict:
    minute = t.timestamp() / 60
    r = {"seq_no": int(t.timestamp() // 60),         # one per minute: re-sent readings are duplicates
         "event_time": t.isoformat(),
         "temp": round(dev.normal_temp(minute, rng) if temp is None else temp + rng.gauss(0, 0.1), 1),
         "power_ok": power_ok, "door_open": False}
    if lonlat:
        r["lon"], r["lat"] = round(lonlat[0], 5), round(lonlat[1], 5)
    return r


def send(client, serial: str, readings: list[dict]) -> None:
    if readings:
        msg = {"device_id": serial, "readings": readings} if len(readings) > 1 else {"device_id": serial, **readings[0]}
        while True:   # keep going through broker restarts and the laptop sleeping
            try:
                info = client.publish(topic(serial), json.dumps(msg), qos=1)
                info.wait_for_publish(timeout=30)
                if info.is_published():
                    return
                raise RuntimeError("not acknowledged within 30 s")
            except (RuntimeError, ValueError) as e:
                print(f"publish failed ({e}); reconnecting in 5 s", flush=True)
                time.sleep(5)
                try:
                    client.reconnect()
                except OSError as e:
                    print(f"reconnect failed: {e}", flush=True)


def token(username: str) -> str:
    s = get_settings()
    realm = json.loads((ROOT / "infra/keycloak/coldchain-realm.json").read_text())
    password = next(u["credentials"][0]["value"] for u in realm["users"] if u["username"] == username)
    r = httpx.post(f"{s.keycloak_public_url}/realms/{s.keycloak_realm}/protocol/openid-connect/token", timeout=10,
                   data={"grant_type": "password", "client_id": "coldchain-dev-cli", "username": username,
                         "password": password, "scope": "openid"})
    r.raise_for_status()
    return r.json()["access_token"]


def outreach_trip(conn) -> None:
    """Create and hand over the outreach consignment through the API, unless its logger is already out."""
    if conn.execute("""SELECT 1 FROM device_assignment da JOIN device d ON d.id = da.device_id
                       WHERE d.serial_no = 'DEMO-MOB-0001' AND da.valid_to IS NULL""").fetchone():
        return
    api = httpx.Client(base_url="http://localhost:8000", timeout=20)
    lga, team = {"Authorization": f"Bearer {token('test.lga')}"}, {"Authorization": f"Bearer {token('test.outreach')}"}
    r = api.post("/consignments", headers=lga, json={
        "product_id": PRODUCT, "quantity": 120, "origin_facility_id": GWALE_STORE,
        "destination": "Outreach: Kofar Waika settlements", "expected_transit_min": 150,
        "device_serial": "DEMO-MOB-0001"})
    r.raise_for_status()
    cid = r.json()["consignment_id"]
    api.post(f"/consignments/{cid}/release", headers=lga, json={"to_user": "test.outreach"}).raise_for_status()
    api.post(f"/consignments/{cid}/confirm", headers=team).raise_for_status()
    print("outreach consignment handed to test.outreach")


def main() -> None:
    rng = random.Random(7)
    t0 = datetime.now(UTC).replace(second=0, microsecond=0)
    with psycopg.connect(get_settings().db_url("app").replace("postgresql+psycopg://", "postgresql://"),
                         autocommit=True) as conn:
        fixed = [r[0] for r in conn.execute(
            "SELECT serial_no FROM device WHERE serial_no LIKE 'DEMO-FIX-%' AND status = 'active' ORDER BY 1")]
        if not fixed:
            sys.exit("no demo loggers: run `make seed` first")
        outreach_trip(conn)
    devices = {s: SimDevice(s, phase=rng.random()) for s in fixed}
    scenarios = {s: scenario(s, t0) for s in fixed}
    left = t0 - TRIP_START
    mobile = SimDevice("DEMO-MOB-0001", low=4.0, high=6.0, cycle_minutes=90, phase=0.3)
    client = connect(f"demo-feeder-{rng.randrange(10**6)}")

    def readings_at(t: datetime) -> dict[str, list[dict]]:
        out = {}
        for s, dev in devices.items():
            temp, power_ok, silent = scenarios[s](t)
            if not silent:
                out[s] = [reading(dev, t, rng, temp, power_ok)]
        if t >= left:
            out["DEMO-MOB-0001"] = [reading(mobile, t, rng, None, True, position(t, left))]
        return out

    # history, uploaded in one batch per logger
    history: dict[str, list[dict]] = {}
    t = t0 - HISTORY
    while t < t0:
        for s, rs in readings_at(t).items():
            history.setdefault(s, []).extend(rs)
        t += INTERVAL
    for s, rs in history.items():
        send(client, s, rs)
    print(f"uploaded {sum(map(len, history.values()))} readings of history for {len(history)} loggers")

    try:
        t = t0
        while True:
            for s, rs in readings_at(t).items():
                send(client, s, rs)
            print(f"{t:%H:%M} sent readings", flush=True)
            t += INTERVAL
            time.sleep(max(0.0, (t - datetime.now(UTC)).total_seconds()))
    except KeyboardInterrupt:
        pass
    finally:
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    main()
