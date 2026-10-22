# Smart Pricing Monitor — developer entry points. Run `make help` for the list.
SHELL := /bin/bash
.DEFAULT_GOAL := help

BACKEND  := backend
FRONTEND := frontend
UV       := uv --directory $(BACKEND)
SPM      := $(UV) run spm

.PHONY: help setup up down migrate seed api worker scheduler web dev lint typecheck test test-int check sim

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

setup: ## Install backend (uv) and frontend (pnpm) deps, install pre-commit hooks
	$(UV) sync
	@if [ -f $(FRONTEND)/package.json ]; then pnpm --dir $(FRONTEND) install; else echo "frontend/ not present yet — skipping pnpm install"; fi
	$(UV) run pre-commit install

up: ## Start Postgres, Redis and Mailpit (waits until healthy)
	docker compose up -d --wait

down: ## Stop local infrastructure
	docker compose down

migrate: ## Apply database migrations (M03)
	$(SPM) migrate

seed: ## Load catalog seeds (M04)
	$(SPM) seed

api: ## Run the API on :8000 (M01)
	$(SPM) api

worker: ## Run the event consumers (M13)
	$(SPM) worker

scheduler: ## Run the scheduler (M17)
	$(SPM) scheduler

web: ## Run the PWA dev server on :5173 (M19)
	pnpm --dir $(FRONTEND) dev

dev: up migrate seed ## up + migrate + seed, then run api, worker, scheduler and web together
	@trap 'kill 0' EXIT; \
	$(MAKE) api & $(MAKE) worker & $(MAKE) scheduler & \
	if [ -f $(FRONTEND)/package.json ]; then $(MAKE) web & fi; \
	wait

lint: ## ruff check + ruff format --check
	$(UV) run ruff check .
	$(UV) run ruff format --check .

typecheck: ## mypy + import-linter
	$(UV) run mypy
	$(UV) run lint-imports

test: ## Unit tests (no Docker)
	$(UV) run pytest -m "not integration and not e2e"

test-int: ## Integration + e2e tests (needs `make up`)
	@# pytest exits 5 when no tests are collected (none exist before M03); treat that as success.
	$(UV) run pytest -m "integration or e2e"; rc=$$?; if [ $$rc -eq 5 ]; then exit 0; else exit $$rc; fi

check: lint typecheck test ## lint + typecheck + unit tests

sim: ## Run the simulator & evaluation harness (M14)
	$(UV) run python -m tools.simulator
