"""Simulation service (I7, Section 3.3.5): configuration and seed sets, network, equipment, power, climate,
movement and connectivity models, failure injection with ground truth, reproducibility, the load network, and
(integration) registration and the MQTT device emulator."""
import csv
import gzip
import json
from collections import Counter
from datetime import datetime, timedelta

import numpy as np
import pytest

from coldchain.sim import config
from coldchain.sim.calibrate import holdover_minutes, pull_down_minutes
from coldchain.sim.engine import CountingSink, CsvSink, Simulation
from coldchain.sim.network import _quota, build, lga_codes, load_network
from coldchain.sim.params import EQUIPMENT, GRID_BANDS
from coldchain.sim.sources import MOVEMENT

SMALL = {"lgas": ["Gwale", "Dala", "Ungogo"]}


def cfg(**kw):
    doc = {"name": "t", "purpose": "calibration", "seed": 21, "days": 2, "network": SMALL}
    doc.update(kw)
    return config.from_dict(doc)


@pytest.fixture(scope="module")
def net():
    c = cfg()
    return build(c["network"], c["network"]["seed"])


# Configuration -----------------------------------------------------------------------------------------

def test_seed_must_belong_to_the_purposes_set():
    with pytest.raises(ValueError, match="not a test seed"):
        config.from_dict({"name": "x", "purpose": "test", "seed": 21})
    with pytest.raises(ValueError, match="not a training seed"):
        config.from_dict({"name": "x", "purpose": "training", "seed": 101})
    assert config.from_dict({"name": "x", "purpose": "test", "seed": 105}).seed == 105


def test_unknown_settings_are_rejected_and_defaults_fill_the_rest():
    with pytest.raises(ValueError, match="unknown setting"):
        config.from_dict({"name": "x", "purpose": "demo", "seed": 900, "network": {"lgsa": []}})
    c = cfg(days=3)
    assert c["days"] == 3 and c["sensor"]["noise_sd"] == 0.2 and c.minutes == 3 * 1440


def test_effective_configuration_round_trips():
    import tomllib
    c = cfg(failures={"freeze": {"rate": 3.0}})
    again = config.from_dict(tomllib.loads(config.dump(c)))
    assert again.values == c.values and (again.name, again.purpose, again.seed) == (c.name, c.purpose, c.seed)


# Network -----------------------------------------------------------------------------------------------

def test_network_from_grid3_with_one_store_per_lga(net):
    counts = net.counts()
    assert counts["sites"]["state_store"] == 1 and counts["sites"]["lga_store"] == 3
    assert counts["sites"]["health_facility"] > 20
    assert all(s.source.startswith("GRID3") for s in net.sites if s.tier == "health_facility")
    assert {u.equipment for u in net.units if not u.mobile} == {"walk_in_cold_room", "solar_direct_drive",
                                                              "ice_lined_refrigerator"}
    assert len({u.serial for u in net.units}) == len(net.units)


def test_network_is_the_same_whatever_the_run_seed(net):
    other = build(cfg(seed=22)["network"], 0)
    assert [(s.id, s.power_source, s.grid_band) for s in other.sites] == \
           [(s.id, s.power_source, s.grid_band) for s in net.sites]


def test_quota_gives_exact_proportions():
    labels = _quota(np.random.default_rng(1), {"a": 0.5, "b": 0.3, "c": 0.2}, 10)
    assert Counter(labels) == {"a": 5, "b": 3, "c": 2}


def test_lga_codes_keep_reference_codes_and_are_unique():
    codes = lga_codes(["Gwale", "Dala", "Dawakin Kudu", "Dawakin Tofa", "Kano Municipal", "Nassarawa"])
    assert (codes["Gwale"], codes["Dala"], codes["Nassarawa"]) == ("GWL", "DAL", "NAS")
    assert len(set(codes.values())) == len(codes)


# Equipment model ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("key", list(EQUIPMENT))
def test_holdover_matches_the_pqs_value(key):
    eq = EQUIPMENT[key]
    assert abs(holdover_minutes(eq) / 60 / eq.holdover_h - 1) < 0.01
    if eq.powered:
        assert abs(pull_down_minutes(eq) / 60 / eq.pull_down_h - 1) < 0.01


# Power and climate -------------------------------------------------------------------------------------

def test_grid_supply_hours_fall_in_each_band(net):
    sims = [Simulation(cfg(seed=s, days=30), net).run() for s in (21, 22, 23)]
    hours = {b: [] for b in GRID_BANDS}
    for sim in sims:
        for u in np.flatnonzero(sim.has_grid):
            hours[sim.sites[u].grid_band].append(sim.mains_minutes[u] / 60 / 30)
    for band, (lo, hi) in GRID_BANDS.items():
        if len(hours[band]) >= 3:
            assert lo <= np.mean(hours[band]) <= hi, band


def test_daily_mean_ambient_equals_the_source(net):
    sim = Simulation(cfg(), net).run()
    means = sim.ambient_sum[0] / 1440
    assert np.allclose(means, sim.climate.mean[sim.climate.day(sim.cfg.start)])


# Failure injection and ground truth -----------------------------------------------------------------------

FAILING = {k: {"rate": 40.0} for k in ("heat_excursion", "freeze", "power_loss", "door_open", "device_silence",
                                         "connectivity_gap", "sensor_drift")}


def test_every_injection_is_labelled_with_its_expected_owner(net):
    sim = Simulation(cfg(days=7, failures=FAILING), net).run()
    gt = sim.ground_truth()
    assert {r["type"] for r in gt} == set(FAILING)
    for r in gt:
        assert r["start"] < r["end"] and r["serial"].startswith("SIM-")
        prefix = {"health_facility": "facility_oic:", "lga_store": "lga_officer:", "state_store": "state_officer:"}
        if r["expected_owner"].startswith("outreach_lead:"):          # a carrier away on an outreach trip
            assert r["expected_owner"] == f"outreach_lead:{r['serial']}" and "-M" in r["serial"]
        else:
            assert r["expected_owner"].startswith(prefix[r["tier"]])


def test_injected_heat_and_freeze_produce_conditions_attributed_to_them(net):
    sim = Simulation(cfg(days=7, failures={"heat_excursion": {"rate": 40.0}, "freeze": {"rate": 40.0}}), net).run()
    heat = {i.id for i in sim.injections if i.type == "heat_excursion"}
    caused = {c for row in sim.conditions if row["kind"] == "above_upper" for c in row["cause"].split(";")}
    assert heat and heat <= caused
    freezes = [r for r in sim.conditions if r["kind"] == "at_or_below_freeze"]
    assert freezes and all(r["cause"].startswith("freeze-") for r in freezes)
    assert all(r["peak"] <= -0.5 for r in freezes)


def test_silence_sends_nothing_and_a_connectivity_gap_uploads_a_batch(net):
    sink = CountingSink()
    sim = Simulation(cfg(days=3, failures={"device_silence": {"rate": 100.0}, "connectivity_gap": {"rate": 100.0}}),
                     net, sink).run()
    silent = sum(min(i.end, sim.t(sim.cfg.minutes)).timestamp() - i.start.timestamp()
                 for i in sim.injections if i.type == "device_silence") / 60
    full = len(sim.units) * sim.cfg.minutes
    assert sink.readings == pytest.approx(full - silent, abs=len(sim.injections) * 2)
    assert sink.batches >= sum(i.type == "connectivity_gap" for i in sim.injections) - 1


def test_runs_are_reproducible_from_their_configuration(net, tmp_path):
    c = cfg(days=2, failures={"freeze": {"rate": 50.0}, "door_open": {"rate": 50.0}})
    a, b = (Simulation(c, net, CsvSink(tmp_path / f"{n}.csv.gz")) for n in "ab")
    for s in (a, b):
        s.run()
        s.sink.close()
    assert a.ground_truth() == b.ground_truth() and a.conditions == b.conditions
    assert gzip.decompress((tmp_path / "a.csv.gz").read_bytes()) == gzip.decompress((tmp_path / "b.csv.gz").read_bytes())
    other = Simulation(cfg(seed=22, days=2, failures=c["failures"]), net).run()
    assert other.ground_truth() != a.ground_truth()


def test_readings_carry_power_and_door_only_where_the_logger_senses_them(net, tmp_path):
    sim = Simulation(cfg(days=1), net, CsvSink(tmp_path / "r.csv.gz"))
    sim.run()
    sim.sink.close()
    rows = list(csv.DictReader(gzip.decompress((tmp_path / "r.csv.gz").read_bytes()).decode().splitlines()))
    units = {u.serial: u for u in net.units}
    solar = [r for r in rows if units[r["serial"]].equipment == "solar_direct_drive"]
    mains = [r for r in rows if units[r["serial"]].has_power_signal]
    assert solar and all(r["power_ok"] == "" for r in solar)
    assert mains and {r["power_ok"] for r in mains} <= {"True", "False"}
    assert all(abs(float(r["temp"]) * 10 - round(float(r["temp"]) * 10)) < 1e-9 for r in rows[:2000])
    json.dumps(sim.summary())


# Movement, connectivity and mobile units (I7b) -----------------------------------------------------------

@pytest.fixture(scope="module")
def week(net):
    return Simulation(cfg(days=7), net).run()


def test_outreach_trips_leave_and_return_to_the_store_within_the_run(week):
    sim = week
    assert sim.trips and {t.team for t in sim.trips} == {u.serial for u in sim.units if u.mobile}
    end = sim.t(sim.cfg.minutes)
    inside = [t for t in sim.trips if sim.cfg.start <= t.depart and t.returned <= end]
    assert len(inside) >= len(sim.trips) - 2 * len(sim.mobile.nonzero()[0])    # only trips at the run's edges cross it
    for t in inside:
        assert t.depart < t.session_start <= t.session_end < t.returned
        store = sim.net.site(t.store_id)
        for when in (t.depart, t.returned - timedelta(seconds=1)):
            lon, lat = t.position(when)
            assert abs(lon - store.lon) < 0.02 and abs(lat - store.lat) < 0.02    # nearest road junction
        assert t.position(t.returned) is None and t.position(t.depart - timedelta(seconds=1)) is None
        assert t.position(t.session_start + (t.session_end - t.session_start) / 2) == tuple(t.out.route[-1])


def test_trip_speeds_and_stops_are_resampled_from_gts_vehicle_runs(week):
    library = json.loads(MOVEMENT.read_text())["vehicle"]
    speeds = {v for run in library["runs"] for v, _ in run}
    stops = set(library["stop_minutes"])
    for t in week.trips[:20]:
        for leg in (t.out, t.back):
            assert set(leg.speeds) <= speeds and set(leg.stops) <= stops
            assert leg.d[-1] == pytest.approx(leg.dist[-1])                    # the whole route is covered


def test_mobile_readings_carry_positions_and_upload_after_the_trip(net, tmp_path):
    sim = Simulation(cfg(days=3), net, CsvSink(tmp_path / "r.csv.gz"))
    sim.run()
    sim.sink.close()
    rows = list(csv.DictReader(gzip.decompress((tmp_path / "r.csv.gz").read_bytes()).decode().splitlines()))
    trip = max((t for t in sim.trips if t.depart >= sim.cfg.start and t.returned <= sim.t(sim.cfg.minutes)),
               key=lambda t: t.km)
    mine = [r for r in rows if r["serial"] == trip.team]
    on_trip = [r for r in mine if trip.depart <= datetime.fromisoformat(r["event_time"]) < trip.returned]
    assert on_trip and all(r["batch"] == "True" for r in on_trip)             # buffered while away
    assert len({(r["lat"], r["lon"]) for r in on_trip}) > 10                    # moving
    assert all(r["lat"] and r["lon"] for r in mine)
    gaps = [g for g in sim.gaps if g["cause"] == "trip" and g["serial"] == trip.team]
    gap_start = datetime.fromisoformat(gaps[0]["start"])
    assert timedelta(0) <= gap_start - trip.depart < timedelta(minutes=1)      # from the first whole minute away


def test_carrier_at_the_store_follows_the_cold_room_and_warms_on_a_trip(week):
    sim = week
    at_store = sim.mobile & ~sim.on_trip
    assert np.allclose(sim.T[at_store], sim.T[sim.home[at_store]])


def test_connectivity_offline_share_follows_the_location_class(net):
    c = cfg(days=7, connectivity={"urban": {"offline_share": 0.2, "mean_gap_min": 60.0},
                                  "rural": {"offline_share": 0.0}, "mobile_offline_on_trip": False})
    sim = Simulation(c, net, CountingSink()).run()
    share = sim.summary()["offline_share"]
    assert share["urban"] == pytest.approx(0.2, abs=0.05)          # Gwale, Dala and Ungogo are urban LGAs
    assert sim.sink.batches > 0 and all(g["cause"] == "network" for g in sim.gaps)


def test_time_in_range_counts_fixed_units_only(week):
    s = week.summary()
    assert s["fixed_units"] + s["mobile_units"] == s["units"]
    assert s["time_in_range"] + s["time_above_upper"] + s["time_below_lower"] == pytest.approx(1.0)


def test_load_network_matches_the_load_model():
    scenario = build(cfg()["network"], 0)
    load = load_network(scenario, 500, 60)
    counts = load.counts()
    assert (counts["fixed_units"], counts["mobile_units"]) == (500, 60)
    assert len({u.serial for u in load.units}) == len(load.units)
    assert len({s.id for s in load.sites}) == len(load.sites)
    ids = {s.id for s in load.sites}
    assert all(u.site_id in ids for u in load.units) and all(s.parent_id in ids for s in load.sites if s.parent_id)
    assert {s.jurisdiction_id.split("-")[1] for s in load.sites} >= {"L01", "L02"}


@pytest.mark.integration
def test_registration_makes_devices_known_to_ingestion(net, owner_conn):
    from coldchain.sim.register import register, retire
    counts = register(owner_conn, net)
    assert counts["devices"] == len(net.units)
    n = owner_conn.execute("SELECT count(*) FROM device WHERE serial_no = ANY(%s) AND status = 'active'",
                           ([u.serial for u in net.units],)).fetchone()[0]
    assert n == len(net.units)
    mobile = owner_conn.execute("SELECT count(*) FROM device WHERE serial_no LIKE 'SIM-%%-M%%' AND type = 'mobile'"
                                " AND serial_no = ANY(%s)", ([u.serial for u in net.units],)).fetchone()[0]
    assert mobile == sum(u.mobile for u in net.units)
    assert retire(owner_conn, net) == len(net.units)


@pytest.mark.integration
def test_emulator_publishes_every_reading_with_acknowledgement(net):
    from coldchain.config import get_settings
    from coldchain.sim.emulator import MqttSink
    s = get_settings()
    serials = [u.serial for u in net.units][:30]
    sink = MqttSink(s.mqtt_host, s.mqtt_port, serials, 3, client_prefix="pytest-sim", topic_prefix="simtest")
    for i, serial in enumerate(serials):
        sink.send(serial, [{"seq_no": 1, "event_time": "2026-01-01T00:00:00+00:00", "temp": 4.0}])
        sink.send(serial, [{"seq_no": k, "event_time": "2026-01-01T00:00:00+00:00", "temp": 4.0}
                           for k in range(2, 2 + 600 + i)])                         # split into batches of 500
    sink.close(timeout=30)
    assert sink.stats["readings"] == sum(601 + i for i in range(30))
    assert sink.stats["acked"] == sink.stats["messages"] == 30 * 3 and sink.stats["unacked"] == 0
