"""Simulation engine (Section 3.3.5) on SimPy's discrete-event clock, in steps of one simulated minute.

Processes:
- clock: every minute, updates ambient temperature (climate model), power availability (power model),
  thermostats and the internal temperature of every unit (equipment model), then emits readings through
  the sensor model.
- grid: one per grid-connected unit, an alternating renewal process of supply and outage.
- fuel: one per generator-backed unit, interrupting the generator's fuel supply.
- injector: one per failure type, introducing labelled failures (ground truth).
- link: one per unit, the connectivity model's alternating periods online and offline.

Mobile units (outreach vaccine carriers) sit in their LGA store's cold room between trips, so their
temperature follows the store's; on an outreach trip (movement model) the carrier warms passively towards
ambient, reports its position along the route, and is offline from departure to return.

Outputs: the failure injector's ground-truth labels, the true condition windows of every unit (above the
upper limit, at or below the freezing limit, without power, door open), and summary statistics used by the
calibration checks. Readings go to a sink: none, a CSV file or the MQTT broker.
"""
import csv
import gzip
import json
import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import simpy

from coldchain.sim.config import RunConfig
from coldchain.sim.movement import Movement, Trip
from coldchain.sim.network import Network
from coldchain.sim.params import EQUIPMENT, GRID_BANDS
from coldchain.sim.sources import CLIMATE

WAT = 1   # West Africa Time is UTC+1


def step(T: np.ndarray, k: np.ndarray, ta: np.ndarray, c: np.ndarray, running: np.ndarray, target: np.ndarray,
         dt: float = 1.0) -> np.ndarray:
    """Equipment model, Section 3.3.5: T(t + Δt) = T(t) + Δt [k (Ta − T) − c P (T − Ts)], with Δt in minutes."""
    return T + dt * (k * (ta - T) - c * running * (T - target))


class Climate:
    """Daily mean, minimum and maximum air temperature and irradiance per climate cell (NASA POWER, 2024),
    with a sinusoidal daily cycle about the daily mean, peaking in mid-afternoon. Days are matched by month
    and day, so a run in any year uses 2024's weather for that date."""

    def __init__(self, cells: list[tuple[float, float]], peak_hour_wat: float):
        index = {c: i for i, c in enumerate(cells)}
        self.days: dict[tuple[int, int], int] = {}
        rows = defaultdict(dict)
        with CLIMATE.open() as f:
            for r in csv.DictReader(f):
                d = datetime.fromisoformat(r["date"])
                self.days.setdefault((d.month, d.day), len(self.days))
                rows[(d.month, d.day)][index[(float(r["cell_lat"]), float(r["cell_lon"]))]] = r
        n_cells, n_days = len(cells), len(self.days)
        self.mean, self.amp, self.irradiance = (np.zeros((n_days, n_cells)) for _ in range(3))
        for md, d in self.days.items():
            for c, r in rows[md].items():
                self.mean[d, c] = float(r["t2m"])
                self.amp[d, c] = (float(r["t2m_max"]) - float(r["t2m_min"])) / 2
                self.irradiance[d, c] = float(r["irradiance_mj"])
        self.peak = peak_hour_wat

    def day(self, t: datetime) -> int:
        return self.days[(t.month, t.day)]

    def ambient(self, t: datetime) -> np.ndarray:
        """Air temperature in each cell at time t (UTC)."""
        hour_wat = (t.hour + t.minute / 60 + WAT) % 24
        d = self.day(t)
        return self.mean[d] + self.amp[d] * math.cos(2 * math.pi * (hour_wat - self.peak) / 24)


@dataclass
class Injection:
    id: str
    type: str
    unit: int
    start: datetime
    end: datetime
    truncated: bool
    details: dict


@dataclass
class Window:
    kind: str
    unit: int
    start: datetime
    peak: float | None
    causes: set = field(default_factory=set)


CONDITION_FOR = {"heat_excursion": "above_upper", "freeze": "at_or_below_freeze", "power_loss": "no_power",
                 "door_open": "door_open"}


class Simulation:
    def __init__(self, cfg: RunConfig, network: Network, sink=None, realtime_factor: float | None = None):
        self.cfg, self.net, self.sink = cfg, network, sink
        self.rng = np.random.default_rng(cfg.seed)
        self.env = (simpy.rt.RealtimeEnvironment(factor=realtime_factor, strict=False) if realtime_factor
                    else simpy.Environment())
        self.units = list(network.units)
        self.sites = [network.site(u.site_id) for u in self.units]
        n = len(self.units)
        self.mobile = np.array([u.mobile for u in self.units], bool)
        self.fixed = ~self.mobile
        fixed_at = {u.site_id: i for i, u in enumerate(self.units) if not u.mobile}
        self.home = np.array([fixed_at[u.site_id] if u.mobile else i for i, u in enumerate(self.units)])
        cc = cfg["connectivity"]
        self.location_class = ["mobile" if u.mobile else "urban" if (s.lga is None or s.lga in cc["urban_lgas"])
                               else "rural" for u, s in zip(self.units, self.sites, strict=True)]
        eq = [EQUIPMENT[u.equipment] for u in self.units]
        nc = cfg["network"]
        self.base_k = np.array([e.k for e in eq]) * np.where([u.degraded for u in self.units], nc["degraded_factor"], 1.0)
        self.c = np.array([e.c for e in eq])
        offset = np.array([u.thermostat_offset for u in self.units])
        self.cut_in = np.array([e.cut_in for e in eq]) + offset
        self.cut_out = np.array([e.cut_out for e in eq]) + offset
        self.target = np.array([e.cooling_target for e in eq])
        self.powered = np.array([e.powered for e in eq])
        self.T = np.array([e.start_temp for e in eq], dtype=float)
        self.T[self.mobile] = self.T[self.home[self.mobile]]
        self.cell = np.array([u.cell for u in self.units])
        power = [s.power_source for s in self.sites]
        self.has_grid = np.array([p in ("grid", "grid_generator") for p in power])
        self.has_gen = np.array([p in ("generator", "grid_generator") for p in power])
        self.solar = np.array([p == "solar" for p in power])
        self.signal = np.array([u.has_power_signal for u in self.units])
        self.door_sensor = np.array([u.has_door_sensor for u in self.units])
        self.grid_on = np.ones(n, bool)
        self.fuel_ok = np.ones(n, bool)
        self.compressor = np.zeros(n, bool)
        # active injected faults (counts, so overlapping faults of different types compose)
        self.forced_off = np.zeros(n, int)
        self.cooling_fault = np.zeros(n, int)
        self.stuck_on = np.zeros(n, int)
        self.heat_factor = np.ones(n)
        self.door_injected = np.zeros(n, int)
        self.silent = np.zeros(n, int)
        self.offline = np.zeros(n, int)
        self.offset = np.zeros(n)
        self.door_until = np.zeros(n)
        self.door_starts: dict[int, list[tuple[int, int]]] = defaultdict(list)
        s = cfg["sensor"]
        drifting = self.rng.random(n) < s["drift_share"]
        self.drift_per_min = np.where(drifting, self.rng.uniform(-1, 1, n) * s["drift_max_per_30d"] / (30 * 1440), 0.0)
        self.buffers: dict[int, list[dict]] = defaultdict(list)
        self.seq = np.zeros(n, np.int64)
        self.climate = Climate(network.cells, cfg["climate"]["peak_hour_wat"])
        # connectivity and movement
        self.link_off = np.zeros(n, bool)
        self.on_trip = np.zeros(n, bool)
        self.trip_of: dict[int, Trip] = {}
        self.trip_start: dict[int, int] = {}
        self.trips: list[Trip] = []
        self.trip_events: dict[int, list[tuple[int, Trip | None]]] = defaultdict(list)
        self.gaps: list[dict] = []
        self.offline_minutes = np.zeros(n)
        if self.mobile.any():
            index = {u.serial: i for i, u in enumerate(self.units)}
            self.trips = Movement(network, cfg).plan(np.random.default_rng([cfg.seed, 1]))
            for trip in self.trips:
                u = index[trip.team]
                self.trip_events[max(0, math.ceil((trip.depart - cfg.start).total_seconds() / 60))].append((u, trip))
                self.trip_events[math.ceil((trip.returned - cfg.start).total_seconds() / 60)].append((u, None))
        # outputs
        self.injections: list[Injection] = []
        self.active: dict[int, set[str]] = defaultdict(set)
        self.windows: dict[tuple[str, int], Window] = {}
        self.prev: dict[str, np.ndarray] = {}
        self.peaks: dict[str, np.ndarray] = {}
        self.conditions: list[dict] = []
        self.in_range = np.zeros(n)
        self.too_warm = np.zeros(n)
        self.too_cold = np.zeros(n)
        self.mains_minutes = np.zeros(n)
        self.power_minutes = np.zeros(n)
        self.ambient_sum = np.zeros((math.ceil(cfg.days) + 1, len(network.cells)))
        self.target_supply = np.full(n, np.nan)

    # ---- time --------------------------------------------------------------------------------------
    def t(self, minute: float | None = None) -> datetime:
        return self.cfg.start + timedelta(minutes=self.env.now if minute is None else minute)

    # ---- power model -------------------------------------------------------------------------------
    def grid(self, u: int):
        """Alternating renewal process: exponentially distributed supply and outage periods whose means give
        the unit's daily supply hours, drawn within its NERC band."""
        lo, hi = GRID_BANDS[self.sites[u].grid_band]
        hours = self.rng.uniform(lo, hi)
        self.target_supply[u] = hours
        mean_off = self.cfg["power"]["mean_outage_h"] * 60
        mean_on = mean_off * hours / (24 - hours)
        self.grid_on[u] = self.rng.random() < hours / 24
        while True:
            yield self.env.timeout(self.rng.exponential(mean_on if self.grid_on[u] else mean_off))
            self.grid_on[u] = not self.grid_on[u]

    def fuel(self, u: int):
        p = self.cfg["power"]
        rate = p["fuel_gaps_per_1000_days"] / 1000 / 1440
        while True:
            yield self.env.timeout(self.rng.exponential(1 / rate))
            self.fuel_ok[u] = False
            yield self.env.timeout(self.rng.uniform(*p["fuel_gap_days"]) * 1440)
            self.fuel_ok[u] = True

    # ---- connectivity model ----------------------------------------------------------------------------
    def link(self, u: int):
        """Alternating online and offline periods with exponential durations, by location class (a mobile unit
        uses its store's class while at the store)."""
        cls = self.location_class[self.home[u]]
        spec = self.cfg["connectivity"][cls]
        share, gap = spec["offline_share"], spec["mean_gap_min"]
        if share <= 0:
            return
        up = gap * (1 - share) / share
        while True:
            yield self.env.timeout(self.rng.exponential(up))
            start = self.env.now
            self.link_off[u] = True
            yield self.env.timeout(self.rng.exponential(gap))
            self.link_off[u] = False
            self.gaps.append({"serial": self.units[u].serial, "class": cls, "cause": "network",
                              "start": self.t(start).isoformat(), "end": self.t().isoformat(),
                              "minutes": round(self.env.now - start)})

    def availability(self, t: datetime) -> tuple[np.ndarray, np.ndarray]:
        """Power reaching each unit, and mains or generator supply (what a power sensor reports)."""
        p = self.cfg["power"]
        hour_wat = (t.hour + t.minute / 60 + WAT) % 24
        g0, g1 = p["generator_hours"]
        generator = self.has_gen & self.fuel_ok & (g0 <= hour_wat < g1)
        supply = (self.has_grid & self.grid_on) | generator
        irr = self.climate.irradiance[self.climate.day(t)][self.cell]
        solar_hours = p["solar_hours"] * np.minimum(1.0, irr / p["solar_full_mj"])
        sun = self.solar & (np.abs(hour_wat - 12.5) < solar_hours / 2)
        up = self.forced_off == 0
        return (supply | sun) & up, supply & up

    # ---- usage -------------------------------------------------------------------------------------
    def plan_openings(self, day_start: int):
        """Routine door openings in clinic hours, Monday to Saturday (assumption)."""
        if self.t(day_start).weekday() == 6:
            return
        us = self.cfg["usage"]
        h0, h1 = us["clinic_hours"]
        counts = self.rng.poisson(us["openings_per_day"], len(self.units)) * self.fixed
        for u in np.flatnonzero(counts):
            for _ in range(counts[u]):
                start = day_start + int(self.rng.uniform(h0 - WAT, h1 - WAT) * 60)
                self.door_starts[start].append((u, int(self.rng.integers(us["open_min"][0], us["open_min"][1] + 1))))

    # ---- failure injector --------------------------------------------------------------------------
    def eligible(self, kind: str) -> np.ndarray:
        if kind in ("heat_excursion", "freeze"):
            return np.flatnonzero(self.powered)
        if kind == "power_loss":
            return np.flatnonzero(self.powered & self.signal)
        if kind == "door_open":
            return np.flatnonzero(self.door_sensor)
        return np.arange(len(self.units))

    def injector(self, kind: str, spec: dict):
        pool = self.eligible(kind)
        if spec["rate"] <= 0 or len(pool) == 0:
            return
        per_minute = spec["rate"] / 1000 * len(pool) / 1440
        end_of_run = self.cfg.minutes
        while True:
            yield self.env.timeout(self.rng.exponential(1 / per_minute))
            if self.env.now >= end_of_run:
                return
            free =[u for u in pool if not any(i.startswith(kind) for i in self.active[u])]
            if not free:
                continue
            u = int(self.rng.choice(free))
            minutes = float(self.rng.uniform(*spec["minutes"]))
            details = {}
            if kind == "sensor_drift":
                details["offset"] = round(float(self.rng.uniform(*spec["offset"])) * self.rng.choice([-1, 1]), 2)
            if kind == "heat_excursion":
                details["heat_gain_factor"] = spec["factor"]
            start, end = self.env.now, min(self.env.now + minutes, end_of_run)
            inj = Injection(f"{kind}-{len(self.injections) + 1:05d}", kind, u, self.t(start), self.t(end),
                            self.env.now + minutes > end_of_run, details)
            self.injections.append(inj)
            self.env.process(self.apply(inj, end - start))

    def apply(self, inj: Injection, minutes: float):
        u, kind = inj.unit, inj.type
        self.active[u].add(inj.id)
        for (k, uu), w in self.windows.items():   # a condition already under way on this unit
            if uu == u and k == CONDITION_FOR.get(kind):
                w.causes.add(inj.id)
        self._fault(kind, u, inj, +1)
        yield self.env.timeout(minutes)
        self._fault(kind, u, inj, -1)
        self.active[u].discard(inj.id)     # buffered readings go with the next reading sent (emit)

    def _fault(self, kind: str, u: int, inj: Injection, sign: int):
        if kind == "heat_excursion":      # cooling fails and the cabinet is left open or its seal fails
            self.cooling_fault[u] += sign
            self.heat_factor[u] = inj.details["heat_gain_factor"] if self.cooling_fault[u] else 1.0
        elif kind == "freeze":            # thermostat stuck: the compressor runs continuously
            self.stuck_on[u] += sign
        elif kind == "power_loss":
            self.forced_off[u] += sign
        elif kind == "door_open":
            self.door_injected[u] += sign
        elif kind == "device_silence":    # the logger stops: nothing is recorded or sent
            self.silent[u] += sign
        elif kind == "connectivity_gap":  # the logger records but cannot send; it uploads on reconnecting
            self.offline[u] += sign
        elif kind == "sensor_drift":
            self.offset[u] += sign * inj.details["offset"]

    # ---- clock -------------------------------------------------------------------------------------
    def clock(self):
        cfg, lim = self.cfg, self.cfg["limits"]
        every = max(1, self.units[0].interval_s // 60) if self.units else 1
        door_factor = cfg["usage"]["door_factor"]
        s = cfg["sensor"]
        for minute in range(cfg.minutes):
            if minute % 1440 == 0:
                self.plan_openings(minute)
            for u, length in self.door_starts.pop(minute, []):
                self.door_until[u] = max(self.door_until[u], minute + length)
            for u, trip in self.trip_events.pop(minute, []):
                self.depart(u, trip, minute) if trip else self.arrive(u, minute)
            t = self.t(minute)
            ambient = self.climate.ambient(t)
            self.ambient_sum[minute // 1440] += ambient
            ta = ambient[self.cell]
            avail, supply = self.availability(t)
            self.mains_minutes += self.has_grid & self.grid_on
            self.power_minutes += avail
            # thermostat, with the injected faults
            self.compressor = np.where(self.T > self.cut_in, True, np.where(self.T < self.cut_out, False, self.compressor))
            running = self.powered & avail & ((self.compressor & (self.cooling_fault == 0)) | (self.stuck_on > 0))
            door = (self.door_until > minute) | (self.door_injected > 0)
            k = self.base_k * self.heat_factor * np.where(door & (self.cooling_fault == 0), door_factor, 1.0)
            self.T = step(self.T, k, ta, self.c, running, self.target)
            at_store = self.mobile & ~self.on_trip       # in the store's cold room between trips
            self.T[at_store] = self.T[self.home[at_store]]
            self.in_range += (self.T >= lim["lower"]) & (self.T <= lim["upper"])
            self.too_warm += self.T > lim["upper"]
            self.too_cold += self.T < lim["lower"]
            self.track(t, {"above_upper": self.T > lim["upper"], "at_or_below_freeze": self.T <= lim["freeze"],
                           "no_power": self.powered & self.signal & ~supply, "door_open": door & self.door_sensor})
            if minute % every == 0:
                self.emit(t, minute, supply, door, s)
            yield self.env.timeout(1)
        self.close_windows(self.t(cfg.minutes))

    # ---- movement ----------------------------------------------------------------------------------
    def depart(self, u: int, trip: Trip, minute: int):
        self.on_trip[u] = True
        self.trip_of[u] = trip
        self.trip_start[u] = minute

    def arrive(self, u: int, minute: int):
        self.on_trip[u] = False
        self.trip_of.pop(u, None)
        if self.cfg["connectivity"]["mobile_offline_on_trip"]:
            start = self.trip_start.pop(u)
            self.gaps.append({"serial": self.units[u].serial, "class": "mobile", "cause": "trip",
                              "start": self.t(start).isoformat(), "end": self.t(minute).isoformat(),
                              "minutes": minute - start})

    def position(self, u: int, t: datetime) -> tuple[float, float]:
        trip = self.trip_of.get(u)
        pos = trip.position(t) if trip else None
        site = self.sites[u]
        return pos if pos else (site.lon, site.lat)

    def offline_now(self) -> np.ndarray:
        trip = self.on_trip & self.cfg["connectivity"]["mobile_offline_on_trip"]
        return (self.offline > 0) | self.link_off | trip

    # ---- sensor model and device emulator ------------------------------------------------------------
    def emit(self, t: datetime, minute: int, supply: np.ndarray, door: np.ndarray, s: dict):
        offline = self.offline_now()
        self.offline_minutes += offline
        if self.sink is None:
            return
        noise = self.rng.normal(0, s["noise_sd"], len(self.units))
        measured = self.T + self.drift_per_min * minute + self.offset + noise
        measured = np.round(measured / s["resolution"]) * s["resolution"]
        stamp = t.isoformat()
        for u in np.flatnonzero(self.silent == 0):
            self.seq[u] += 1
            r = {"seq_no": int(self.seq[u]), "event_time": stamp, "temp": round(float(measured[u]), 1)}
            if self.signal[u]:
                r["power_ok"] = bool(supply[u])
            if self.door_sensor[u]:
                r["door_open"] = bool(door[u])
            if self.mobile[u]:
                lon, lat = self.position(u, t)
                r["lat"], r["lon"] = round(lat, 5), round(lon, 5)
            if offline[u]:
                self.buffers[u].append(r)
            elif self.buffers.get(u):      # back online: the buffer goes up with this reading
                self.buffers[u].append(r)
                self.flush(u)
            else:
                self.sink.send(self.units[u].serial, [r])

    def flush(self, u: int):
        if self.sink is not None and self.buffers[u]:
            self.sink.send(self.units[u].serial, self.buffers.pop(u))

    # ---- ground truth: true condition windows -------------------------------------------------------
    def track(self, t: datetime, masks: dict[str, np.ndarray]):
        """Open a window where a condition starts and close it where it ends; peaks are kept in arrays."""
        for kind, mask in masks.items():
            prev = self.prev.setdefault(kind, np.zeros(len(self.units), bool))
            peak = self.peaks.setdefault(kind, np.zeros(len(self.units)))
            for u in np.flatnonzero(prev & ~mask):
                self.end_window(self.windows.pop((kind, u)), t)
            for u in np.flatnonzero(mask & ~prev):
                causes = set(self.active[u])
                if self.mobile[u] and not self.on_trip[u]:     # a carrier in the store's cold room
                    causes |= self.active[self.home[u]]
                self.windows[(kind, u)] = Window(kind, u, t, None, causes)
                peak[u] = self.T[u]
            if kind == "above_upper":
                np.maximum(peak, np.where(mask, self.T, -np.inf), out=peak)
            elif kind == "at_or_below_freeze":
                np.minimum(peak, np.where(mask, self.T, np.inf), out=peak)
            self.prev[kind] = mask.copy()

    def end_window(self, w: Window, t: datetime, open_at_end: bool = False):
        unit = self.units[w.unit]
        if w.kind in ("above_upper", "at_or_below_freeze"):
            w.peak = float(self.peaks[w.kind][w.unit])
        relevant = {i for i in w.causes if CONDITION_FOR.get(i.rsplit("-", 1)[0]) == w.kind
                    or (w.kind == "above_upper" and i.startswith("power_loss"))}
        self.conditions.append({
            "kind": w.kind, "serial": unit.serial, "site_id": unit.site_id, "start": w.start.isoformat(),
            "end": t.isoformat(), "minutes": round((t - w.start).total_seconds() / 60), "open_at_end": open_at_end,
            "peak": None if w.peak is None or w.kind in ("no_power", "door_open") else round(w.peak, 2),
            "cause": ";".join(sorted(relevant)) or "natural"})

    def close_windows(self, t: datetime):
        for w in self.windows.values():
            self.end_window(w, t, open_at_end=True)
        self.windows.clear()

    # ---- run ---------------------------------------------------------------------------------------
    def run(self):
        for u in np.flatnonzero(self.has_grid):
            self.env.process(self.grid(int(u)))
        for u in np.flatnonzero(self.has_gen):
            self.env.process(self.fuel(int(u)))
        for u in range(len(self.units)):
            self.env.process(self.link(u))
        for kind, spec in self.cfg["failures"].items():
            self.env.process(self.injector(kind, spec))
        self.env.process(self.clock())
        self.env.run(until=self.cfg.minutes + 1)
        for u in list(self.buffers):
            self.flush(u)
        return self

    # ---- outputs -----------------------------------------------------------------------------------
    def expected_owner(self, u: int, t: datetime | None = None) -> str:
        """Who should own an event on unit u starting at t (Table 3.16): an outreach carrier on a trip is held
        by the outreach team; otherwise the unit's facility's officer-in-charge or the store's officer."""
        if self.mobile[u] and t is not None and any(tr.team == self.units[u].serial and tr.depart <= t < tr.returned
                                                     for tr in self.trips):
            return f"outreach_lead:{self.units[u].serial}"
        site = self.sites[u]
        if site.tier == "health_facility":
            return f"facility_oic:{site.id}"
        if site.tier == "lga_store":
            return f"lga_officer:{site.jurisdiction_id}"
        return f"state_officer:{site.jurisdiction_id}"

    def ground_truth(self) -> list[dict]:
        return [{"id": i.id, "type": i.type, "serial": self.units[i.unit].serial, "site_id": self.sites[i.unit].id,
                 "site": self.sites[i.unit].name, "lga": self.sites[i.unit].lga or "", "tier": self.sites[i.unit].tier,
                 "expected_owner": self.expected_owner(i.unit, i.start), "start": i.start.isoformat(), "end": i.end.isoformat(),
                 "truncated": i.truncated, "details": json.dumps(i.details)} for i in self.injections]

    def summary(self) -> dict:
        days = self.cfg.days
        fixed_minutes = self.cfg.minutes * int(self.fixed.sum())
        by_type = defaultdict(int)
        for i in self.injections:
            by_type[i.type] += 1
        expected = {k: v["rate"] / 1000 * len(self.eligible(k)) * days for k, v in self.cfg["failures"].items()}
        conds = defaultdict(lambda: {"natural": 0, "injected": 0})
        for c in self.conditions:
            conds[c["kind"]]["natural" if c["cause"] == "natural" else "injected"] += 1
        share = (lambda a: float(a[self.fixed].sum() / fixed_minutes) if fixed_minutes else None)
        offline = {c: float(np.mean([self.offline_minutes[u] / self.cfg.minutes for u in range(len(self.units))
                                     if self.location_class[u] == c])) for c in set(self.location_class)}
        return {"units": len(self.units), "fixed_units": int(self.fixed.sum()), "mobile_units": int(self.mobile.sum()),
                "unit_days": len(self.units) * days,
                "time_in_range": share(self.in_range), "time_above_upper": share(self.too_warm),
                "time_below_lower": share(self.too_cold), "trips": len(self.trips),
                "offline_share": {c: round(v, 4) for c, v in sorted(offline.items())},
                "injections": dict(by_type), "injections_expected": expected,
                "eligible_units": {k: len(self.eligible(k)) for k in self.cfg["failures"]},
                "conditions": dict(conds)}


class CsvSink:
    """Readings to a gzipped CSV (serial, seq_no, event_time, temp, power_ok, door_open, lat, lon, batch)."""

    def __init__(self, path: Path):
        self.f = gzip.open(path, "wt", newline="")  # noqa: SIM115 (closed by close())
        self.w = csv.writer(self.f)
        self.w.writerow(["serial", "seq_no", "event_time", "temp", "power_ok", "door_open", "lat", "lon", "batch"])

    def send(self, serial: str, readings: list[dict]):
        batch = len(readings) > 1
        for r in readings:
            self.w.writerow([serial, r["seq_no"], r["event_time"], r["temp"], r.get("power_ok", ""),
                             r.get("door_open", ""), r.get("lat", ""), r.get("lon", ""), batch])

    def close(self):
        self.f.close()


class CountingSink:
    def __init__(self):
        self.readings = 0
        self.batches = 0

    def send(self, serial: str, readings: list[dict]):
        self.readings += len(readings)
        self.batches += len(readings) > 1
