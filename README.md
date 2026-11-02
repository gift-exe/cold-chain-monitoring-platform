# Cold chain integrity monitoring and response (prototype)

Prototype for the MIT Professional Master's Project, *a closed-loop cold chain integrity monitoring and
response system for Nigeria's vaccine supply chain*. The design is in Chapter Three of the report; table
and requirement numbers in the code (e.g. FR1.4, Table 3.18) refer to it.

## Status

| Iteration | Scope | Status |
|---|---|---|
| I1 Foundations | Repository, local stack, database schema, identity server with roles and jurisdictions | Done |
| I2 Ingestion | MQTT bridge, Kafka, storage writer, validation and de-duplication, basic simulator | Done |
| I3–I8 | Detection, incidents and custody, notification, dashboard, simulator, evaluation | Planned |

## Requirements

- Podman with a machine of at least 8 GB memory (`podman machine set --memory 12288`), and `docker-compose`
  installed so that `podman compose` works
- [uv](https://docs.astral.sh/uv/) (installs Python 3.12 and all dependencies)

## Getting started

```bash
make dev      # create .env, start the stack, apply migrations, load seed data
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
| ingestion-bridge, storage-writer | — | Built from `Containerfile`; `make up` rebuilds after code changes |

After editing a file in `infra/`, restart that service (e.g. `podman compose restart mosquitto`): containers read their configuration only at start.

## Layout

```
compose.yaml              local stack
infra/                    configuration for PostgreSQL, Keycloak and Mosquitto
migrations/               database schema (Alembic, written in SQL)
seeds/                    development reference data and test users
scripts/                  seed loader; generator for the Keycloak realm and test users
src/coldchain/            messages.py (device message format), ingestion/ (bridge, writer), simulator/
tests/                    acceptance and unit tests
```

## Test users

`scripts/gen_dev_identity.py` defines one test user per role and writes both the Keycloak realm
(`infra/keycloak/coldchain-realm.json`, including generated passwords) and the matching `app_user` rows
(`seeds/dev_users.sql`). These are development credentials only.

## Recording defects

Defects are recorded as described in Section 3.1.2: one issue per defect with the requirement affected,
severity (critical, major, minor) and steps to reproduce, fixed in its own commit with a regression test.
