"""Movement model (Section 3.3.5): outreach trips on the Kano road network, with speeds and stops resampled
from GTS tracks.

An outreach team keeps its vaccine carrier at its LGA store. On an outreach day it leaves at a time drawn
from the GTS teams' start times, travels by road to an outreach site in its LGA (a GRID3 facility that does
not hold vaccines, such as a health post), holds a session there, and returns so as to arrive back about
the time GTS teams finished. Travel follows the shortest road route; along it, the team moves in moving
runs and stops resampled whole from GTS vehicle runs and stops, so speeds and stop durations follow the
GTS sample (calibration check, Table 3.13).

ASSUMPTIONS (params.DEFAULTS["movement"]): outreach days per week and which weekdays; session and
travel-time limits. GTS tracks are campaign (house-to-house) movement, which may differ from routine
outreach; only vehicle runs and the stops next to them are used for travel (limitation, Section 3.3.5).
"""
import csv
import gzip
import itertools
import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from functools import cached_property

import numpy as np
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import dijkstra
from scipy.spatial import cKDTree

from coldchain.sim.sources import FACILITIES, MOVEMENT, ROADS

WAT = 1


class Roads:
    """The derived Kano road graph: junctions, edges with their shapes, shortest routes."""

    def __init__(self):
        us, vs, lengths, shapes = [], [], [], []
        with gzip.open(ROADS, "rt", newline="") as f:
            for r in csv.DictReader(f):
                us.append(int(r["u"]))
                vs.append(int(r["v"]))
                lengths.append(max(float(r["length_m"]), 0.1))
                shapes.append(r["shape"])
        n = max(max(us), max(vs)) + 1
        self.xy = np.full((n, 2), np.nan)
        for u, v, s in zip(us, vs, shapes, strict=True):
            a, b = s.split(" ", 1)[0], s.rsplit(" ", 1)[-1]
            self.xy[u] = [float(c) for c in a.split(",")]
            self.xy[v] = [float(c) for c in b.split(",")]
        used = ~np.isnan(self.xy[:, 0])
        self.nodes = np.flatnonzero(used)
        self.tree = cKDTree(self._scaled(self.xy[used]))
        # keep the shortest edge between each pair of junctions
        best: dict[tuple[int, int], int] = {}
        for i, (u, v, w) in enumerate(zip(us, vs, lengths, strict=True)):
            key = (min(u, v), max(u, v))
            if key not in best or w < lengths[best[key]]:
                best[key] = i
        idx = list(best.values())
        rows = [us[i] for i in idx] + [vs[i] for i in idx]
        cols = [vs[i] for i in idx] + [us[i] for i in idx]
        self.graph = csr_matrix(([lengths[i] for i in idx] * 2, (rows, cols)), shape=(n, n))
        self.edge = {(us[i], vs[i]): shapes[i] for i in idx}
        self._pred: dict[int, np.ndarray] = {}

    @staticmethod
    def _scaled(lonlat: np.ndarray) -> np.ndarray:
        return np.column_stack([lonlat[:, 0] * math.cos(math.radians(12)), lonlat[:, 1]])

    def nearest(self, lon: float, lat: float) -> int:
        return int(self.nodes[self.tree.query(self._scaled(np.array([[lon, lat]])))[1][0]])

    def route(self, a: int, b: int) -> np.ndarray | None:
        """Shortest route from junction a to b, as an array of (lon, lat) points along the road shapes."""
        if a not in self._pred:
            self._pred[a] = dijkstra(self.graph, indices=a, return_predecessors=True)[1]
        pred = self._pred[a]
        if a != b and pred[b] < 0:
            return None
        path = [b]
        while path[-1] != a:
            path.append(int(pred[path[-1]]))
        path.reverse()
        pts = [self.xy[a]]
        for u, v in itertools.pairwise(path):
            shape = self.edge.get((u, v))
            coords = [tuple(map(float, p.split(","))) for p in (shape or self.edge[(v, u)]).split(" ")]
            if shape is None:
                coords.reverse()
            pts.extend(coords[1:])
        return np.array(pts)


def along(points: np.ndarray) -> np.ndarray:
    """Cumulative distance (m) along a polyline of (lon, lat) points."""
    lon, lat = np.radians(points[:, 0]), np.radians(points[:, 1])
    h = np.sin(np.diff(lat) / 2) ** 2 + np.cos(lat[:-1]) * np.cos(lat[1:]) * np.sin(np.diff(lon) / 2) ** 2
    return np.concatenate([[0.0], np.cumsum(2 * 6371000 * np.arcsin(np.sqrt(h)))])


class Gts:
    """Resampling library from the GTS-derived parameters (gts.py)."""

    def __init__(self):
        doc = json.loads(MOVEMENT.read_text())
        self.doc = doc
        self.runs = doc["vehicle"]["runs"]
        self.stops = np.array(doc["vehicle"]["stop_minutes"])
        self.start_q = np.array(doc["day_start_hour_wat"])
        self.end_q = np.array(doc["day_end_hour_wat"])

    @staticmethod
    def _quantile(q: np.ndarray, u: float) -> float:
        return float(np.interp(u, np.linspace(0, 1, len(q)), q))

    # The central 95% of GTS start and end times: the tails include phones left on overnight.
    def start_hour(self, rng) -> float:
        return self._quantile(self.start_q, rng.uniform(0.025, 0.975))

    def end_hour(self, rng) -> float:
        return self._quantile(self.end_q, rng.uniform(0.025, 0.975))


@dataclass
class Leg:
    """Travel along a route: segment end times (s from the leg's start) and distances reached (m)."""
    route: np.ndarray
    dist: np.ndarray                      # cumulative distance at each route point
    t: list[float] = field(default_factory=list)
    d: list[float] = field(default_factory=list)
    speeds: list[float] = field(default_factory=list)     # moving segment speeds used (for calibration)
    stops: list[float] = field(default_factory=list)      # en-route stop minutes used (for calibration)

    @property
    def seconds(self) -> float:
        return self.t[-1] if self.t else 0.0

    def position(self, s: float) -> tuple[float, float]:
        """(lon, lat) at s seconds after the leg's start."""
        d = float(np.interp(s, [0.0, *self.t], [0.0, *self.d]))
        return float(np.interp(d, self.dist, self.route[:, 0])), float(np.interp(d, self.dist, self.route[:, 1]))


def travel(route: np.ndarray, gts: Gts, rng) -> Leg:
    """Cover the route in GTS vehicle runs, resampled whole, with a GTS stop between consecutive runs."""
    leg = Leg(route, along(route))
    total, t, d = float(leg.dist[-1]), 0.0, 0.0
    while d < total:
        if leg.t:
            stop = float(gts.stops[rng.integers(len(gts.stops))])
            t += stop * 60
            leg.t.append(t)
            leg.d.append(d)
            leg.stops.append(stop)
        run = gts.runs[rng.integers(len(gts.runs))]
        length = sum(v * dt for v, dt in run)
        # The run that reaches the destination is used whole, with its segment durations shortened to fit the
        # remaining distance, so its speeds are kept. Cutting it short instead would under-use long, fast runs,
        # which are the likeliest to span the destination (inspection paradox), and bias speeds down.
        scale = min(1.0, (total - d) / length) if length > 0 else 1.0
        for v, dt in run:
            step = v * dt * scale
            t += dt * scale
            d = min(total, d + step)
            leg.t.append(t)
            leg.d.append(d)
            leg.speeds.append(v)
        if scale < 1.0:           # arrived (exactly, whatever the floating-point rounding)
            d = leg.d[-1] = total
    return leg


@dataclass
class Trip:
    team: str                  # carrier serial
    store_id: str
    site_id: str
    site_name: str
    depart: datetime
    out: Leg
    session_start: datetime
    session_end: datetime
    back: Leg
    returned: datetime

    @property
    def km(self) -> float:
        return float(self.out.dist[-1] + self.back.dist[-1]) / 1000

    def position(self, t: datetime) -> tuple[float, float] | None:
        """(lon, lat) at time t, or None if the team is at its store."""
        if t < self.depart or t >= self.returned:
            return None
        if t < self.session_start:
            return self.out.position((t - self.depart).total_seconds())
        if t < self.session_end:
            return tuple(self.out.route[-1])
        return self.back.position((t - self.session_end).total_seconds())

    def row(self) -> dict:
        return {"team": self.team, "store_id": self.store_id, "site_id": self.site_id, "site": self.site_name,
                "depart": self.depart.isoformat(), "session_start": self.session_start.isoformat(),
                "session_end": self.session_end.isoformat(), "returned": self.returned.isoformat(),
                "km": round(self.km, 1)}


class Movement:
    """Plans every outreach trip of a run, for the network's mobile units."""

    def __init__(self, network, cfg):
        self.net, self.cfg, self.mc = network, cfg, cfg["movement"]

    @cached_property
    def roads(self) -> Roads:
        return Roads()

    @cached_property
    def gts(self) -> Gts:
        return Gts()

    @cached_property
    def outreach_sites(self) -> dict[str, list[dict]]:
        """Per LGA, the GRID3 facilities served by outreach: those at the configured levels (health posts); in
        an LGA with none, any facility there that does not hold vaccines; failing that, any facility there."""
        nc = self.cfg["network"]
        by_lga: dict[str, list[dict]] = {}
        with FACILITIES.open() as f:
            for r in csv.DictReader(f):
                by_lga.setdefault(r["lga"], []).append(r)

        def holds(r):
            return r["level_option"] in nc["vaccine_levels"] and r["ownership"] in nc["ownership"]
        sites = {}
        for lga, rows in by_lga.items():
            sites[lga] = ([r for r in rows if r["level_option"] in self.mc["outreach_site_levels"] and not holds(r)]
                          or [r for r in rows if not holds(r)] or rows)
        return sites

    def plan(self, rng: np.random.Generator) -> list[Trip]:
        """Every trip that overlaps the run: one under way at the start begins the run away from the store,
        and one still away at the end has not uploaded its buffer by then."""
        start, days = self.cfg.start, self.cfg.days
        weekdays = set(self.mc["weekdays"])
        per_week = self.mc["days_per_week"]
        trips = []
        for unit in (u for u in self.net.units if u.mobile):
            store = self.net.site(unit.site_id)
            candidates = self.outreach_sites.get(store.lga, [])
            if not candidates:
                continue
            home = self.roads.nearest(store.lon, store.lat)
            week_days: dict[int, set[int]] = {}
            for d in range(-1, math.ceil(days)):      # from the day before: a trip may be under way at the start
                day = (start + timedelta(days=d)).date()
                week = day.isocalendar()[1]
                if week not in week_days:     # the team's outreach days this week
                    pool = sorted(weekdays)
                    week_days[week] = set(rng.choice(pool, min(per_week, len(pool)), replace=False).tolist())
                if day.isoweekday() not in week_days[week]:
                    continue
                trip = self._trip(unit, store, candidates, home, day, rng)
                if trip and trip.depart < start + timedelta(days=days) and trip.returned > start:
                    trips.append(trip)
        return trips

    def _trip(self, unit, store, candidates, home, day, rng) -> Trip | None:
        site = candidates[rng.integers(len(candidates))]
        dest = self.roads.nearest(float(site["lon"]), float(site["lat"]))
        route = self.roads.route(home, dest)
        if route is None or len(route) < 2:
            return None
        out = travel(route, self.gts, rng)
        back = travel(route[::-1].copy(), self.gts, rng)
        midnight = datetime(day.year, day.month, day.day, tzinfo=self.cfg.start.tzinfo)
        depart = midnight + timedelta(hours=self.gts.start_hour(rng) - WAT)
        session_start = depart + timedelta(seconds=out.seconds)
        # return so as to arrive about when GTS teams finish, after at least the minimum session
        arrive_target = midnight + timedelta(hours=self.gts.end_hour(rng) - WAT)
        session_end = max(session_start + timedelta(minutes=self.mc["min_session_min"]),
                          arrive_target - timedelta(seconds=back.seconds))
        returned = session_end + timedelta(seconds=back.seconds)
        if (returned - depart) > timedelta(hours=self.mc["max_trip_h"]):
            return None
        return Trip(unit.serial, store.id, site["grid3_id"], site["name"], depart, out, session_start,
                    session_end, back, returned)
