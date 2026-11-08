"""Walkthrough data for the E9 round-2 evaluators: a month of plausible activity on the demo network, so
that every screen has something to show and every task (Table 3.22) can be done. Development stack only;
it adds to the development seeds (seeds/*.sql), which the tests rely on, and is meant for a freshly reset
database:

    make reset && make dev COMPOSE="podman compose -f compose.yaml -f compose.local-sms.yaml"
    uv run python scripts/e9_data.py
    make demo            # keeps the loggers reporting; live incidents start from it

What it adds (every name, number and facility below is invented; places and wards are real):
- vaccines: six more products with the 2-8 °C range, each marked damaged by freezing or not. The
  tolerances are the system's defaults, not values checked against WHO guidance;
- a second state (Kaduna, one LGA, a state store, an LGA store and two health facilities, each with a
  logger), so the national officer's breakdowns have more than one row;
- people: an officer-in-charge for every demo health facility, an LGA officer for Dala, Nassarawa and
  Kaduna North, a Kaduna state officer and a Dala outreach team lead, all on SMS only (fake +234 numbers;
  every SMS goes to the local gateway emulator), so incidents have real owners and escalation has someone
  to reach at each level;
- 30 days of incident history (about three a day), written with the times they would have happened at:
  readings around each event, the audit trail (created, notified, escalated, acknowledged, resolved,
  closed), and response times that differ by LGA (Gwale quick, Dala slow and often escalated) and improve
  over the month, so the performance page has trends and a ranking. Two recent incidents are resolved
  and waiting for review (T3);
- shipments: completed journeys over the month (store -> outreach team, store -> facility -> outreach
  team, one refused and re-sent), and current ones in each state: at the Gwale store ready to hand over
  (T4), handed to the Gwale test facility and waiting for confirmation, refused and back with the
  sender, held at the facility ready to go on to an outreach team, and one not confirmed in time (the
  incident service raises its custody gap within a minute). The demo feeder adds the outreach trip with
  GPS and temperature (T6).

Running it twice adds a second month of history; reset first.
"""
import random
from datetime import UTC, datetime, timedelta

import psycopg
from psycopg.types.json import Jsonb

from coldchain import custody
from coldchain.config import get_settings

R = random.Random(9)
NOW = datetime.now(UTC).replace(microsecond=0)
DAYS = 30
F = "8a3c1f52-2b7e-4c1d-9d6a-0f4e5b6c7"          # facility id prefix used by the seeds
U = "0b1e5a10-0000-4000-8000-"                    # user id prefix used by the seeds

STATE_STORE, GWALE_STORE, GWALE_TEST = F + "d10", F + "d11", F + "d01"
DALA_STORE, NAS_STORE = F + "e01", F + "e02"
PHCS = {"NG-KN-GWL": [F + "e11", F + "e12", F + "e13"], "NG-KN-DAL": [F + "e21", F + "e22", F + "e23"],
        "NG-KN-NAS": [F + "e31", F + "e32", F + "e33"], "NG-KD-KDN": [F + "f21", F + "f22"]}
KD_STATE_STORE, KD_LGA_STORE = F + "f10", F + "f11"

USERS = {k: U + f"{n:012d}" for k, n in [("national", 1), ("state", 2), ("lga", 3), ("oic", 4), ("outreach", 5),
                                         ("driver", 6), ("admin", 7)]}
PENTA, MR = "5c1d0a00-0000-4000-8000-000000000001", "5c1d0a00-0000-4000-8000-000000000002"


def uid(n: int) -> str:
    return U + f"{100 + n:012d}"


# name, freeze sensitive (WHO lists these by type; the tolerances are left at the defaults)
PRODUCTS = [("BCG", False), ("bOPV (oral polio)", False), ("PCV (pneumococcal)", True), ("IPV (inactivated polio)", True),
            ("HPV", True), ("Td (tetanus-diphtheria)", True)]

PEOPLE = [  # key, name, role, jurisdiction, facility
    ("oic_e11", "Aisha Bello", "facility_oic", "NG-KN-GWL", F + "e11"),
    ("oic_e12", "Musa Garba", "facility_oic", "NG-KN-GWL", F + "e12"),
    ("oic_e13", "Halima Yusuf", "facility_oic", "NG-KN-GWL", F + "e13"),
    ("oic_e21", "Ibrahim Sani", "facility_oic", "NG-KN-DAL", F + "e21"),
    ("oic_e22", "Zainab Abdullahi", "facility_oic", "NG-KN-DAL", F + "e22"),
    ("oic_e23", "Sadiq Umar", "facility_oic", "NG-KN-DAL", F + "e23"),
    ("oic_e31", "Fatima Lawal", "facility_oic", "NG-KN-NAS", F + "e31"),
    ("oic_e32", "Usman Danjuma", "facility_oic", "NG-KN-NAS", F + "e32"),
    ("oic_e33", "Rukayya Isa", "facility_oic", "NG-KN-NAS", F + "e33"),
    ("oic_f21", "Grace Audu", "facility_oic", "NG-KD-KDN", F + "f21"),
    ("oic_f22", "Samuel Bako", "facility_oic", "NG-KD-KDN", F + "f22"),
    ("lga_dal", "Abubakar Shehu", "lga_officer", "NG-KN-DAL", None),
    ("lga_nas", "Maryam Aliyu", "lga_officer", "NG-KN-NAS", None),
    ("lga_kdn", "Peter Yakubu", "lga_officer", "NG-KD-KDN", None),
    ("state_kd", "Hauwa Mohammed", "state_officer", "NG-KD", None),
    ("out_dal", "Kabiru Adamu", "outreach_lead", "NG-KN-DAL", None),
]
P = {k: uid(i) for i, (k, *_) in enumerate(PEOPLE)}
OIC = {fac: P[k] for k, _, role, _, fac in PEOPLE if role == "facility_oic"}
OIC[GWALE_TEST] = USERS["oic"]
LGA_OFFICER = {"NG-KN-GWL": USERS["lga"], "NG-KN-DAL": P["lga_dal"], "NG-KN-NAS": P["lga_nas"], "NG-KD-KDN": P["lga_kdn"]}
STATE_OFFICER = {"NG-KN": USERS["state"], "NG-KD": P["state_kd"]}


def structure(conn: psycopg.Connection) -> None:
    for i, (name, fs) in enumerate(PRODUCTS):
        conn.execute("""INSERT INTO product (id, name, lower_limit, upper_limit, freeze_sensitive)
                        VALUES (%s, %s, 2.0, 8.0, %s) ON CONFLICT DO NOTHING""",
                     (f"5c1d0a00-0000-4000-8000-0000000001{i:02d}", name, fs))
    conn.execute("""INSERT INTO jurisdiction (id, level, name, parent_id) VALUES
                    ('NG-KD', 'state', 'Kaduna', 'NG'), ('NG-KD-KDN', 'lga', 'Kaduna North', 'NG-KD')
                    ON CONFLICT DO NOTHING""")
    for fid, name, tier, jur, parent, lon, lat, power in [
        (KD_STATE_STORE, "Kaduna State Cold Store", "state_store", "NG-KD", None, 7.4380, 10.5260, "grid_generator"),
        (KD_LGA_STORE, "Kaduna North LGA Cold Store", "lga_store", "NG-KD-KDN", KD_STATE_STORE, 7.4420, 10.5480, "solar"),
        (F + "f21", "Kaduna North Demo PHC 1", "health_facility", "NG-KD-KDN", KD_LGA_STORE, 7.4310, 10.5620, "grid"),
        (F + "f22", "Kaduna North Demo PHC 2", "health_facility", "NG-KD-KDN", KD_LGA_STORE, 7.4520, 10.5590, "solar"),
    ]:
        conn.execute("""INSERT INTO facility (id, name, tier, jurisdiction_id, parent_facility_id, location, power_source)
                        VALUES (%s, %s, %s, %s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography, %s)
                        ON CONFLICT DO NOTHING""", (fid, name, tier, jur, parent, lon, lat, power))
        conn.execute("""INSERT INTO device (serial_no, facility_id, type, equipment_type, has_door_sensor, has_power_signal)
                        VALUES (%s, %s, 'fixed', %s, true, %s) ON CONFLICT (serial_no) DO NOTHING""",
                     ("DEMO-FIX-" + fid[-3:].upper(), fid,
                      "walk_in_cold_room" if "store" in tier else "solar_direct_drive" if power == "solar"
                      else "ice_lined_refrigerator", power != "solar"))
    for i, (key, name, role, jur, fac) in enumerate(PEOPLE):
        conn.execute("""INSERT INTO app_user (id, username, full_name, role, jurisdiction_id, facility_id, phone_number)
                        VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING""",
                     (P[key], "sms." + name.lower().replace(" ", "."), name, role, jur, fac, f"+23480000{100 + i:05d}"))


# Incident history -------------------------------------------------------------------------------------

DEVICE = {}         # facility -> fixed logger id
JUR = {}            # facility -> jurisdiction

# minutes to acknowledge, by LGA: (typical low, typical high) at the start of the month; improves by ~40%
SPEED = {"NG-KN-GWL": (3, 14), "NG-KN-NAS": (6, 45), "NG-KN-DAL": (12, 150), "NG-KD-KDN": (4, 30), "NG-KN": (5, 25),
         "NG-KD": (5, 25)}
TYPES = [("heat_excursion", "critical", 0.32), ("power_loss", "warning", 0.25), ("device_silence", "warning", 0.2),
         ("door_open", "warning", 0.12), ("freeze", "critical", 0.11)]
TOLERANCE = {"heat_excursion": 30, "freeze": 5, "power_loss": 10, "device_silence": 15, "door_open": 10, "anomaly": 60}
NOTES = {
    "heat_excursion": ["Moved the vaccines to the backup ice-lined fridge; technician called about the thermostat.",
                       "Fridge door seal replaced; vial monitors checked, all usable.",
                       "Generator refuelled; temperature back in range within the hour."],
    "freeze": ["Thermostat turned up; freeze-sensitive vaccines shake-tested, none frozen.",
               "Vaccines moved away from the cold plate and the basket rearranged."],
    "power_loss": ["Switched to the generator until mains power returned.", "Solar panel cleaned; battery checked."],
    "device_silence": ["Logger battery replaced.", "Logger SIM topped up; readings resumed."],
    "door_open": ["Door closed; staff reminded to check it after each session.", "Door latch repaired."],
    "anomaly": ["Technician found the thermostat sticking; replaced.", "Door seal worn; replaced, cycling back to normal."],
}
POLICY = {"critical": [(15, "lga"), (60, "state"), (240, "national")], "warning": [(1440, "lga")]}


def act(conn, inc, action, at, actor=None, channel="system", **details):
    conn.execute("""INSERT INTO incident_action (incident_id, action, actor_id, channel, occurred_at, details)
                    VALUES (%s, %s, %s, %s, %s, %s)""", (inc, action, actor, channel, at, Jsonb(details)))


def readings(conn, device, typ, start, end, peak):
    """Five-minutely readings from an hour before the event to half an hour after it."""
    rows, t = [], start - timedelta(hours=1)
    while t <= end + timedelta(minutes=30):
        during = start <= t <= end
        temp = round(R.uniform(4.0, 6.0), 1)
        if during and typ == "heat_excursion":
            temp = round(min(peak, 8.3 + (peak - 8) * min(1, (t - start) / (end - start + timedelta(minutes=1)) * 2)), 1)
        elif during and typ == "freeze":
            temp = round(max(peak, 1.5 - (2 - peak) * min(1, (t - start) / timedelta(minutes=20))), 1)
        if not (during and typ == "device_silence"):
            rows.append((device, int(t.timestamp() // 60), t, t + timedelta(seconds=R.randint(5, 60)), temp,
                         not (during and typ == "power_loss"), during and typ == "door_open"))
        t += timedelta(minutes=5)
    with conn.cursor() as cur:
        cur.executemany("""INSERT INTO reading (device_id, seq_no, event_time, received_at, temperature, power_ok, door_open)
                           VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING""", rows)


def one_incident(conn, fac, typ, sev, start, state="closed", failed=False, quick=False, explanation=None):
    jur = JUR[fac]
    state_jur = jur if "-" not in jur[3:] else jur.rsplit("-", 1)[0]
    owner = OIC.get(fac) or LGA_OFFICER.get(jur) or STATE_OFFICER[state_jur]
    lga = LGA_OFFICER.get(jur)
    length = timedelta(minutes=R.randint(25, 170))
    end = start + length
    detected = start + timedelta(minutes=TOLERANCE[typ], seconds=R.randint(20, 90))
    if detected >= end:
        end = detected + timedelta(minutes=R.randint(10, 60))
    created = detected + timedelta(seconds=2)
    peak = {"heat_excursion": round(R.uniform(9.0, 13.5), 1), "freeze": round(R.uniform(-2.5, -0.4), 1)}.get(typ)
    device = DEVICE[fac]
    readings(conn, device, typ, start, end, peak or 5)
    details = {"explanation": explanation} if explanation else {}
    ev = conn.execute("""INSERT INTO integrity_event (device_id, type, event_start, event_end, peak_value, detected_at, details)
                         VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                      (device, typ, start, end, peak, detected, Jsonb(details))).fetchone()[0]
    # how long until someone responds: by LGA, faster as the month goes on
    lo, hi = SPEED.get(jur, SPEED.get(state_jur, (5, 30)))
    progress = (NOW - start) / timedelta(days=DAYS)          # 1 at the start of the month, 0 now
    ack_after = timedelta(minutes=R.uniform(lo, hi) * (0.6 + 0.4 * progress) * (1 if sev == "critical" else 2.5))
    if quick:                                                # acknowledged by the owner, before escalation
        ack_after = timedelta(minutes=R.uniform(3, 10))
    acked = created + ack_after
    tier, escalations = "owner", []
    for mins, t in POLICY[sev]:
        if ack_after > timedelta(minutes=mins):
            tier = t
            escalations.append((created + timedelta(minutes=mins), t))
    acker = owner if tier == "owner" else (lga if tier == "lga" and lga else STATE_OFFICER[state_jur] if tier in ("lga", "state") else USERS["national"])
    resolved = acked + timedelta(minutes=R.randint(15, 140))
    closed = resolved + timedelta(hours=R.uniform(1, 20))
    reviewer = lga if lga and lga != acker else STATE_OFFICER[state_jur]
    if state == "resolved":
        closed = None
    inc = conn.execute("""
        INSERT INTO incident (incident_key, integrity_event_id, type, severity, mode, state, owner_id, current_tier,
                              condition_cleared, created_at, notified_at, acknowledged_at, resolved_at, closed_at,
                              device_id, jurisdiction_id, ownership, received_at, detected_at, acknowledged_by,
                              resolved_by, closed_by, ack_due_from, ack_stage, resolve_due_from, notification_failed)
        VALUES (%s, %s, %s, %s, 'operational', %s, %s, %s, true, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s) RETURNING id""",
        (f"{device}:{typ}:{start.isoformat()}", ev, typ, sev, state, owner, tier, created, created, acked, resolved,
         closed, device, jur, "FR5.2: facility officer-in-charge" if fac in OIC
         else "FR5.2: facility has no officer-in-charge; LGA officer" if lga else "store: state officer",
         detected, detected, acker, acker, reviewer if closed else None, created, len(escalations), acked, failed)).fetchone()[0]
    conn.execute("UPDATE integrity_event SET incident_id = %s WHERE id = %s", (inc, ev))
    act(conn, inc, "created", created, type=typ, severity=sev, mode="operational", owner_id=str(owner),
        event_start=start.isoformat(), event_end=end.isoformat())
    act(conn, inc, "notified", created, tier="owner", stage=0, clock="acknowledge", recipients=[str(owner)])
    for k, (at, t) in enumerate(escalations, start=1):
        who = [str(x) for x in ([LGA_OFFICER.get(jur)] if t == "lga" else [STATE_OFFICER[state_jur]] if t == "state"
                                 else [USERS["national"]]) if x]
        act(conn, inc, "escalated", at, tier=t, stage=k, clock="acknowledge", recipients=who)
    act(conn, inc, "acknowledged", acked, acker, R.choice(["sms", "dashboard"]),
        tier_at_acknowledgement=tier, late=tier != "owner")
    act(conn, inc, "resolved", resolved, acker, "dashboard", note=R.choice(NOTES[typ]), condition_cleared=True)
    if closed:
        act(conn, inc, "closed", closed, reviewer, "dashboard", note=R.choice(["Checked; fine.", "Reviewed with the OIC.", ""]))


def history(conn: psycopg.Connection) -> int:
    for fid, jur, dev in conn.execute("""
            SELECT f.id::text, f.jurisdiction_id, d.id::text FROM facility f
            JOIN device d ON d.facility_id = f.id AND d.type = 'fixed' AND d.status = 'active'
            WHERE d.serial_no LIKE 'DEMO-FIX-%%' OR d.serial_no = 'TEST-FIX-0001'"""):
        DEVICE.setdefault(fid, dev)
        JUR[fid] = jur
    facilities = sorted(DEVICE)
    phcs = {f for fs in PHCS.values() for f in fs}
    weights = [3 if f == GWALE_TEST else 2 if f in phcs else 1 for f in facilities]
    n, t = 0, NOW - timedelta(days=DAYS)
    while t < NOW - timedelta(days=1):
        t += timedelta(hours=R.expovariate(1 / 8))            # about three a day
        fac = R.choices(facilities, weights)[0]
        typ, sev, _ = R.choices(TYPES, [w for *_, w in TYPES])[0]
        one_incident(conn, fac, typ, sev, t, failed=R.random() < 0.03)
        n += 1
    anomalies(conn)
    # waiting for review (T3): resolved in the last day and a half
    one_incident(conn, F + "e11", "heat_excursion", "critical", NOW - timedelta(hours=30), state="resolved", quick=True)
    one_incident(conn, GWALE_TEST, "power_loss", "warning", NOW - timedelta(hours=9), state="resolved", quick=True)
    return n + 4


def anomalies(conn) -> None:
    """Two anomaly warnings (FR4), with explanations in the form detection writes them."""
    one_incident(conn, F + "e22", "anomaly", "warning", NOW - timedelta(days=12, hours=5), explanation=(
        "Unusual pattern: 9 cooling cycles in the hour (usually 2 to 5); temperature varied by 3.4°C within the "
        "hour (usually 0.4 to 1.8°C)."))
    one_incident(conn, F + "e13", "anomaly", "warning", NOW - timedelta(hours=20), state="resolved", quick=True,
                 explanation="Unusual pattern: 2.6°C warmer than this unit's last 24 hours; average temperature 7.1°C "
                             "(usually 3.6 to 6.2°C).")


# Shipments --------------------------------------------------------------------------------------------

def event(conn, cid, typ, at, actor, frm=None, to=None, handoff=None, **details):
    conn.execute("""INSERT INTO custody_event (consignment_id, type, actor_id, from_user, to_user, handoff_id, occurred_at, details)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""", (cid, typ, actor, frm, to, handoff, at, Jsonb(details)))


def journey(conn, product, qty, origin, destination, transit, start, hops, complete=True):
    """A shipment that set out at `start` and was handed on along `hops`: (holder, receiver, outcome) with
    outcome 'confirm', 'reject:<reason>' or 'pending'. Returns its id."""
    cid = conn.execute("""INSERT INTO consignment (product_id, quantity, origin_facility_id, destination, expected_transit_min, created_at)
                          VALUES (%s, %s, %s, %s, %s, %s) RETURNING id""", (product, qty, origin, destination, transit, start)).fetchone()[0]
    holder, since, t = hops[0][0], start, start
    event(conn, cid, "create", t, holder, None, holder, quantity=qty)
    for frm, to, outcome in hops:
        if outcome == "hold":                     # created and still at the store: no handover yet
            break
        t += timedelta(minutes=R.randint(10, 90))
        window = t + timedelta(minutes=transit + 30)
        state = "pending" if outcome == "pending" else "confirmed" if outcome == "confirm" else "rejected"
        ended = None if state == "pending" else t + timedelta(minutes=R.randint(10, max(11, transit)))
        reason = outcome.split(":", 1)[1] if state == "rejected" else None
        h = conn.execute("""INSERT INTO custody_handoff (consignment_id, from_user, to_user, released_at, window_ends_at, state, ended_at, reason)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                         (cid, frm, to, t, window, state, ended, reason)).fetchone()[0]
        event(conn, cid, "release", t, frm, frm, to, h, window_ends_at=window.isoformat(), expected_transit_min=transit)
        if state == "confirmed":
            conn.execute("INSERT INTO custody_interval (consignment_id, holder_id, valid_from, valid_to) VALUES (%s, %s, %s, %s)",
                         (cid, holder, since, ended))
            event(conn, cid, "confirm", ended, to, frm, to, h)
            holder, since, t = to, ended, ended
        elif state == "rejected":
            event(conn, cid, "reject", ended, to, frm, to, h, reason=reason)
            t = ended
            if hops[-1] != (frm, to, outcome):       # re-sent: the sender withdrew the refused handover first
                t += timedelta(minutes=R.randint(5, 30))
                conn.execute("UPDATE custody_handoff SET state = 'cancelled' WHERE id = %s", (h,))
                event(conn, cid, "cancel", t, frm, frm, to, h, reason="Repacked with conditioned ice packs")
    done = None
    if complete:
        done = t + timedelta(minutes=R.randint(60, 300))
        event(conn, cid, "complete", done, holder, holder, None)
        conn.execute("UPDATE consignment SET completed_at = %s WHERE id = %s", (done, cid))
    conn.execute("INSERT INTO custody_interval (consignment_id, holder_id, valid_from, valid_to) VALUES (%s, %s, %s, %s)",
                 (cid, holder, since, done))
    return cid


def shipments(conn: psycopg.Connection) -> None:
    products = [r[0] for r in conn.execute("SELECT id::text FROM product ORDER BY name")]
    lga, oic, out, state = USERS["lga"], USERS["oic"], USERS["outreach"], USERS["state"]
    gw = [(lga, out, "confirm")]
    chain = [(lga, oic, "confirm"), (oic, out, "confirm")]
    for d, hops, origin, dest in [
        (28, [(state, lga, "confirm")], STATE_STORE, "Gwale LGA Cold Store"),
        (26, gw, GWALE_STORE, "Outreach session, Dorayi"),
        (23, chain, GWALE_STORE, "Outreach session, Kabuga"),
        (20, [(P["lga_dal"], P["out_dal"], "confirm")], DALA_STORE, "Outreach session, Kofar Ruwa"),
        (17, [(lga, out, "reject:Two vial monitors past the discard point"), (lga, out, "confirm")], GWALE_STORE,
         "Outreach session, Sani Mainagge"),
        (14, chain, GWALE_STORE, "Outreach session, Dorayi"),
        (11, [(P["lga_dal"], P["oic_e21"], "confirm")], DALA_STORE, "Dala Demo PHC 1"),
        (8, gw, GWALE_STORE, "Outreach session, Kofar Waika"),
        (5, [(state, lga, "confirm")], STATE_STORE, "Gwale LGA Cold Store"),
        (3, chain, GWALE_STORE, "Outreach session, Kabuga"),
    ]:
        journey(conn, R.choice(products), R.choice([50, 80, 100, 120, 200]), origin, dest, R.choice([45, 60, 90, 120]),
                NOW - timedelta(days=d, hours=R.randint(1, 8)), hops)
    kaduna(conn, products)
    # current: refused this morning, back with the Gwale LGA officer (who needs to withdraw it)
    journey(conn, PENTA, 100, GWALE_STORE, "Outreach session, Dorayi", 60, NOW - timedelta(hours=4),
            [(lga, out, "reject:Carrier arrived warm; ice packs not conditioned")], complete=False)
    # current: received by the Gwale test facility earlier today, ready to go on to an outreach team
    journey(conn, MR, 60, GWALE_STORE, "Outreach session, Sani Mainagge", 45, NOW - timedelta(hours=6),
            [(lga, oic, "confirm")], complete=False)
    # current: handed to the Dala outreach lead three hours ago and never confirmed; the incident
    # service's sweep finds the pending handover past its window and raises a custody gap incident
    journey(conn, products[0], 150, DALA_STORE, "Outreach session, Gwammaja", 60, NOW - timedelta(hours=3, minutes=20),
            [(P["lga_dal"], P["out_dal"], "pending")], complete=False)


def kaduna(conn: psycopg.Connection, products: list[str]) -> None:
    """Kaduna North: one completed delivery to a facility and one held at the LGA store, so the national
    officer's breakdown by state has two rows."""
    journey(conn, R.choice(products), 80, KD_LGA_STORE, "Kaduna North Demo PHC 1", 40, NOW - timedelta(days=9),
            [(P["lga_kdn"], P["oic_f21"], "confirm")])
    journey(conn, R.choice(products), 120, KD_LGA_STORE, "Kaduna North Demo PHC 2", 60, NOW - timedelta(hours=2),
            [(P["lga_kdn"], P["lga_kdn"], "hold")], complete=False)


def live(conn: psycopg.Connection) -> None:
    """Current shipments made through the custody rules themselves, so their SMS codes and timers are real."""
    bcg = conn.execute("SELECT id::text FROM product WHERE name = 'BCG'").fetchone()[0]
    now = conn.execute("SELECT now()").fetchone()[0]
    # at the Gwale store, ready to hand over (T4)
    custody.create(conn, USERS["lga"], bcg, 200, GWALE_STORE, "Outreach session, Dorayi", 90, None, now)
    # handed to the Gwale test facility just now; the officer has an hour and a quarter to confirm
    cid = custody.create(conn, USERS["lga"], PENTA, 100, GWALE_STORE, "Gwale Test Health Facility", 45, None, now)
    custody.release(conn, USERS["lga"], cid, USERS["oic"], now)


def main() -> None:
    url = get_settings().db_url("owner").replace("postgresql+psycopg://", "postgresql://")
    with psycopg.connect(url) as conn:
        structure(conn)
        n = history(conn)
        shipments(conn)
        live(conn)
    print(f"E9 walkthrough data loaded: {len(PRODUCTS)} more products, Kaduna, {len(PEOPLE)} people, "
          f"{n} past incidents, 15 past and current shipments plus two live ones.")
    print("Next: restart the incident service (it starts timers for open incidents and handovers), then run `make demo`.")


if __name__ == "__main__":
    main()
