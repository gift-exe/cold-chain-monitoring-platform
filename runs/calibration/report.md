# Simulator calibration (I7)

Generated 2026-11-05T09:18:47+00:00; calibration seeds 21–30, 30 simulated days each; network {'sites': {'state_store': 1, 'lga_store': 44, 'health_facility': 541}, 'fixed_units': 586, 'mobile_units': 88, 'lgas': 44}.

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
| Power (source mix) | Facilities connected to the grid | Ilegogie (2025) | 43% ±0.5 percentage points | 42.9% | yes |
| Power (grid) | Grid-connected facilities with about 5 hours a day (Band E) | Ilegogie (2025) | 57% ±0.5 percentage points | 56.9% | yes |
| Power (grid) | Mean daily supply, Band A (130 unit-runs) | NERC (n.d.); outage durations ASSUMPTION | 20–24 h per day | 22.0 h (units: 18.5–24.0) | yes |
| Power (grid) | Mean daily supply, Band B (180 unit-runs) | NERC (n.d.); outage durations ASSUMPTION | 16–20 h per day | 17.9 h (units: 13.9–21.2) | yes |
| Power (grid) | Mean daily supply, Band C (440 unit-runs) | NERC (n.d.); outage durations ASSUMPTION | 12–16 h per day | 13.9 h (units: 10.1–17.4) | yes |
| Power (grid) | Mean daily supply, Band D (830 unit-runs) | NERC (n.d.); outage durations ASSUMPTION | 8–12 h per day | 10.1 h (units: 6.7–13.6) | yes |
| Power (grid) | Mean daily supply, Band E (2070 unit-runs) | NERC (n.d.); outage durations ASSUMPTION | 4–8 h per day | 6.0 h (units: 3.4–9.5) | yes |
| Climate | Simulated daily mean air temperature (360 cell-days) | NASA POWER daily T2M, 2024 | within ±1 °C of the source on every cell-day | largest difference 0.000 °C | yes |
| Power (solar) | Daily irradiance used (360 cell-days) | NASA POWER daily ALLSKY_SFC_SW_DWN, 2024 | identical to the source series | 0 differences | yes |
| Movement | Moving speed on outreach trips (461660 simulated, 5000 GTS) | GTS tracks, Kano, held-out devices | two-sample Kolmogorov–Smirnov p > 0.05 | D = 0.020, p = 0.60; medians 5.8 vs 5.8 m/s | yes |
| Movement | En-route stop duration on outreach trips (49753 simulated, 5000 GTS) | GTS tracks, Kano, held-out devices | two-sample Kolmogorov–Smirnov p > 0.05 | D = 0.013, p = 0.96; medians 6.7 vs 6.8 min | yes |
| Movement | Outreach trips planned and completed | Section 3.3.5 | every mobile unit makes trips | 11076 trips | yes |
| Overall | Proportion of time in range (2–8 °C), fixed units | Lutukai et al. (2019), baseline | within ±10 percentage points of 83.9% | 90.8% (above 8 °C 3.2%, below 2 °C 6.0%; published 2.9% and 6.5%) | yes |
| Failure injector | Injected heat excursion events | run configuration | within the Poisson range of 3516 (3358–3677; 95% family-wise) | 3489 | yes |
| Failure injector | Injected freeze events | run configuration | within the Poisson range of 3516 (3358–3677; 95% family-wise) | 3472 | yes |
| Failure injector | Injected power loss events | run configuration | within the Poisson range of 2214 (2088–2342; 95% family-wise) | 2204 | yes |
| Failure injector | Injected door open events | run configuration | within the Poisson range of 3516 (3358–3677; 95% family-wise) | 3469 | yes |
| Failure injector | Injected device silence events | run configuration | within the Poisson range of 4044 (3874–4216; 95% family-wise) | 4061 | yes |
| Failure injector | Injected connectivity gap events | run configuration | within the Poisson range of 4044 (3874–4216; 95% family-wise) | 4046 | yes |
| Failure injector | Injected sensor drift events | run configuration | within the Poisson range of 1011 (927–1098; 95% family-wise) | 1055 | yes |
| Failure injector | Ground-truth labels complete | Section 3.3.5 | every injection has type, unit, start, end and expected owner | 21796 labels | yes |

Time in range (true temperature, 2–8 °C, fixed units), by seed: 90.9%, 90.6%, 90.8%, 90.9%, 90.8%, 90.7%, 90.6%, 90.9%, 90.9%, 91.0%.

Connectivity (assumption, not calibrated; Table 3.13), mean share of time offline by location class: mobile 18.8%, rural 5.0%, urban 1.0%.

Each seed is run twice: without injected failures (Table 3.14) for every check except the injector's, and with injection at the check rates for the injector checks; connectivity and sensor parameters are assumptions, not calibrated.
