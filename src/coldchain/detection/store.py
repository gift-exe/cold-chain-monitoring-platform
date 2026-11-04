"""Database access for detection: readings with their limits, and reconciliation of integrity events."""
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

import psycopg
from psycopg.rows import dict_row

from coldchain.detection.rules import Detected, Limits, Reading

log = logging.getLogger("detection.store")

CONTEXT = timedelta(hours=2)        # readings loaded before the new ones, to see how a condition began
MAX_LOOKBACK = timedelta(hours=80)  # beyond the 72 h lateness horizon
RULE_TYPES = ("heat_excursion", "freeze", "power_loss", "door_open", "local_alarm")

FALLBACK = Limits(8.0, 0.0, True, timedelta(minutes=30), timedelta(minutes=5))


@dataclass(frozen=True)
class DeviceInfo:
    id: str
    type: str
    equipment_type: str
    interval: timedelta
    has_power_signal: bool
    has_door_sensor: bool


def default_limits(conn: psycopg.Connection) -> Limits:
    """Most restrictive limits across all products: used when a unit's contents are not known, e.g. a
    facility refrigerator holding several products (Section 3.4.4)."""
    row = conn.execute("""
        SELECT min(upper_limit), max(freeze_limit), bool_or(freeze_sensitive),
               min(heat_tolerance_min), min(freeze_tolerance_min) FROM product""").fetchone()
    if row[0] is None:
        return FALLBACK
    return Limits(float(row[0]), float(row[1]), row[2], timedelta(minutes=row[3]), timedelta(minutes=row[4]))


def device_info(conn: psycopg.Connection, device_id: str) -> DeviceInfo | None:
    row = conn.execute("""
        SELECT id::text, type, equipment_type, reporting_interval_s, has_power_signal, has_door_sensor
        FROM device WHERE id = %s""", (device_id,)).fetchone()
    if row is None:
        return None
    return DeviceInfo(row[0], row[1], row[2], timedelta(seconds=row[3]), row[4], row[5])


def load_readings(conn: psycopg.Connection, device_id: str, lo: datetime, hi: datetime,
                  default: Limits) -> list[Reading]:
    """Readings in [lo, hi] in event-time order, each with the limits of the product it held at the time
    (through the device's assignment to a consignment), or the default limits."""
    rows = conn.execute("""
        SELECT r.event_time, r.temperature, r.power_ok, r.door_open, r.alarm,
               p.upper_limit, p.freeze_limit, p.freeze_sensitive, p.heat_tolerance_min, p.freeze_tolerance_min
        FROM reading r
        LEFT JOIN device_assignment da ON da.device_id = r.device_id
             AND tstzrange(da.valid_from, da.valid_to) @> r.event_time
        LEFT JOIN consignment c ON c.id = da.consignment_id
        LEFT JOIN product p ON p.id = c.product_id
        WHERE r.device_id = %s AND r.event_time BETWEEN %s AND %s AND NOT r.late_flag
        ORDER BY r.event_time, r.seq_no""", (device_id, lo, hi)).fetchall()
    out = []
    for t, temp, power, door, alarm, up, fr, fs, ht, ft in rows:
        lim = default if up is None else Limits(float(up), float(fr), fs, timedelta(minutes=ht),
                                                timedelta(minutes=ft))
        out.append(Reading(t, float(temp), lim, power, door, alarm))
    return out


def abnormal(r: Reading) -> bool:
    """Whether a reading is in any condition a rule watches; used to decide whether to look further back."""
    lim = r.limits
    return (r.temp > lim.upper or (lim.freeze_sensitive and r.temp <= lim.freeze)
            or r.power_ok is False or bool(r.door_open) or bool(r.alarm))


def load_context(conn: psycopg.Connection, dev: DeviceInfo, t_from: datetime, t_to: datetime,
                 default: Limits) -> tuple[list[Reading], datetime, datetime]:
    """Readings around the new ones, extended backwards while the earliest is abnormal, so that an event
    that began before the new readings is seen whole. Late readings (beyond 72 h) are excluded (FR2.3)."""
    latest = conn.execute("SELECT last_event_time FROM device_status WHERE device_id = %s", (dev.id,)).fetchone()
    hi = max(t_to, latest[0]) if latest else t_to   # include later readings: late data may change an end
    lo, step = t_from - CONTEXT, CONTEXT
    while True:
        readings = load_readings(conn, dev.id, lo, hi, default)
        if not readings or not abnormal(readings[0]) or t_from - lo >= MAX_LOOKBACK:
            return readings, lo, hi
        step *= 2
        lo = max(t_from - MAX_LOOKBACK, lo - step)


def reconcile(conn: psycopg.Connection, device_id: str, detected: list[Detected],
              lo: datetime, hi: datetime) -> list[dict]:
    """Bring stored integrity events in line with what the rules now find. An event already stored that
    overlaps a detected one is the same event (its start, end or peak may change as late readings
    arrive); otherwise a new event is created. Each stored event is the same event as at most one detected
    event, taken in time order, so an event that a data gap now splits in two keeps its start and the
    later part becomes a new event. Returns the events created or changed."""
    cur = conn.cursor(row_factory=dict_row)
    existing = cur.execute("""
        SELECT id::text, type, severity, event_start, event_end, peak_value, borderline, details
        FROM integrity_event
        WHERE device_id = %s AND type = ANY(%s) AND event_start <= %s
              AND coalesce(event_end, 'infinity') >= %s
        ORDER BY event_start""", (device_id, list(RULE_TYPES), hi, lo)).fetchall()

    changed = []
    claimed: set[str] = set()   # a stored event is matched to at most one detected event
    for d in sorted(detected, key=lambda x: x.start):
        d_end = d.end or datetime.max.replace(tzinfo=d.start.tzinfo)
        same = [e for e in existing if e["id"] not in claimed and e["type"] == d.type
                and e["event_start"] <= d_end and (e["event_end"] is None or e["event_end"] >= d.start)]
        claimed.update(e["id"] for e in same)
        new = {"severity": d.severity, "event_start": d.start, "event_end": d.end,
               "peak_value": None if d.peak is None else round(d.peak, 2), "borderline": d.borderline,
               "details": d.details}
        if not same:
            row = cur.execute("""
                INSERT INTO integrity_event (device_id, type, severity, event_start, event_end, peak_value,
                                             borderline, details)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id::text, device_id::text, type, severity, event_start, event_end, peak_value,
                          borderline, details, detected_at, updated_at""",
                (device_id, d.type, d.severity, d.start, d.end, new["peak_value"], d.borderline,
                 json.dumps(d.details))).fetchone()
            changed.append({**row, "change": "created"})
            continue
        e = same[0]
        if len(same) > 1:   # late readings joined two events into one
            log.warning("device %s: %s events %s now form one event", device_id, d.type, [x["id"] for x in same])
            new["details"] = {**d.details, "merged": [x["id"] for x in same[1:]]}
        old = {k: e[k] for k in new}
        old["peak_value"] = None if e["peak_value"] is None else float(e["peak_value"])
        if old != new:
            row = cur.execute("""
                UPDATE integrity_event SET severity = %s, event_start = %s, event_end = %s, peak_value = %s,
                       borderline = %s, details = %s, updated_at = now()
                WHERE id = %s
                RETURNING id::text, device_id::text, type, severity, event_start, event_end, peak_value,
                          borderline, details, detected_at, updated_at""",
                (d.severity, d.start, d.end, new["peak_value"], d.borderline, json.dumps(new["details"]),
                 e["id"])).fetchone()
            changed.append({**row, "change": "updated"})
    return changed


# Fixed devices: silent after three reporting intervals without a reading. Mobile loggers upload when
# they reach a network, so they are silent only while assigned to a consignment and only after their
# synchronisation window passes without an upload (Table 3.17); a logger that has never uploaded since
# its assignment is measured from the assignment.
SILENCE_START = """
    WITH contact AS (
        SELECT d.id, d.type, d.reporting_interval_s, d.sync_window_min,
               CASE WHEN d.type = 'fixed' THEN s.last_received_at
                    ELSE greatest(s.last_received_at, da.valid_from) END AS last_contact,
               CASE WHEN d.type = 'fixed' THEN make_interval(secs => 3 * d.reporting_interval_s)
                    ELSE make_interval(mins => d.sync_window_min) END AS allowed
        FROM device d
        LEFT JOIN device_status s ON s.device_id = d.id
        LEFT JOIN device_assignment da ON da.device_id = d.id AND da.valid_to IS NULL
        WHERE d.status = 'active' AND (d.type = 'fixed' OR da.device_id IS NOT NULL))
    INSERT INTO integrity_event (device_id, type, severity, event_start, details)
    SELECT c.id, 'device_silence', 'warning', c.last_contact,
           jsonb_build_object('last_contact', c.last_contact, 'device_type', c.type,
                              'reporting_interval_s', c.reporting_interval_s, 'sync_window_min', c.sync_window_min)
    FROM contact c
    WHERE c.last_contact < now() - c.allowed
      AND NOT EXISTS (SELECT 1 FROM integrity_event e WHERE e.device_id = c.id
                      AND e.type = 'device_silence' AND (e.event_end IS NULL OR e.event_start = c.last_contact))
    RETURNING id::text, device_id::text, type, severity, event_start, event_end, peak_value, borderline,
              details, detected_at, updated_at
"""
# Silence ends when the device is next heard from: the first receipt after the silence began
SILENCE_END = """
    UPDATE integrity_event e SET event_end = c.first_contact, updated_at = now()
    FROM (SELECT o.id,
                 (SELECT min(r.received_at) FROM reading r
                  WHERE r.device_id = o.device_id AND r.received_at > o.event_start
                    AND r.event_time > o.event_start - interval '80 hours') AS first_contact
          FROM integrity_event o JOIN device_status s ON s.device_id = o.device_id
          WHERE o.type = 'device_silence' AND o.event_end IS NULL
            AND s.last_received_at > o.event_start) c
    WHERE e.id = c.id AND c.first_contact IS NOT NULL
    RETURNING e.id::text, e.device_id::text, e.type, e.severity, e.event_start, e.event_end, e.peak_value,
              e.borderline, e.details, e.detected_at, e.updated_at
"""


def sweep_silence(conn: psycopg.Connection) -> list[dict]:
    """Start device-silence events for devices not heard from within their allowed period, and end those
    whose device has been heard from again (Table 3.17)."""
    cur = conn.cursor(row_factory=dict_row)
    with conn.transaction():
        started = [{**r, "change": "created"} for r in cur.execute(SILENCE_START).fetchall()]
        ended = [{**r, "change": "updated"} for r in cur.execute(SILENCE_END).fetchall()]
    return started + ended
