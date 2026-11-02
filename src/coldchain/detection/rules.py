"""Rule-based detection of integrity events (Table 3.17), as pure functions over a device's readings.

The service recomputes these over a device's stored readings whenever new readings arrive for it, in
event-time order, so late and out-of-order readings are handled by recomputation (FR2.1, FR2.2).

Design rules applied here (Section 3.4.4):
- A condition becomes an integrity event only after it has lasted its tolerance period.
- It ends only after readings have been back in range for HYSTERESIS (FR3.3).
- Nothing is inferred across a gap in the data: an episode cannot span a gap longer than the device's
  maximum gap, and a gap is never treated as in range (FR3.2).
"""
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from itertools import pairwise

HYSTERESIS = timedelta(minutes=10)        # FR3.3
POWER_TOLERANCE = timedelta(minutes=10)   # Table 3.17
DOOR_TOLERANCE = timedelta(minutes=5)
SENSOR_ACCURACY = 0.5                     # °C, prototype assumption (Section 3.4.4)
GAP_INTERVALS = 3                         # more than 3 reporting intervals without a reading is a gap
# Power loss becomes critical when temperature is rising towards the upper limit. Prototype assumption:
# a rise of at least 1 °C during the outage and a latest temperature within 2 °C of the upper limit.
POWER_CRITICAL_RISE = 1.0
POWER_CRITICAL_MARGIN = 2.0


@dataclass(frozen=True)
class Limits:
    """Thresholds that apply to one reading (from its consignment's product, or the default profile)."""
    upper: float
    freeze: float
    freeze_sensitive: bool
    heat_tolerance: timedelta
    freeze_tolerance: timedelta


@dataclass(frozen=True)
class Reading:
    t: datetime                 # event time
    temp: float
    limits: Limits
    power_ok: bool | None = None
    door_open: bool | None = None
    alarm: bool | None = None


@dataclass
class Episode:
    """A run of readings in which a condition holds, allowing brief returns shorter than HYSTERESIS."""
    first: int                       # index of the first reading in the run
    last_abnormal: int               # index of the last reading in which the condition held
    end: datetime | None = None      # event end; None while continuing
    ended_by_gap: bool = False

    def duration(self, readings: Sequence[Reading]) -> timedelta:
        until = self.end or readings[self.last_abnormal].t
        return until - readings[self.first].t


@dataclass(frozen=True)
class Detected:
    """An integrity event found in a device's readings."""
    type: str
    severity: str
    start: datetime
    end: datetime | None
    peak: float | None
    borderline: bool = False
    details: dict = field(default_factory=dict)


def episodes(readings: Sequence[Reading], condition: Callable[[Reading], bool | None],
             max_gap: timedelta, hysteresis: timedelta = HYSTERESIS) -> list[Episode]:
    """Find runs where condition holds. Readings must be sorted by event time.

    condition returns None when a reading carries no value for it (e.g. no door sensor); such readings
    are treated as missing data, not as normal.
    """
    found: list[Episode] = []
    current: Episode | None = None
    recovering_since: int | None = None    # index of the first normal reading after the condition
    prev_t: datetime | None = None

    for i, r in enumerate(readings):
        state = condition(r)
        if state is None:
            continue
        if current and prev_t and r.t - prev_t > max_gap:
            # Data gap: the episode ends at the last observation; nothing is assumed about the gap
            current.end = readings[recovering_since].t if recovering_since is not None else prev_t
            current.ended_by_gap = recovering_since is None
            found.append(current)
            current, recovering_since = None, None
        prev_t = r.t

        if state:
            if current is None:
                current = Episode(first=i, last_abnormal=i)
            current.last_abnormal = i
            recovering_since = None
        elif current:
            if recovering_since is None:
                recovering_since = i
            if r.t - readings[recovering_since].t >= hysteresis:
                current.end = readings[recovering_since].t
                found.append(current)
                current, recovering_since = None, None

    if current:
        found.append(current)   # still continuing (end is None), or recovering but not yet for HYSTERESIS
    return found


def max_gap_for(interval: timedelta) -> timedelta:
    return interval * GAP_INTERVALS


def _peak(readings: Sequence[Reading], ep: Episode, highest: bool) -> float:
    temps = [r.temp for r in readings[ep.first:ep.last_abnormal + 1]]
    return max(temps) if highest else min(temps)


def _details(ep: Episode) -> dict:
    return {"ended_by": "data_gap"} if ep.ended_by_gap else {}


def detect(readings: Sequence[Reading], interval: timedelta, has_power_signal: bool,
           has_door_sensor: bool) -> list[Detected]:
    """All rule-based integrity events in a device's readings (sorted by event time)."""
    gap = max_gap_for(interval)
    out: list[Detected] = []

    for ep in episodes(readings, lambda r: r.temp > r.limits.upper, gap):
        lim = readings[ep.first].limits
        if ep.duration(readings) >= lim.heat_tolerance:
            peak = _peak(readings, ep, highest=True)
            out.append(Detected("heat_excursion", "critical", readings[ep.first].t, ep.end, peak,
                                borderline=peak - lim.upper <= SENSOR_ACCURACY, details=_details(ep)))

    freezing = episodes(readings, lambda r: r.limits.freeze_sensitive and r.temp <= r.limits.freeze, gap)
    for ep in freezing:
        lim = readings[ep.first].limits
        if ep.duration(readings) >= lim.freeze_tolerance:
            low = _peak(readings, ep, highest=False)
            out.append(Detected("freeze", "critical", readings[ep.first].t, ep.end, low,
                                borderline=lim.freeze - low <= SENSOR_ACCURACY, details=_details(ep)))

    if has_power_signal:
        for ep in episodes(readings, lambda r: None if r.power_ok is None else not r.power_ok, gap):
            if ep.duration(readings) >= POWER_TOLERANCE:
                first, last = readings[ep.first], readings[ep.last_abnormal]
                rise = last.temp - first.temp
                critical = rise >= POWER_CRITICAL_RISE and last.temp >= last.limits.upper - POWER_CRITICAL_MARGIN
                out.append(Detected("power_loss", "critical" if critical else "warning", first.t, ep.end,
                                    _peak(readings, ep, highest=True),
                                    details={**_details(ep), "temperature_rise": round(rise, 2)}))

    if has_door_sensor:
        for ep in episodes(readings, lambda r: r.door_open, gap):
            if ep.duration(readings) >= DOOR_TOLERANCE:
                out.append(Detected("door_open", "warning", readings[ep.first].t, ep.end,
                                    _peak(readings, ep, highest=True), details=_details(ep)))

    # Local alarm: any report counts; linked to the corresponding event by the incident service (I4)
    for ep in episodes(readings, lambda r: r.alarm, gap):
        out.append(Detected("local_alarm", "critical", readings[ep.first].t, ep.end,
                            _peak(readings, ep, highest=True), details=_details(ep)))
    return out


def cumulative_exposure(readings: Sequence[Reading], interval: timedelta) -> dict[str, timedelta]:
    """Time spent above range and at or below the freeze limit (FR3.4).

    Each reading stands for the time until the next reading, capped at the maximum gap, so time in a data
    gap is not counted as exposure (or as in range); it is reported separately as unobserved.
    """
    gap = max_gap_for(interval)
    above = below = unobserved = timedelta(0)
    for r, nxt in pairwise(readings):
        span = nxt.t - r.t
        if span > gap:
            unobserved += span - interval
            span = interval
        if r.temp > r.limits.upper:
            above += span
        elif r.temp <= r.limits.freeze:
            below += span
    return {"above_range": above, "at_or_below_freeze": below, "unobserved": unobserved}
