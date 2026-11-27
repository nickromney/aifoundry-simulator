SHELL := /bin/bash
REPO_ROOT := $(abspath ..)
BIN := bin/terraform-provider-foundrysim
ADMIN_KEY ?= tfacc-admin-key

.PHONY: build test vet fmt-check check testacc example-plan clean

build:
	go build -o $(BIN) .

test:
	go test ./...

vet:
	go vet ./...

fmt-check:
	@test -z "$$(gofmt -l .)" || { gofmt -l .; echo "run gofmt -w ."; exit 1; }

check: fmt-check vet test

# Starts a throwaway simulator on a free loopback port with the example
# config's deployments and its own admin key, runs the acceptance tests
# against it, and stops it again.
testacc:
	@set -euo pipefail; \
	port="$${SIM_PORT:-$$(python3 -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])')}"; \
	log="$$(mktemp)"; config="$$(mktemp)"; \
	python3 -c 'import json, sys; c = json.load(open(sys.argv[1])); c["auth"]["admin_keys"] = [sys.argv[2]]; json.dump(c, open(sys.argv[3], "w"))' \
		"$(REPO_ROOT)/examples/foundry.json" "$(ADMIN_KEY)" "$$config"; \
	( cd "$(REPO_ROOT)" && AIFOUNDRY_CONFIG_PATH="$$config" exec uv run uvicorn app.main:app --host 127.0.0.1 --port "$$port" --no-access-log ) >"$$log" 2>&1 & \
	pid=$$!; \
	trap 'kill "$$pid" 2>/dev/null || true; wait "$$pid" 2>/dev/null || true; rm -f "$$config"' EXIT; \
	for _ in $$(seq 1 100); do \
		curl -fsS "http://127.0.0.1:$$port/foundry/health" >/dev/null 2>&1 && break; \
		kill -0 "$$pid" 2>/dev/null || { cat "$$log"; exit 1; }; \
		sleep 0.2; \
	done; \
	echo "simulator on :$$port (log $$log)"; \
	TF_ACC=1 FOUNDRYSIM_ENDPOINT="http://127.0.0.1:$$port" FOUNDRYSIM_ADMIN_KEY="$(ADMIN_KEY)" \
		go test ./internal/provider -run '^TestAcc' -count=1 -v $(TESTARGS)

# Plans examples/main.tf with this checkout's provider build, without touching
# your global Terraform CLI configuration.
example-plan: build
	@printf 'provider_installation {\n  dev_overrides {\n    "nickromney/foundrysim" = "%s"\n  }\n  direct {}\n}\n' "$(CURDIR)/bin" > bin/dev.tfrc
	TF_CLI_CONFIG_FILE="$(CURDIR)/bin/dev.tfrc" terraform -chdir=examples plan

clean:
	rm -rf bin
