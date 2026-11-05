# Cold chain integrity monitoring and response (prototype)

Prototype for the MIT Professional Master's Project, *a closed-loop cold chain integrity monitoring and
response system for Nigeria's vaccine supply chain*. The design is in Chapter Three of the report; table
and requirement numbers in the code (e.g. FR1.4, Table 3.18) refer to it.

## Status

| Iteration | Scope | Status |
|---|---|---|
| I1 Foundations | Repository, local stack, database schema, identity server with roles and jurisdictions | Done |
| I2 Ingestion | MQTT bridge, Kafka, storage writer, validation and de-duplication, basic simulator | Done |
| I3 Detection | Rules for the integrity events (event time, hysteresis, gaps), device silence, anomaly warnings | Done |
| I4 Incidents and custody | Incidents with ownership, escalation (Temporal), resolution and review; custody handoffs; hash-chained audit trail; API | Done |
| I5 Notification | SMS alerts, escalations and RETRO digests; reply commands with one-time codes; delivery reports, retries and failure handling; dashboard notifications | Done |
| I6 Dashboard | Role-based map, incident, performance, consignment and administration views; live updates; demo network | Done |
| I7a Simulator core | Kano network (GRID3), equipment (WHO PQS), power (NERC bands, solar, generator), climate (NASA POWER) and sensor models; failure injector with ground truth; run configurations and seed sets; calibration checks | Done |
| I7b Simulator completion | Movement on the Kano road network with speeds and stops resampled from GTS tracks; connectivity model; outreach carriers; MQTT device emulator and database registration; load network (Table 3.7); time-in-range (Lutukai et al., 2019) and source-mix (Ilegogie, 2025) checks; calibration report (32/32) | Done |
| I8 | Evaluation | Planned |

## Requirements

- Podman with a machine of at least 8 GB memory (`podman machine set --memory 12288`), and `docker-compose`
  installed so that `podman compose` works
- [uv](https://docs.astral.sh/uv/) (installs Python 3.12 and all dependencies)

## Getting started

```bash
make dev      # create .env, start the stack, apply migrations, load seed data, train the anomaly model
make demo     # keep the demo network reporting, with scripted incidents (Ctrl-C to stop); then open the dashboard
make test     # run all tests (the stack must be running)
make test-resilience   # restart services under load and check no reading is lost (slow)
make down     # stop, keeping data; `make reset` also deletes data
```

| Service | Address | Notes |
|---|---|---|
| PostgreSQL (TimescaleDB, PostGIS) | localhost:5433 | Database `coldchain`; roles `coldchain_owner` (migrations) and `coldchain_app` (services) |
| Keycloak | http://localhost:8080 | Realm `coldchain`; admin console user `admin`, password in `.env` |
| Mosquitto (MQTT) | localhost:1883 | Anonymous access in development only |
| Kafka | localhost:9092 | Single node, KRaft mode, seven-day retention |
| Temporal | localhost:7233; UI http://localhost:8233 | Development server; workflows persisted to SQLite on a volume |
| Dashboard | http://localhost:3000 | Sign in as a test user (below); also serves the API under `/api` |
| API | http://localhost:8000 (docs at /docs) | Needs a Keycloak access token (`Authorization: Bearer …`) |
| Notification service | http://localhost:8001 | Sends SMS; the gateway's callbacks are `/sms/<SMS_CALLBACK_TOKEN>/inbound` and `/delivery` |
| SMS gateway emulator | http://localhost:8002 | Speaks Africa's Talking's protocol; see below |
| ingestion-bridge, storage-writer, detection, incident-service, notification-service, sms-gateway, api | — | Built from `Containerfile`; `make up` rebuilds after code changes |
| dashboard | — | Built from `dashboard/Containerfile` (Vite build served by nginx); `make up` rebuilds it |

Audit anchors (the daily copy of each audit chain's latest hash) are written to `anchors/` on the host,
outside the database. `make reset` deletes them with the data they describe. To verify the audit trail
by hand: `uv run python -m coldchain.audit verify`.

### Dashboard

The dashboard (`dashboard/`, React with MapLibre and OpenStreetMap tiles) shows each role the views and
actions of Table 3.21, filtered to its jurisdiction: a map of facilities and mobile units by status, the
incident list and each incident's readings and timeline, response performance (MTTA, MTTR), consignments,
and administration (product limits for national officers; phone numbers and system status for
administrators). It updates live over a WebSocket (`/api/ws`) when incidents, notifications, custody or
readings change.

`make demo` keeps the demo network (`seeds/dev_z_demo_network.sql`: three Kano LGAs, invented facilities
named "Demo") reporting, with a heat excursion, a freezing event, a power cut, a silent logger and an
outreach team with GPS positions. For development with hot reload: `cd dashboard && npm install && npm run
dev`, then http://localhost:5173 (Vite forwards `/api` to the API).

### SMS

By default SMS go to a local emulator of the gateway (`src/coldchain/simulator/sms_gateway.py`), which
answers exactly as Africa's Talking does and can be made to fail. Try it:

```bash
curl -s localhost:8002/_sim/messages | python -m json.tool          # what has been sent
curl -s -X POST localhost:8002/_sim/inbound -H 'Content-Type: application/json' \
     -d '{"sender": "+2348000000004", "text": "HELP"}'                # a reply from the test OIC's phone
curl -s -X PUT localhost:8002/_sim/numbers/+2348000000004 -H 'Content-Type: application/json' \
     -d '{"outcome": "failed", "times": 2}'                          # the next two messages to it fail
```

To use the Africa's Talking sandbox instead (tried on 4 November 2026):

1. Create an account at africastalking.com and generate an API key in the **sandbox** app. Create a
   shortcode under SMS → Shortcodes.
2. In `.env` set `SMS_GATEWAY_URL=https://api.sandbox.africastalking.com`, `AT_API_KEY=<the key>`, and the
   development settings below. Set `SMS_CALLBACK_TOKEN` to a long random value.
3. Start a tunnel to the notification service (`cloudflared tunnel --url http://localhost:8001`) and set
   the sandbox's callback URLs to `<tunnel address>/sms/<SMS_CALLBACK_TOKEN>/inbound` (incoming
   messages) and `.../delivery` (delivery reports). The address changes each time the tunnel starts.
4. Launch the simulator phone with a number in Nigerian mobile format (e.g. +2348031234567; 0800 numbers
   are refused as `UnsupportedNumberType`) and give a test user that number (`PUT /users/{ref}/phone`).
5. `podman compose up -d notification-service`. Replies are sent to the shortcode; messages from the
   system arrive from the sandbox's default sender, AFRICASTKNG (the shortcode is refused as a sender).

Development settings, never for real use:

| Setting | Effect |
|---|---|
| `SMS_REDIRECT_TO=<number>` | Every SMS goes to this one phone, so all tiers can be seen on one simulator. Replies still act as the user that number belongs to. |
| `SMS_TRUST_SENT=true` | A message the gateway accepts counts as delivered. The sandbox reported messages as `Failed` that reached the phone, and sent no reports for others, so its reports would escalate every alert. |

The automated tests use the emulator; switch `.env` back (`SMS_GATEWAY_URL=http://sms-gateway:8000`,
the two settings empty/false, the test user's number restored) before running them. If messages from the
simulator stop arriving, relaunch the simulator phone: idle sessions stopped forwarding in testing.

### Simulation

The simulation service (Section 3.3.5) runs from a configuration file in `configs/sim/`; the data it draws
on is described in `data/README.md`.

```
make sim-sources [GTS=<GTS export folder>]                      # once: derived extracts from data/
uv run python -m coldchain.sim network [CONFIG]                 # summarise the network
uv run python -m coldchain.sim run configs/sim/detection-101.toml [--readings]   # offline
make calibrate                                                  # Table 3.13 checks, runs/calibration/report.md
```

To send a run's readings to the running stack, register its devices once, then emulate:

```
uv run python -m coldchain.sim register configs/sim/load-101.toml
uv run python -m coldchain.sim emulate configs/sim/load-101.toml --now --speed 1 --connections 2000
uv run python -m coldchain.sim register configs/sim/load-101.toml --retire     # stop accepting them
```

`--now` starts the run at the current minute, `--speed` is simulated minutes per real minute (omit it to
publish as fast as possible). Emulating the scenario network in real time raises real incidents and sends
SMS to the officers on file (or to `SMS_REDIRECT_TO`). For 2,000 connections, raise the open-file limit
first (`ulimit -n 8192`).

A run writes `runs/<name>/`: the effective configuration, the network (`sites.csv`, `units.csv`), the
failure injector's ground truth (`ground_truth.csv`), every true condition window with its cause
(`conditions.csv`), outreach trips (`trips.csv`), connectivity gaps (`connectivity.csv`), a summary, and
with `--readings` every reading (`readings.csv.gz`). Seeds must belong to the set for the run's purpose
(training 1–20, calibration 21–30, test 101–110; Table 3.14). A configuration with
`network.load_fixed_units` and `load_mobile_units` uses the load network: copies of the Kano network as
synthetic states (`NG-L01`, ...), with serials `LDnn-...` and `LDM-nnnnn`.

After editing a file in `infra/`, restart that service (e.g. `podman compose restart mosquitto`): containers read their configuration only at start.

## Layout

```
compose.yaml              local stack
infra/                    configuration for PostgreSQL, Keycloak and Mosquitto
migrations/               database schema (Alembic, written in SQL)
seeds/                    development reference data and test users
scripts/                  seed loader; generator for the Keycloak realm and test users; demo feeder
src/coldchain/            messages.py (device message format), ingestion/ (bridge, writer),
                          detection/ (rules, anomaly, service), incidents/ (policy, ownership, store,
                          workflows, service), notify/ (templates, codes, commands, gateway, store,
                          service), custody.py, admin.py, audit.py, outbox.py, api/ (app, views),
                          simulator/ (basic devices, SMS gateway), sim/ (simulation service:
                          sources, gts, params, config, network, movement,
                          engine, emulator, register, calibrate)
configs/sim/              simulation run configurations
data/                     simulator source data (raw files not committed; derived/ extracts are)
runs/                     simulation outputs (not committed, except the calibration report)
dashboard/                React dashboard (Vite), its Containerfile and nginx configuration
models/                   trained anomaly models (make train; not committed)
tests/                    acceptance and unit tests
```

## Test users

Sign in to the dashboard as any of `test.national`, `test.state`, `test.lga`, `test.facility`,
`test.outreach`, `test.driver` or `test.admin`; their passwords are in the realm file below.

`scripts/gen_dev_identity.py` defines one test user per role and writes both the Keycloak realm
(`infra/keycloak/coldchain-realm.json`, including generated passwords) and the matching `app_user` rows
(`seeds/dev_users.sql`). These are development credentials only.

## Recording defects

Defects are recorded as described in Section 3.1.2: one issue per defect with the requirement affected,
severity (critical, major, minor) and steps to reproduce, fixed in its own commit with a regression test.
