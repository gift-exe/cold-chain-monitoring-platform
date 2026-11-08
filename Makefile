# Development commands. `make dev` brings everything up from scratch.
COMPOSE = podman compose
# Evaluation stack (Section 3.2.7): own project, volumes and anchors; SMS only to the local gateway
EVAL = podman compose -p coldchain-eval -f compose.yaml -f compose.eval.yaml
EVAL_NET ?= configs/sim/detection-101.toml

INFRA = postgres keycloak mosquitto kafka temporal

.PHONY: eval-image eval-up eval-down eval-reset eval-replay eval-live dev env infra build up down reset migrate seed train demo test test-unit test-resilience lint logs sim-sources calibrate e9-data e9-freeze e9-restore-policy

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

sim-sources:  ## rebuild the simulator's derived data extracts from data/ (GTS=<export folder> to redo movement)
	uv run --with duckdb python -m coldchain.sim sources $(if $(GTS),--gts $(GTS))

calibrate:  ## simulator calibration checks (Table 3.13); report in runs/calibration/
	uv run python -m coldchain.sim calibrate

train:  ## retrain the anomaly model
	uv run python scripts/train_anomaly.py

demo:  ## keep the demo network reporting, with scripted incidents and an outreach trip (Ctrl-C to stop)
	uv run python scripts/demo.py

e9-data:  ## load the walkthrough data for the E9 evaluators (after `make reset` and `make dev`), then run `make demo`
	uv run python scripts/e9_data.py
	podman restart coldchain-incident-service-1

e9-freeze:  ## make the Gwale test facility's fridge freeze for 40 minutes (needs `make demo` running): a fresh incident to watch escalate
	mkdir -p runs && touch runs/e9-freeze

e9-restore-policy:  ## put the escalation time limits back to Table 3.15 after the evaluators shortened them
	uv run python scripts/e9_restore_policy.py

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

eval-image: build  ## pin the current code as the image under evaluation
	podman tag localhost/coldchain-services:dev localhost/coldchain-services:eval

eval-up: env  ## start the evaluation stack on the pinned image (stop the development stack first), migrate, seed, register the Kano network
	mkdir -p runs/eval/anchors
	$(EVAL) up -d --wait
	uv run alembic upgrade head
	uv run python scripts/seed.py --no-demo
	uv run python -m coldchain.sim register $(EVAL_NET)

eval-replay:  ## switch the evaluation stack to accelerated replay (late flag and silence sweep off; 6 detection replicas, one per partition)
	$(EVAL) -f compose.replay.yaml up -d --wait --scale detection=6 ingestion-bridge detection

eval-live:  ## switch the evaluation stack back to real receipt times (one detection replica: it runs the silence sweep)
	$(EVAL) up -d --wait --scale detection=1 ingestion-bridge detection

eval-down:  ## stop the evaluation stack, keeping its data
	$(EVAL) down

eval-reset:  ## stop the evaluation stack and delete its data and anchors
	$(EVAL) down -v
	rm -rf runs/eval/anchors
