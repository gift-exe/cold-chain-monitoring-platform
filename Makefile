# Development commands. `make dev` brings everything up from scratch.
COMPOSE = podman compose

INFRA = postgres keycloak mosquitto kafka temporal

.PHONY: dev env infra build up down reset migrate seed train demo test test-unit test-resilience lint logs sim-sources calibrate

dev: env anchors infra migrate seed models/default.joblib up  ## start the stack, apply migrations, load seed data, start services

anchors:  ## audit anchors are kept on the host, outside the database
	mkdir -p anchors

env:  ## create .env from the example if it does not exist
	@test -f .env || cp .env.example .env

infra: env  ## start databases, identity server and brokers, and wait until they are healthy
	$(COMPOSE) up -d --wait $(INFRA)

build:  ## build the services and dashboard images (podman build: compose --build hangs with Podman)
	podman build -q -t localhost/coldchain-services:dev -f Containerfile .
	podman build -q -t localhost/coldchain-dashboard:dev -f dashboard/Containerfile dashboard

up: env build  ## start everything, rebuilding the services image if the code changed
	$(COMPOSE) up -d --wait

down:  ## stop the stack, keeping data
	$(COMPOSE) down

reset:  ## stop the stack and delete all data, including the audit anchors (they describe the old database)
	$(COMPOSE) down -v
	rm -rf anchors

migrate:
	uv run alembic upgrade head

seed:
	uv run python scripts/seed.py

models/default.joblib:
	uv run python scripts/train_anomaly.py

sim-sources:  ## rebuild the simulator's derived data extracts from data/
	uv run python -m coldchain.sim sources

calibrate:  ## simulator calibration checks (Table 3.13); report in runs/calibration/
	uv run python -m coldchain.sim calibrate

train:  ## retrain the anomaly model
	uv run python scripts/train_anomaly.py

demo:  ## keep the demo network reporting, with scripted incidents and an outreach trip (Ctrl-C to stop)
	uv run python scripts/demo.py

test:  ## all tests (stack must be running)
	uv run pytest

test-unit:  ## tests that need no running services
	uv run pytest -m "not integration and not resilience"

test-resilience:  ## restart services under load and check nothing is lost (slow)
	uv run pytest -m resilience

lint:
	uv run ruff check .

logs:
	$(COMPOSE) logs -f --tail=100
