"""Register a simulated network in the prototype's database (Section 3.3.2: facilities, devices and users
are registered from simulated data), so that the ingestion bridge accepts its devices' readings.

Jurisdictions, facilities and devices are inserted if missing and devices are (re)activated; nothing
existing is deleted. `retire` marks a network's devices retired, which stops ingestion accepting them while
keeping their readings and incidents.

    uv run python -m coldchain.sim register CONFIG
    uv run python -m coldchain.sim register CONFIG --retire
"""
import psycopg

from coldchain.sim.network import STATE_ID, Network


def register(conn: psycopg.Connection, net: Network) -> dict:
    states = sorted({s.jurisdiction_id for s in net.sites if s.tier == "state_store"} - {STATE_ID})
    lgas = {}
    for s in net.sites:
        if s.tier != "state_store":
            lgas[s.jurisdiction_id] = s.lga
    with conn.cursor() as cur:
        cur.executemany("""INSERT INTO jurisdiction (id, level, name, parent_id) VALUES (%s, 'state', %s, 'NG')
                           ON CONFLICT (id) DO NOTHING""", [(j, f"Load state {j[-2:]}") for j in states])
        cur.executemany("""INSERT INTO jurisdiction (id, level, name, parent_id) VALUES (%s, 'lga', %s, %s)
                           ON CONFLICT (id) DO NOTHING""",
                        [(j, name, j.rsplit("-", 1)[0]) for j, name in sorted(lgas.items())])
        # parents first: state stores, then LGA stores, then facilities
        order = {"state_store": 0, "lga_store": 1, "health_facility": 2}
        cur.executemany("""
            INSERT INTO facility (id, name, tier, jurisdiction_id, parent_facility_id, location, power_source)
            VALUES (%s, %s, %s, %s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography, %s)
            ON CONFLICT (id) DO NOTHING""",
            [(s.id, s.name, s.tier, s.jurisdiction_id, s.parent_id, s.lon, s.lat, s.power_source)
             for s in sorted(net.sites, key=lambda s: order[s.tier])])
        cur.executemany("""
            INSERT INTO device (serial_no, facility_id, type, equipment_type, has_door_sensor, has_power_signal,
                                reporting_interval_s, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, 'active')
            ON CONFLICT (serial_no) DO UPDATE SET facility_id = EXCLUDED.facility_id, type = EXCLUDED.type,
                equipment_type = EXCLUDED.equipment_type, has_door_sensor = EXCLUDED.has_door_sensor,
                has_power_signal = EXCLUDED.has_power_signal,
                reporting_interval_s = EXCLUDED.reporting_interval_s, status = 'active'""",
            [(u.serial, u.site_id, "mobile" if u.mobile else "fixed", u.equipment, u.has_door_sensor,
              u.has_power_signal, u.interval_s) for u in net.units])
    return {"jurisdictions": len(states) + len(lgas), "facilities": len(net.sites), "devices": len(net.units)}


def retire(conn: psycopg.Connection, net: Network) -> int:
    return conn.execute("UPDATE device SET status = 'retired' WHERE serial_no = ANY(%s) AND status <> 'retired'",
                        ([u.serial for u in net.units],)).rowcount
