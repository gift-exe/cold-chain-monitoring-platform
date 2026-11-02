# Development commands. `make dev` brings everything up from scratch.
COMPOSE = podman compose

INFRA = postgres keycloak mosquitto kafka

.PHONY: dev env infra up down reset migrate seed test test-unit test-resilience lint logs

dev: env infra migrate seed up  ## start the stack, apply migrations, load seed data, start services

env:  ## create .env from the example if it does not exist
	@test -f .env || cp .env.example .env

infra: env  ## start databases, identity server and brokers, and wait until they are healthy
	$(COMPOSE) up -d --wait $(INFRA)

up: env  ## start everything, rebuilding the services image if the code changed
	$(COMPOSE) up -d --build --wait

down:  ## stop the stack, keeping data
	$(COMPOSE) down

reset:  ## stop the stack and delete all data
	$(COMPOSE) down -v

migrate:
	uv run alembic upgrade head

seed:
	uv run python scripts/seed.py

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
