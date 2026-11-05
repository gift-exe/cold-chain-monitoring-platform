# Simulator calibration (I7a)

Generated 2026-11-05T06:28:47+00:00; calibration seeds 21–30, 30 simulated days each; network {'sites': {'state_store': 1, 'lga_store': 44, 'health_facility': 541}, 'fixed_units': 586, 'mobile_units': 88, 'lgas': 44}.

| Model | Check | Source | Criterion | Result | Passed |
|---|---|---|---|---|---|
| Equipment | Hold-over, Solar direct-drive ice-lined refrigerator | WHO PQS E003-164, B Medical Systems TCW 50R SDD (hold-over 94:15 at +43 °C) | within ±20% of 94.2 h | 94.2 h (+0.0%) | yes |
| Equipment | Pull-down to 3.0 °C, Solar direct-drive ice-lined refrigerator | ASSUMPTION (pull-down time not in the PQS record) | within ±20% of 8.0 h | 8.0 h | yes |
| Equipment | Hold-over, Mains ice-lined refrigerator | WHO PQS E003-151, B Medical Systems TCW 240R AC (hold-over 115:19 at +43 °C) | within ±20% of 115.3 h | 115.3 h (+0.0%) | yes |
| Equipment | Pull-down to 3.0 °C, Mains ice-lined refrigerator | ASSUMPTION (pull-down time not in the PQS record) | within ±20% of 8.0 h | 8.0 h | yes |
| Equipment | Hold-over, Walk-in cold room (state and LGA stores) | ASSUMPTION: PQS E001 cold rooms state no hold-over; 24 h assumed | within ±20% of 24.0 h | 24.0 h (+0.0%) | yes |
| Equipment | Pull-down to 3.5 °C, Walk-in cold room (state and LGA stores) | ASSUMPTION (pull-down time not in the PQS record) | within ±20% of 6.0 h | 6.0 h | yes |
| Equipment | Hold-over, Long-range cold box | WHO PQS E004-084, Mark Enterprises MCBX-01 (cold life 129:56 at +43 °C) | within ±20% of 129.9 h | 129.9 h (+0.0%) | yes |
| Equipment | Hold-over, Long-range vaccine carrier | WHO PQS E004-078, Trimurti Plast TPVC 46 (cold life 47:02 at +43 °C) | within ±20% of 47.0 h | 47.0 h (+0.0%) | yes |
| Network | Vaccine-holding health facilities | GRID3 v2.0 (Kano) | equal to the source count (541) | 541 | yes |
| Network | LGA stores | one per LGA | equal to the number of LGAs (44) | 44 | yes |
| Power (source mix) | Facilities by power source | Ilegogie (2025) grid share; rest ASSUMPTION | each share within ±0.5 percentage points of the configuration | solar 40.1%, grid 25.0%, grid_generator 17.9%, generator 17.0% | yes |
| Power (grid) | Mean daily supply, Band A (140 unit-runs) | NERC (n.d.); outage durations ASSUMPTION | 20–24 h per day | 22.2 h (units: 18.7–24.0) | yes |
| Power (grid) | Mean daily supply, Band B (280 unit-runs) | NERC (n.d.); outage durations ASSUMPTION | 16–20 h per day | 18.1 h (units: 14.3–21.2) | yes |
| Power (grid) | Mean daily supply, Band C (560 unit-runs) | NERC (n.d.); outage durations ASSUMPTION | 12–16 h per day | 14.0 h (units: 10.7–18.0) | yes |
| Power (grid) | Mean daily supply, Band D (830 unit-runs) | NERC (n.d.); outage durations ASSUMPTION | 8–12 h per day | 10.1 h (units: 6.7–14.2) | yes |
| Power (grid) | Mean daily supply, Band E (960 unit-runs) | NERC (n.d.); outage durations ASSUMPTION | 4–8 h per day | 6.0 h (units: 3.2–8.9) | yes |
| Climate | Simulated daily mean air temperature (360 cell-days) | NASA POWER daily T2M, 2024 | within ±1 °C of the source on every cell-day | largest difference 0.000 °C | yes |
| Power (solar) | Daily irradiance used (360 cell-days) | NASA POWER daily ALLSKY_SFC_SW_DWN, 2024 | identical to the source series | 0 differences | yes |
| Failure injector | Injected heat excursion events | run configuration | within the Poisson range of 3516 (3358–3677; 95% family-wise) | 3566 | yes |
| Failure injector | Injected freeze events | run configuration | within the Poisson range of 3516 (3358–3677; 95% family-wise) | 3427 | yes |
| Failure injector | Injected power loss events | run configuration | within the Poisson range of 2214 (2088–2342; 95% family-wise) | 2187 | yes |
| Failure injector | Injected door open events | run configuration | within the Poisson range of 3516 (3358–3677; 95% family-wise) | 3447 | yes |
| Failure injector | Injected device silence events | run configuration | within the Poisson range of 3516 (3358–3677; 95% family-wise) | 3463 | yes |
| Failure injector | Injected connectivity gap events | run configuration | within the Poisson range of 3516 (3358–3677; 95% family-wise) | 3483 | yes |
| Failure injector | Injected sensor drift events | run configuration | within the Poisson range of 879 (800–960; 95% family-wise) | 857 | yes |
| Failure injector | Ground-truth labels complete | Section 3.3.5 | every injection has type, unit, start, end and expected owner | 20430 labels | yes |

Time in range (true temperature, 2–8 °C), by seed: 94.8%, 94.7%, 94.9%, 94.5%, 95.0%, 94.9%, 94.4%, 94.8%, 94.6%, 95.2%. Reported for information; the check against published figures is part of I7b.
