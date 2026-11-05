"""Simulation parameters and their sources (Table 3.13).

Every value is either taken from a named source or marked ASSUMPTION. Assumptions are defaults that a run
configuration may override, so that the evaluation can vary them (sensitivity runs).
"""
import math
from dataclasses import dataclass

# Equipment (WHO PQS catalogue). Hold-over (refrigerators) and cold life (passive containers) are the times
# for the vaccine compartment to reach +10 °C after power is cut (or after packing), at +43 °C ambient.
PQS_TEST_AMBIENT = 43.0
PQS_LIMIT = 10.0


@dataclass(frozen=True)
class Equipment:
    key: str
    description: str
    source: str                 # PQS code and model, or ASSUMPTION
    holdover_h: float           # hours to +10 °C at +43 °C without power
    powered: bool               # False for passive containers (cold boxes, carriers)
    start_temp: float           # T0: compartment temperature when power is cut or the container is packed
    cut_in: float = 0.0         # thermostat: compressor on above this temperature
    cut_out: float = 0.0        # ...and off below this one
    cooling_target: float = 0.0  # temperature the cooling drives towards (evaporator side)
    pull_down_h: float = 0.0    # hours to cool from +43 °C to cut-out with power on

    @property
    def k(self) -> float:
        """Heat-gain rate per minute: k = ln[(Ta − T0) / (Ta − 10)] / h, Section 3.3.5."""
        return math.log((PQS_TEST_AMBIENT - self.start_temp) / (PQS_TEST_AMBIENT - PQS_LIMIT)) / (self.holdover_h * 60)

    @property
    def c(self) -> float:
        """Cooling rate per minute, chosen so the unit pulls down from +43 °C to cut-out in pull_down_h."""
        if not self.powered:
            return 0.0
        # With the compressor on, T relaxes towards T* = (k·Ta + c·Tc)/(k + c) at rate (k + c). Solve for c by
        # bisection so that T reaches cut_out from 43 °C in pull_down_h hours.
        k, ta, tc, t1, minutes = self.k, PQS_TEST_AMBIENT, self.cooling_target, self.cut_out, self.pull_down_h * 60

        def time_to_cut_out(c: float) -> float:
            star = (k * ta + c * tc) / (k + c)
            if star >= t1:
                return math.inf
            return math.log((ta - star) / (t1 - star)) / (k + c)

        lo, hi = 1e-6, 1.0
        for _ in range(200):
            mid = (lo + hi) / 2
            lo, hi = (mid, hi) if time_to_cut_out(mid) > minutes else (lo, mid)
        return hi


EQUIPMENT = {e.key: e for e in [
    Equipment("solar_direct_drive", "Solar direct-drive ice-lined refrigerator",
              "WHO PQS E003-164, B Medical Systems TCW 50R SDD (hold-over 94:15 at +43 °C)",
              holdover_h=94 + 15 / 60, powered=True, start_temp=5.0, cut_in=6.0, cut_out=3.0,
              cooling_target=-8.0, pull_down_h=8.0),
    Equipment("ice_lined_refrigerator", "Mains ice-lined refrigerator",
              "WHO PQS E003-151, B Medical Systems TCW 240R AC (hold-over 115:19 at +43 °C)",
              holdover_h=115 + 19 / 60, powered=True, start_temp=5.0, cut_in=6.0, cut_out=3.0,
              cooling_target=-8.0, pull_down_h=8.0),
    Equipment("walk_in_cold_room", "Walk-in cold room (state and LGA stores)",
              "ASSUMPTION: PQS E001 cold rooms state no hold-over; 24 h assumed",
              holdover_h=24.0, powered=True, start_temp=5.0, cut_in=6.0, cut_out=3.5,
              cooling_target=-5.0, pull_down_h=6.0),
    Equipment("cold_box", "Long-range cold box",
              "WHO PQS E004-084, Mark Enterprises MCBX-01 (cold life 129:56 at +43 °C)",
              holdover_h=129 + 56 / 60, powered=False, start_temp=3.0),
    Equipment("vaccine_carrier", "Long-range vaccine carrier",
              "WHO PQS E004-078, Trimurti Plast TPVC 46 (cold life 47:02 at +43 °C)",
              holdover_h=47 + 2 / 60, powered=False, start_temp=3.0),
]}
# Pull-down times and thermostat settings are ASSUMPTIONS: the PQS records used here do not state them.

# Grid supply: NERC service-based tariff bands, minimum average hours of supply per day (NERC, n.d.). A band
# covers its minimum up to the next band's minimum.
GRID_BANDS = {"A": (20.0, 24.0), "B": (16.0, 20.0), "C": (12.0, 16.0), "D": (8.0, 12.0), "E": (4.0, 8.0)}

DEFAULTS = {
    "start": "2024-03-01T00:00:00+00:00",
    "days": 30,
    "network": {
        "seed": 0,                                      # the network's own seed: runs share one network
        "lgas": [],                                     # empty: every LGA in the state
        # ASSUMPTION: which facilities hold vaccines. Public primary health centres and clinics and public
        # general hospitals; health posts are served by outreach and hold none.
        "vaccine_levels": ["Primary Health Center", "Primary Health Clinic", "General Hospital"],
        "ownership": ["Public"],
        "state_store": {"name": "Kano State Cold Store", "lat": 12.0000, "lon": 8.5167},
        "outreach_teams_per_lga": 2,                    # ASSUMPTION
        "interval_s": 60,                               # Table 3.7: one reading per minute when connected
        # Power-source mix of health facilities. Grid share 0.43 from Ilegogie (2025); the split of the rest
        # and of grid facilities with a generator is an ASSUMPTION pending the source figures.
        "power_mix": {"solar": 0.40, "grid": 0.25, "grid_generator": 0.18, "generator": 0.17},
        # ASSUMPTION: tariff band of grid-connected facilities (northern, largely rural).
        "grid_bands": {"A": 0.05, "B": 0.10, "C": 0.20, "D": 0.30, "E": 0.35},
        # ASSUMPTION: share of units in poor condition (worn seals, ice lining not frozen), whose heat gain is
        # multiplied by the factor. Calibrated against time in range in I7b.
        "degraded_share": 0.15,
        "degraded_factor": 4.0,
    },
    "power": {
        "mean_outage_h": 3.0,                           # ASSUMPTION: mean grid outage duration
        "solar_hours": 10.0,                            # ASSUMPTION: cooling hours on a clear day, centred on 12:30 WAT
        "solar_full_mj": 18.0,                          # ASSUMPTION: irradiance (MJ/m²/day) giving full solar hours
        "generator_hours": [8, 18],                     # ASSUMPTION: generators run 08:00–18:00 WAT
        "fuel_gaps_per_1000_days": 15.0,                # ASSUMPTION
        "fuel_gap_days": [1, 5],                        # ASSUMPTION
    },
    "climate": {"peak_hour_wat": 15.0},                 # Section 3.3.5: daily cycle peaking mid-afternoon
    "sensor": {                                          # ASSUMPTIONS within ±0.5 °C accuracy (Section 3.4.4)
        "noise_sd": 0.2, "resolution": 0.1, "drift_share": 0.05, "drift_max_per_30d": 0.5,
    },
    "usage": {                                           # ASSUMPTION: routine door openings in clinic hours
        "openings_per_day": 4, "clinic_hours": [8, 14], "open_min": [1, 3], "door_factor": 200.0,
    },
    # Failure injector: rate per 1,000 device-days and duration range in minutes (ASSUMPTIONS, Section 3.3.5).
    "failures": {
        "heat_excursion": {"rate": 0.0, "minutes": [120, 720], "factor": 100.0},
        "freeze": {"rate": 0.0, "minutes": [240, 720]},
        "power_loss": {"rate": 0.0, "minutes": [240, 2880]},
        "door_open": {"rate": 0.0, "minutes": [30, 180]},
        "device_silence": {"rate": 0.0, "minutes": [120, 1440]},
        "connectivity_gap": {"rate": 0.0, "minutes": [60, 4320]},
        "sensor_drift": {"rate": 0.0, "minutes": [4320, 10080], "offset": [1.5, 3.0]},
    },
    "limits": {"lower": 2.0, "upper": 8.0, "freeze": -0.5},
    "output": {"readings": False},
}

# Table 3.14: disjoint seed sets, so that no data used to develop or tune is used to evaluate.
SEED_SETS = {"training": range(1, 21), "calibration": range(21, 31), "test": range(101, 111),
             "demo": range(900, 1000)}
