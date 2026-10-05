COMPOSE ?= docker compose
UV_RUN ?= uv run
APIM_FOUNDRY_PORT ?= 8030
export APIM_FOUNDRY_PORT

.PHONY: up smoke down config
up:
	$(COMPOSE) -p foundry-pairing -f compose.yml up --detach --wait

smoke:
	cd ../.. && SMOKE_APIM_FOUNDRY_BASE_URL=http://127.0.0.1:$(APIM_FOUNDRY_PORT) $(UV_RUN) python scripts/smoke_apim_integration.py

down:
	$(COMPOSE) -p foundry-pairing -f compose.yml down --remove-orphans

config:
	$(COMPOSE) -p foundry-pairing -f compose.yml config --quiet
