"""Scripted test cases for the detection rules (Table 3.17, Section 3.4.4). No services needed."""
import random
from datetime import UTC, datetime, timedelta

import pytest

from coldchain.detection.rules import Limits, Reading, cumulative_exposure, detect

T0 = datetime(2026, 11, 1, 8, 0, tzinfo=UTC)
MIN = timedelta(minutes=1)
LIM = Limits(upper=8.0, freeze=0.0, freeze_sensitive=True, heat_tolerance=timedelta(minutes=30),
             freeze_tolerance=timedelta(minutes=5))


def series(temps, start=T0, step=MIN, limits=LIM, **flags):
    """One reading per step. flags: name -> list of values aligned with temps (e.g. power_ok=[...])."""
    return [Reading(start + k * step, t, limits, **{f: v[k] for f, v in flags.items()}) for k, t in enumerate(temps)]


def run(readings, power=False, door=False):
    return detect(readings, MIN, has_power_signal=power, has_door_sensor=door)


def of_type(events, kind):
    return [e for e in events if e.type == kind]


# Heat excursion: above 8 °C for at least 30 minutes ---------------------------

def test_heat_excursion_of_30_minutes_detected():
    ev = of_type(run(series([5] * 10 + [9.5] * 31 + [5] * 15)), "heat_excursion")
    assert len(ev) == 1
    e = ev[0]
    assert e.severity == "critical" and e.start == T0 + 10 * MIN and e.end == T0 + 41 * MIN and e.peak == 9.5


def test_heat_of_29_minutes_not_detected():
    assert not of_type(run(series([5] * 10 + [9.5] * 29 + [5] * 15)), "heat_excursion")


def test_ongoing_excursion_reported_without_end():
    e = of_type(run(series([5] * 5 + [10] * 40)), "heat_excursion")[0]
    assert e.end is None


def test_brief_dip_does_not_split_excursion():  # hysteresis, FR3.3
    temps = [5] * 5 + [9.5] * 20 + [7.5] * 4 + [9.5] * 20 + [5] * 15
    ev = of_type(run(series(temps)), "heat_excursion")
    assert len(ev) == 1 and ev[0].start == T0 + 5 * MIN and ev[0].end == T0 + 49 * MIN


def test_return_to_range_shorter_than_hysteresis_keeps_event_open():
    ev = of_type(run(series([5] * 5 + [9.5] * 35 + [5] * 6)), "heat_excursion")
    assert len(ev) == 1 and ev[0].end is None   # back in range only 6 min: not yet ended


def test_readings_around_threshold_create_one_event_not_many():
    temps = [5] * 5 + [8.2, 7.9] * 30 + [5] * 15   # flapping across 8 °C for an hour
    assert len(of_type(run(series(temps)), "heat_excursion")) == 1


def test_borderline_flag_within_sensor_accuracy():
    e = of_type(run(series([5] * 5 + [8.4] * 35 + [5] * 12)), "heat_excursion")[0]
    assert e.borderline
    e = of_type(run(series([5] * 5 + [9.0] * 35 + [5] * 12)), "heat_excursion")[0]
    assert not e.borderline


# Freeze: at or below 0 °C for at least 5 minutes, freeze-sensitive products -----

def test_freeze_detected():
    e = of_type(run(series([4] * 5 + [-1.4] * 6 + [4] * 12)), "freeze")[0]
    assert e.severity == "critical" and e.peak == -1.4 and e.start == T0 + 5 * MIN


def test_freeze_of_4_minutes_not_detected():
    assert not of_type(run(series([4] * 5 + [-1.4] * 4 + [4] * 12)), "freeze")


def test_freeze_ignored_for_product_that_is_not_freeze_sensitive():
    lim = Limits(8.0, 0.0, False, timedelta(minutes=30), timedelta(minutes=5))
    assert not of_type(run(series([4] * 5 + [-2] * 20 + [4] * 12, limits=lim)), "freeze")


def test_product_specific_limits_used():
    strict = Limits(6.0, 0.0, True, timedelta(minutes=10), timedelta(minutes=5))
    assert of_type(run(series([5] * 5 + [7] * 11 + [5] * 12, limits=strict)), "heat_excursion")
    assert not of_type(run(series([5] * 5 + [7] * 11 + [5] * 12)), "heat_excursion")


# Insufficient data (FR3.2) -----------------------------------------------------

def test_no_detection_across_a_data_gap():
    # 20 min hot, 2 h with no readings, 20 min hot: neither part reaches 30 min, and the gap is not assumed hot
    before = series([5] * 5 + [9.5] * 20)
    after = series([9.5] * 20 + [5] * 15, start=before[-1].t + timedelta(hours=2))
    assert not of_type(run(before + after), "heat_excursion")


def test_episode_ends_at_last_observation_before_gap():
    before = series([5] * 5 + [9.5] * 35)
    after = series([5] * 15, start=before[-1].t + timedelta(hours=2))
    e = of_type(run(before + after), "heat_excursion")[0]
    assert e.end == before[-1].t and e.details == {"ended_by": "data_gap"}


def test_short_irregularity_within_max_gap_is_not_a_gap():
    temps = series([5] * 5 + [9.5] * 40 + [5] * 15)
    del temps[20:22]                                    # two missing readings (3 min apart < 3 intervals)
    assert len(of_type(run(temps), "heat_excursion")) == 1


def test_no_events_from_empty_or_single_reading():
    assert run([]) == []
    assert run(series([12])) == []


def test_readings_must_be_in_event_time_order_regardless_of_arrival():
    temps = series([5] * 5 + [9.5] * 35 + [5] * 15)
    arrived = random.Random(1).sample(temps, len(temps))      # arrival order
    assert run(sorted(arrived, key=lambda r: r.t)) == run(temps)   # the service sorts by event time


# Power loss and door: need the matching input (Table 3.17) ---------------------

def test_power_loss_warning():
    power = [True] * 5 + [False] * 15 + [True] * 12
    e = of_type(run(series([5] * 32, power_ok=power), power=True), "power_loss")[0]
    assert e.severity == "warning" and e.start == T0 + 5 * MIN and e.end == T0 + 20 * MIN


def test_power_loss_critical_when_temperature_rising_towards_limit():
    temps = [5] * 5 + [5 + 0.12 * k for k in range(15)] + [6] * 12   # rises 1.7 °C to 6.7 °C
    power = [True] * 5 + [False] * 15 + [True] * 12
    assert of_type(run(series(temps, power_ok=power), power=True), "power_loss")[0].severity == "critical"


def test_power_loss_not_reported_without_power_signal():
    power = [True] * 5 + [False] * 15 + [True] * 12
    assert not of_type(run(series([5] * 32, power_ok=power), power=False), "power_loss")


def test_missing_power_values_not_treated_as_power_loss():
    assert not of_type(run(series([5] * 30, power_ok=[None] * 30), power=True), "power_loss")


def test_door_open_warning():
    door = [False] * 5 + [True] * 6 + [False] * 12
    e = of_type(run(series([5] * 23, door_open=door), door=True), "door_open")[0]
    assert e.severity == "warning"


def test_door_open_needs_door_sensor():
    door = [False] * 5 + [True] * 6 + [False] * 12
    assert not of_type(run(series([5] * 23, door_open=door), door=False), "door_open")


def test_door_open_for_4_minutes_ignored():
    door = [False] * 5 + [True] * 4 + [False] * 12
    assert not of_type(run(series([5] * 21, door_open=door), door=True), "door_open")


# Local alarm ------------------------------------------------------------------

def test_local_alarm_recorded():
    alarm = [None] * 5 + [True] * 2 + [False] * 12
    e = of_type(run(series([9] * 19, alarm=alarm)), "local_alarm")[0]
    assert e.severity == "critical" and e.start == T0 + 5 * MIN


# Cumulative exposure (FR3.4) -----------------------------------------------------

def test_cumulative_exposure_counts_short_excursions_and_excludes_gaps():
    a = series([5] * 5 + [9] * 10 + [5] * 5 + [9] * 10 + [5] * 5)    # two 10-minute excursions
    b = series([-1] * 3 + [5] * 2, start=a[-1].t + timedelta(hours=1))
    exp = cumulative_exposure(a + b, MIN)
    assert exp["above_range"] == timedelta(minutes=20)
    assert exp["at_or_below_freeze"] == timedelta(minutes=3)
    assert exp["unobserved"] == timedelta(minutes=59)


@pytest.mark.parametrize("kind", ["heat_excursion", "freeze"])
def test_every_event_reported_once(kind):
    temps = [5] * 5 + ([12] if kind == "heat_excursion" else [-3]) * 40 + [5] * 15
    assert len(of_type(run(series(temps)), kind)) == 1
