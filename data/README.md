# Simulator source data

Raw source files sit in this folder and are **not committed** (they are large and licensed by their
publishers). `uv run python -m coldchain.sim sources` reduces them to the small extracts in `derived/`, which
are committed so that every simulation run can be reproduced.

| File | Source | Used for | Licence |
|---|---|---|---|
| `GRID3_NGA_health_facilities_v2_0_*.csv` | GRID3 Nigeria health facilities v2.0 (CIESIN, Columbia University; Humanitarian Data Exchange, dataset `a3b971e7-2f6e-4d1c-b4b2-87698545e5ca`, issued 12 Dec 2024) | Network model: facility names, LGAs, levels, ownership, locations | Creative Commons Attribution (catalogue record); confirm the version on the HDX page before citing |
| `nasa_power/power_<lat>_<lon>_2024.json` | NASA POWER daily point API, community AG, parameters T2M, T2M_MIN, T2M_MAX, ALLSKY_SFC_SW_DWN, 1 Jan–31 Dec 2024, one point per 0.5° × 0.625° grid cell containing a Kano facility (12 cells); downloaded 5 Nov 2026 | Climate model; solar power model | NASA open data; credit "NASA Langley Research Center POWER Project" |
| `hotosm_nga_roads_osm_gpkg/roads.gpkg` | OpenStreetMap roads for Nigeria, HOT export (oex 0.4.16), snapshot 9 Oct 2026 | Movement model: Kano road graph | ODbL; credit "© OpenStreetMap contributors" |
| GTS export (kept outside the repository, e.g. `~/Documents/Miva/gts/export_1133_*`) | Geospatial Tracking System tracks, NPHCDA polio campaign, March 2026 OBR phase 2 (tracking round 1133), exported 5 Nov 2026 | Movement model: speeds, stops, working hours | Personal data (Nigeria Data Protection Act 2023): used with the data holder's permission, read in place, never copied here; only anonymous distributions are derived (Section 3.3.5) |
| `hotosm_nga_waterways_osm_geojson/` | OpenStreetMap waterways for Nigeria, HOT export | Not used | ODbL |

## Derived extracts

- `derived/kano_facilities.csv`: the 1,723 Kano State facilities in GRID3 v2.0, with GRID3 id, NHFR code,
  name, LGA, ward, level, ownership and coordinates.
- `derived/kano_climate_2024.csv`: 12 cells × 366 days of daily mean, minimum and maximum air temperature at
  2 m (°C) and all-sky surface shortwave irradiance (MJ/m²/day).
- `derived/kano_roads.csv.gz`: Kano's drivable roads (motorway to track; paths and footways left out) as a
  graph: 168,197 junctions and 244,042 edges with class, length and shape; largest connected part only.
- `derived/gts_kano_movement.json`: from the Kano teams' tracks on the campaign days (31,247 team-days):
  resampling libraries of vehicle and on-foot moving runs (segment speeds and durations) and stop durations,
  quantiles of working-day start and end hours and daily distance, and a held-out check sample. It holds no
  identifiers, positions or dates. To rebuild it: `make sim-sources GTS=<export folder>` (needs DuckDB,
  fetched by `uv run --with duckdb`).

To download the NASA POWER files again: see `coldchain.sim.sources` (the request parameters are listed
above); the API needs no key.
