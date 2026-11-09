# Evaluation results

The result files from which the tables in Chapter Five of the report were computed, copied from
`runs/eval/` and `runs/calibration/` at the end of the evaluation (8 November 2026). Report Appendix A,
Table A.2, maps each test to its script, configuration, seeds, command and the files here.

| Folder | Test (report Table 3.10) |
|---|---|
| `calibration/` | Simulator calibration checks (report Table 4.3) |
| `detection/` | E1 detection, E2 false alarms, E3 anomaly comparison: `summary.json` and `report-e1-e3.json` aggregate the ten replications; `detection-101` … `detection-110` hold each seed's configuration, ground truth (`ground_truth.csv`, `conditions.csv`), the prototype's events (`observed.json`) and scores |
| `e4/` | E4 notification latency |
| `e5/` | E5 acknowledgement and escalation |
| `e6/` | E6 custody correctness |
| `e7/` | E7 offline recovery |
| `e8/` | E8 access-control matrix |
| `e10/` | E10 scalability (`load.json`), notification latency under load (`alerts.json`), and the target phase repeated at 1,100 readings/s on 9 Nov 2026 (`load-1100.json`) |
| `e11/`, `e11-run1/` | E11 fault tolerance: second run (after the fix) and first run (defect found) |
| `e12/` | E12 functional coverage: `junit.xml` (the test run), `coverage.json` (as scored at the time, FR5.5 excluded), `coverage-revised-fr5.5.json` (the same run rescored with FR5.5 as revised) |
| `*.log` | Console logs of the runs |

E9 (usability) materials are in `docs/e9/`. All data are simulated; no personal data are included.
