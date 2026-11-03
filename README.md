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
| I6–I8 | Dashboard, simulator calibration, evaluation | Planned |

## Requirements

- Podman with a machine of at least 8 GB memory (`podman machine set --memory 12288`), and `docker-compose`
  installed so that `podman compose` works
- [uv](https://docs.astral.sh/uv/) (installs Python 3.12 and all dependencies)

## Getting started

```bash
make dev      # create .env, start the stack, apply migrations, load seed data, train the anomaly model
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
| API | http://localhost:8000 (docs at /docs) | Needs a Keycloak access token (`Authorization: Bearer …`) |
| Notification service | http://localhost:8001 | Sends SMS; the gateway's callbacks are `/sms/<SMS_CALLBACK_TOKEN>/inbound` and `/delivery` |
| SMS gateway emulator | http://localhost:8002 | Speaks Africa's Talking's protocol; see below |
| ingestion-bridge, storage-writer, detection, incident-service, notification-service, sms-gateway, api | — | Built from `Containerfile`; `make up` rebuilds after code changes |

Audit anchors (the daily copy of each audit chain's latest hash) are written to `anchors/` on the host,
outside the database. `make reset` deletes them with the data they describe. To verify the audit trail
by hand: `uv run python -m coldchain.audit verify`.

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

To use the Africa's Talking sandbox instead: create an account at africastalking.com, open the sandbox
app and generate an API key; in `.env` set `SMS_GATEWAY_URL=https://api.sandbox.africastalking.com` and
`AT_API_KEY=<the key>`, then `podman compose up -d notification-service`. Messages then appear in the
sandbox's phone simulator. For replies and delivery reports to reach the prototype, the sandbox's
callback URLs must be set to a public HTTPS address that forwards to port 8001 (for example a tunnel),
followed by `/sms/<SMS_CALLBACK_TOKEN>/inbound` and `/sms/<SMS_CALLBACK_TOKEN>/delivery`.

After editing a file in `infra/`, restart that service (e.g. `podman compose restart mosquitto`): containers read their configuration only at start.

## Layout

```
compose.yaml              local stack
infra/                    configuration for PostgreSQL, Keycloak and Mosquitto
migrations/               database schema (Alembic, written in SQL)
seeds/                    development reference data and test users
scripts/                  seed loader; generator for the Keycloak realm and test users
src/coldchain/            messages.py (device message format), ingestion/ (bridge, writer),
                          detection/ (rules, anomaly, service), incidents/ (policy, ownership, store,
                          workflows, service), notify/ (templates, codes, commands, gateway, store,
                          service), custody.py, audit.py, outbox.py, api/, simulator/ (devices, SMS gateway)
models/                   trained anomaly models (make train; not committed)
tests/                    acceptance and unit tests
```

## Test users

`scripts/gen_dev_identity.py` defines one test user per role and writes both the Keycloak realm
(`infra/keycloak/coldchain-realm.json`, including generated passwords) and the matching `app_user` rows
(`seeds/dev_users.sql`). These are development credentials only.

## Recording defects

Defects are recorded as described in Section 3.1.2: one issue per defect with the requirement affected,
severity (critical, major, minor) and steps to reproduce, fixed in its own commit with a regression test.
