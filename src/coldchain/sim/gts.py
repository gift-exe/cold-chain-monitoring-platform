"""Movement parameters from GTS tracks (Section 3.3.5, "Use of GTS data").

Input: a GTS export folder (Tracks/*.csv), read in place and never copied into the repository. The tracks
are personal data; only movement attributes are used, and what is written contains no identifiers, no
positions and no dates:

- moving runs, by mode (vehicle or foot): sequences of segment speeds (m/s) and durations (s) between
  consecutive fixes while moving, which the movement model resamples whole, so that speed changes within a
  run stay realistic;
- stop durations, by mode: time spent stationary between two moving runs (minutes); a stop next to a
  vehicle run counts as a vehicle stop;
- working day: start and end hour (WAT) and distance travelled per team-day, as quantiles;
- a separate check sample of segment speeds and stop durations from the other half of the team-days, which
  the calibration compares with simulated movement (two-sample Kolmogorov–Smirnov test, Table 3.13).

Processing: Kano teams (team code beginning KN and fixes inside the Kano facility area) on the campaign
days (dates with at least 1,000 Kano devices reporting); fixes with accuracy worse than 20 m and
duplicate fixes are dropped; the first and last 30 minutes of each team-day are removed (generalisation,
so tracks cannot be linked to homes); segments shorter than 30 s or longer than 10 minutes are not used.

Needs DuckDB, which is not a runtime dependency:  uv run --with duckdb python -m coldchain.sim sources --gts DIR
"""
import csv
import hashlib
import json
from pathlib import Path

import numpy as np

from coldchain.sim.sources import FACILITIES, MOVEMENT

MAX_ACCURACY_M = 20
TRIM_MIN = 30
MIN_DT_S, MAX_DT_S = 30, 600
MOVING_MPS = 0.3            # ASSUMPTION: slower than this between fixes counts as stationary (GPS noise)
MAX_MPS = 35.0              # faster than this between fixes is a GPS jump, not movement
VEHICLE_MPS = 2.5           # ASSUMPTION: a run whose median speed is above brisk walking is by vehicle
MODES = ("vehicle", "foot")
CAMPAIGN_MIN_DEVICES = 1000
LIBRARY = 20000             # moving runs and stops kept for resampling
CHECK = 5000                # held-out values kept for the calibration check


def _half(imei: str, day) -> int:
    """Team-days are split at random into two halves: one builds the resampling library, the other is held
    out for the calibration check. Splitting by team-day rather than by device keeps the halves comparable
    (teams differ from one another), at the cost of some dependence between halves from the same team."""
    return hashlib.sha256(f"{imei}|{day}".encode()).digest()[0] & 1


def reduce(export: Path) -> str:
    import duckdb
    with FACILITIES.open() as f:
        rows = list(csv.DictReader(f))
    lat = [float(r["lat"]) for r in rows]
    lon = [float(r["lon"]) for r in rows]
    box = (min(lat) - 0.05, max(lat) + 0.05, min(lon) - 0.05, max(lon) + 0.05)
    src = str(export / "Tracks" / "*.csv")
    con = duckdb.connect()
    # Team codes may contain unquoted commas, so each line is split from both ends.
    con.execute(f"""
        CREATE TEMP TABLE fix AS
        WITH raw AS (SELECT string_split(line, ',') f FROM read_csv('{src}', columns={{'line': 'VARCHAR'}},
                     delim='\x01', quote='', escape='', header=true)),
        p AS (SELECT array_to_string(f[5:len(f)-10], ',') team, f[len(f)-9] imei,
                     try_strptime(f[len(f)-6], '%m/%d/%Y %H:%M:%S') + INTERVAL 1 HOUR wat,
                     try_cast(f[len(f)-4] AS DOUBLE) lat, try_cast(f[len(f)-3] AS DOUBLE) lon,
                     try_cast(f[len(f)-2] AS DOUBLE) acc FROM raw)
        SELECT DISTINCT imei, wat, lat, lon FROM p
        WHERE upper(team) LIKE 'KN%' AND lat BETWEEN {box[0]} AND {box[1]} AND lon BETWEEN {box[2]} AND {box[3]}
          AND acc <= {MAX_ACCURACY_M} AND wat IS NOT NULL""")
    days = [d for (d,) in con.execute(f"""SELECT wat::date FROM fix GROUP BY 1
                                          HAVING count(DISTINCT imei) >= {CAMPAIGN_MIN_DEVICES} ORDER BY 1""").fetchall()]
    con.execute(f"""
        CREATE TEMP TABLE seg AS
        WITH f AS (SELECT * FROM fix WHERE wat::date IN (SELECT unnest(?::date[]))
                   QUALIFY row_number() OVER (PARTITION BY imei, wat ORDER BY lat, lon) = 1),
        b AS (SELECT *, min(wat) OVER d t0, max(wat) OVER d t1 FROM f WINDOW d AS (PARTITION BY imei, wat::date)),
        t AS (SELECT * FROM b WHERE wat >= t0 + INTERVAL {TRIM_MIN} MINUTE AND wat <= t1 - INTERVAL {TRIM_MIN} MINUTE),
        s AS (SELECT imei, wat::date dday, wat, t0, t1,
                     epoch(wat - lag(wat) OVER w) dt,
                     2 * 6371000 * asin(sqrt(pow(sin(radians(lat - lag(lat) OVER w) / 2), 2) + cos(radians(lat))
                       * cos(radians(lag(lat) OVER w)) * pow(sin(radians(lon - lag(lon) OVER w) / 2), 2))) dm
              FROM t WINDOW w AS (PARTITION BY imei, wat::date ORDER BY wat))
        SELECT * FROM s WHERE dt IS NOT NULL""", [days])
    segs = con.execute(f"""
        SELECT imei, dday, epoch(wat) e, dt, dm / dt v FROM seg
        WHERE dt BETWEEN {MIN_DT_S} AND {MAX_DT_S} AND dm / dt <= {MAX_MPS} ORDER BY imei, dday, wat""").fetchall()
    team_days = con.execute(f"""
        SELECT imei, dday, hour(min(t0)) + minute(min(t0)) / 60, hour(max(t1)) + minute(max(t1)) / 60,
               sum(CASE WHEN dt <= {MAX_DT_S} AND dm / dt <= {MAX_MPS} THEN dm ELSE 0 END) / 1000
        FROM seg GROUP BY imei, dday HAVING count(*) >= 30""").fetchall()

    # Each team-day becomes a sequence of moving runs and stops; a gap longer than MAX_DT_S ends a run.
    runs = {(h, m): [] for h in (0, 1) for m in MODES}
    stops = {(h, m): [] for h in (0, 1) for m in MODES}
    for seq, h in _sequences(segs):
        for i, (kind, value) in enumerate(seq):
            if kind == "run":
                runs[(h, mode(value))].append(value)
            elif 0 < i < len(seq) - 1:                  # a stop between two moving runs
                before, after = mode(seq[i - 1][1]), mode(seq[i + 1][1])
                stops[(h, "vehicle" if "vehicle" in (before, after) else "foot")].append(round(value, 1))

    rng = np.random.default_rng(0)

    def sample(xs, n):
        return [xs[i] for i in sorted(rng.choice(len(xs), min(n, len(xs)), replace=False))]

    q = np.linspace(0, 1, 101)
    td = np.array([(a, b, c) for imei, day, a, b, c in team_days if _half(imei, day) == 0])
    doc = {
        "source": "GTS export, Kano teams, polio campaign days " + ", ".join(str(d) for d in days),
        "processing": {"max_accuracy_m": MAX_ACCURACY_M, "trim_min": TRIM_MIN, "segment_s": [MIN_DT_S, MAX_DT_S],
                       "moving_mps": MOVING_MPS, "max_mps": MAX_MPS, "vehicle_mps": VEHICLE_MPS},
        "counts": {"segments": len(segs), "team_days": len(team_days),
                   **{f"{m}_runs": len(runs[(0, m)]) + len(runs[(1, m)]) for m in MODES},
                   **{f"{m}_stops": len(stops[(0, m)]) + len(stops[(1, m)]) for m in MODES}},
        "day_start_hour_wat": np.round(np.quantile(td[:, 0], q), 2).tolist(),
        "day_end_hour_wat": np.round(np.quantile(td[:, 1], q), 2).tolist(),
        "day_km": np.round(np.quantile(td[:, 2], q), 2).tolist(),
    }
    for m in MODES:
        doc[m] = {"runs": sample(runs[(0, m)], LIBRARY), "stop_minutes": sample(stops[(0, m)], LIBRARY),
                  "check": {"speed_mps": sample([v for r in runs[(1, m)] for v, _ in r], CHECK),
                            "stop_minutes": sample(stops[(1, m)], CHECK)}}
    MOVEMENT.write_text(json.dumps(doc, separators=(",", ":")))
    c = doc["counts"]
    return (f"{c['team_days']} team-days; {c['vehicle_runs']} vehicle and {c['foot_runs']} foot runs; "
            f"{c['vehicle_stops']} and {c['foot_stops']} stops")


def mode(run: list[tuple[float, int]]) -> str:
    """A moving run is by vehicle if its median segment speed is above walking pace."""
    return "vehicle" if float(np.median([v for v, _ in run])) >= VEHICLE_MPS else "foot"


def _sequences(segs):
    """Per team-day: [("run", [(speed, dt), ...]) | ("stop", minutes), ...] and the device's half."""
    key, last_e, seq, h = None, None, [], 0
    for imei, dday, e, dt, v in segs:
        if (imei, dday) != key or e - dt > last_e + 1:
            if seq:
                yield seq, h
            key, seq, h = (imei, dday), [], _half(imei, dday)
        if v >= MOVING_MPS:
            if not seq or seq[-1][0] != "run":
                seq.append(("run", []))
            seq[-1][1].append((round(v, 2), round(dt)))
        else:
            if not seq or seq[-1][0] != "stop":
                seq.append(("stop", 0.0))
            seq[-1] = ("stop", seq[-1][1] + dt / 60)
        last_e = e
    if seq:
        yield seq, h
