"""Register a simulated network in the prototype's database (Section 3.3.2: facilities, devices and users
are registered from simulated data), so that the ingestion bridge accepts its devices' readings.

Jurisdictions, facilities and devices are inserted if missing and devices are (re)activated; nothing
existing is deleted. `retire` marks a network's devices retired, which stops ingestion accepting them while
keeping their readings and incidents.

    uv run python -m coldchain.sim register CONFIG
    uv run python -m coldchain.sim register CONFIG --retire
"""
import uuid

import psycopg

from coldchain.sim.network import NAMESPACE, STATE_ID, Network

PHONE_BASE = 2348100000000   # simulated users' phones: +23481000xxxxx, used only with the local SMS gateway


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
            INSERT INTO facility (id, name, tier, jurisdiction_id, parent_facility_id, location, power_source, ward)
            VALUES (%s, %s, %s, %s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography, %s, %s)
            ON CONFLICT (id) DO UPDATE SET ward = EXCLUDED.ward""",
            [(s.id, s.name, s.tier, s.jurisdiction_id, s.parent_id, s.lon, s.lat, s.power_source, s.ward)
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
        people = users(net)
        cur.executemany("""
            INSERT INTO app_user (id, username, full_name, role, jurisdiction_id, facility_id, phone_number)
            VALUES (%(id)s, %(username)s, %(full_name)s, %(role)s, %(jurisdiction_id)s, %(facility_id)s, %(phone)s)
            ON CONFLICT (id) DO UPDATE SET active = true""", people)
    return {"jurisdictions": len(states) + len(lgas), "facilities": len(net.sites), "devices": len(net.units),
            "users": len(people)}


def users(net: Network) -> list[dict]:
    """The people the incident service needs (FR5.2, FR6.1): an officer-in-charge for every site, an LGA
    officer for every LGA, an outreach lead for every outreach carrier and a state officer for every load
    state (Kano's state and national officers are in the reference data). They have no dashboard login;
    they exist so that incidents have owners and escalations have recipients."""
    people = []

    def add(key: str, role: str, name: str, jurisdiction: str, facility: str | None):
        people.append({"id": str(uuid.uuid5(NAMESPACE, f"user:{key}")), "username": f"sim.{key}",
                       "full_name": name, "role": role, "jurisdiction_id": jurisdiction, "facility_id": facility,
                       "phone": f"+{PHONE_BASE + len(people) + 1}"})

    for s in net.sites:
        add(f"oic.{s.id}", "facility_oic", f"OIC, {s.name}", s.jurisdiction_id, s.id)
        if s.tier == "state_store" and s.jurisdiction_id != STATE_ID:
            add(f"state.{s.jurisdiction_id}", "state_officer", f"State officer {s.jurisdiction_id}",
                s.jurisdiction_id, None)
    for jurisdiction in sorted({s.jurisdiction_id for s in net.sites if s.tier != "state_store"}):
        add(f"lga.{jurisdiction}", "lga_officer", f"LGA officer {jurisdiction}", jurisdiction, None)
    for u in net.units:
        if u.mobile:
            store = net.site(u.site_id)
            add(f"outreach.{u.serial}", "outreach_lead", f"Outreach lead {u.serial}", store.jurisdiction_id,
                store.id)
    return people


def user_id(key: str) -> str:
    return str(uuid.uuid5(NAMESPACE, f"user:{key}"))


def retire(conn: psycopg.Connection, net: Network) -> int:
    return conn.execute("UPDATE device SET status = 'retired' WHERE serial_no = ANY(%s) AND status <> 'retired'",
                        ([u.serial for u in net.units],)).rowcount
