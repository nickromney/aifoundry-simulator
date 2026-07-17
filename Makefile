.DEFAULT_GOAL := help

COMPOSE ?= docker compose
UV_RUN ?= uv run

FOUNDRY_PORT ?= 8020
FOUNDRY_BASE_URL ?= http://127.0.0.1:$(FOUNDRY_PORT)
SMOKE_FOUNDRY_BASE_URL ?= $(FOUNDRY_BASE_URL)

export FOUNDRY_PORT SMOKE_FOUNDRY_BASE_URL

HELP_FMT := "  %-24s %s\n"

.PHONY: help prereqs hooks build up down restart logs smoke smoke-foundry smoke-cache smoke-safety test fmt lint lint-check lint-yaml compose-config local-ci

help:
	@printf "Run:\n"
	@printf "\nStack lifecycle:\n"
	@printf $(HELP_FMT) "up" "Build and start the simulator on localhost:$(FOUNDRY_PORT)"
	@printf $(HELP_FMT) "down" "Stop the simulator stack"
	@printf $(HELP_FMT) "restart" "Recreate the simulator container"
	@printf $(HELP_FMT) "logs" "Follow simulator logs"
	@printf $(HELP_FMT) "build" "Build the container image"
	@printf "\nSmoke tests (need a running stack):\n"
	@printf $(HELP_FMT) "smoke" "Run every smoke script"
	@printf $(HELP_FMT) "smoke-foundry" "Core surfaces: chat, embeddings, v1, auth, management"
	@printf $(HELP_FMT) "smoke-cache" "Semantic cache: miss/hit, threshold, streaming replay"
	@printf $(HELP_FMT) "smoke-safety" "Content safety: analyze, shield, blocklists, filtering"
	@printf "\nDevelopment:\n"
	@printf $(HELP_FMT) "test" "Run pytest with branch coverage"
	@printf $(HELP_FMT) "fmt" "Format Python with ruff"
	@printf $(HELP_FMT) "lint" "Run every lint gate"
	@printf $(HELP_FMT) "lint-check" "ruff format --check and ruff check"
	@printf $(HELP_FMT) "lint-yaml" "yamllint over the repo"
	@printf $(HELP_FMT) "compose-config" "Validate the compose file"
	@printf $(HELP_FMT) "local-ci" "gitleaks + lint + test + compose-config (pre-push gate)"
	@printf $(HELP_FMT) "hooks" "Install lefthook git hooks"
	@printf $(HELP_FMT) "prereqs" "Check Docker and uv are ready"

prereqs:
	@command -v docker >/dev/null 2>&1 || { echo "docker is required"; exit 1; }
	@docker info >/dev/null 2>&1 || { echo "Docker daemon is not running"; exit 1; }
	@command -v uv >/dev/null 2>&1 || { echo "uv is required for tests and smoke scripts"; exit 1; }
	@echo "prereqs OK"

hooks:
	lefthook install

build:
	$(COMPOSE) build

up:
	$(COMPOSE) up --build --detach --wait
	@echo "aifoundry-simulator ready on $(FOUNDRY_BASE_URL)"
	@echo "try: curl $(FOUNDRY_BASE_URL)/foundry/health"

down:
	$(COMPOSE) down --remove-orphans

restart:
	$(COMPOSE) up --detach --force-recreate

logs:
	$(COMPOSE) logs --follow aifoundry-simulator

smoke: smoke-foundry smoke-cache smoke-safety

smoke-foundry:
	$(UV_RUN) python scripts/smoke_foundry.py

smoke-cache:
	$(UV_RUN) python scripts/smoke_semantic_cache.py

smoke-safety:
	$(UV_RUN) python scripts/smoke_content_safety.py

test:
	$(UV_RUN) --extra dev pytest -q --cov=app --cov-branch --cov-report=term-missing --cov-report=xml
	mkdir -p coverage-reports
	cp coverage.xml coverage-reports/python-coverage-report.xml

fmt:
	$(UV_RUN) --extra dev ruff format .

lint: lint-check lint-yaml

lint-check:
	$(UV_RUN) --extra dev ruff format --check .
	$(UV_RUN) --extra dev ruff check .

lint-yaml:
	$(UV_RUN) --extra dev yamllint .

compose-config:
	$(COMPOSE) config --quiet

local-ci:
	@command -v gitleaks >/dev/null 2>&1 || { echo "gitleaks is required for local CI"; exit 1; }
	gitleaks detect --source . --config .gitleaks.toml --redact
	@$(MAKE) --no-print-directory lint
	@$(MAKE) --no-print-directory test
	@$(MAKE) --no-print-directory compose-config
