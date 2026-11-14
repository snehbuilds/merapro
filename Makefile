# The only interface anyone needs to remember (docs/10 §2).
# On Windows without make: the same commands are in scripts/dev.ps1.
.PHONY: setup up down test test-unit test-integration lint typecheck migrate logs web-dev preflight e2e

BACKEND := cd backend &&

setup:
	@test -f .env || cp .env.example .env
	$(BACKEND) uv sync
	corepack enable pnpm && pnpm install

up:
	docker compose up -d --build --wait

down:
	docker compose down

test:
	$(BACKEND) uv run pytest

test-unit:
	$(BACKEND) uv run pytest tests/unit

test-integration:
	$(BACKEND) uv run pytest -m integration

lint:
	$(BACKEND) uv run ruff check . && uv run ruff format --check .
	pnpm -r lint

typecheck:
	$(BACKEND) uv run mypy src
	pnpm -r typecheck

migrate:
	$(BACKEND) uv run alembic revision --autogenerate -m "$(name)"

logs:
	docker compose logs -f $(service)

web-dev:
	pnpm --filter web dev

# Go-live checks inside the running api container, with its real settings (P2-M0).
# Secrets are never printed. Extra flags: make preflight args="--live --send-test-email you@example.com"
args ?= --live
preflight:
	docker compose exec api python -m mera.preflight $(args)

# Playwright browser test of the approval loop against the running stack (P2-M0.7).
e2e:
	pnpm --filter web e2e
