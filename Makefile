# Development commands. `make dev` brings everything up from scratch.
COMPOSE = podman compose

.PHONY: dev env up down reset migrate seed test test-unit lint logs

dev: env up migrate seed  ## start the stack, apply migrations, load seed data

env:  ## create .env from the example if it does not exist
	@test -f .env || cp .env.example .env

up: env  ## start the stack and wait until every service is healthy
	$(COMPOSE) up -d --wait

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
	uv run pytest -m "not integration"

lint:
	uv run ruff check .

logs:
	$(COMPOSE) logs -f --tail=100
