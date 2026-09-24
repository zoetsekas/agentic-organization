# Local bring-up for the Docker alpha.
#
# Nothing in this repository has ever been built or started: there is no Docker
# daemon in the environment it was written in. These targets are the path from
# "generated and parsed" to "running", and the first person to run them should
# expect to fix something. `make alpha` is the whole loop.

SHELL := /bin/bash
PY ?= python3
COMPOSE ?= docker compose
SPEC ?= examples/acme.system.yaml
BINDING ?= examples/acme.binding.yaml
TENANT_DIR ?= build/tenant
PORT ?= 8000

export PYTHONPATH := src

.PHONY: help
help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
	  | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}'

# -- checks that need no daemon -------------------------------------------

.PHONY: test
test: ## Run the test suite
	$(PY) -m pytest -q

.PHONY: check
check: ## Validate the records, the spec and the generated Compose files
	$(PY) -m orgagents.cli records validate
	$(PY) -m orgagents.cli spec validate $(SPEC)
	$(PY) -m orgagents.cli phase $(SPEC) --binding $(BINDING) --target local

.PHONY: generate
generate: ## Compile the example system for the local target
	$(PY) -m orgagents.cli compile $(SPEC) --binding $(BINDING) \
	  --target local --out $(TENANT_DIR) --force
	@echo "generated into $(TENANT_DIR)"

.PHONY: config
config: generate secrets ## Ask Docker to parse every generated Compose file
	@set -e; for f in $$(find $(TENANT_DIR) -name 'docker-compose*.y*ml'); do \
	  echo "== $$f"; $(COMPOSE) -f $$f config -q --no-interpolate; done
	$(COMPOSE) -f docker-compose.yml config -q
	$(COMPOSE) -f docker/compose/fabric.yml config -q --no-interpolate
	@echo "every Compose file parses"

# -- things that need a daemon --------------------------------------------

.PHONY: secrets
secrets: ## Write the designer's Postgres password secret if missing (ADR-0114)
	$(PY) scripts/designer_secrets.py init

.PHONY: rotate-db-password
rotate-db-password: ## New designer Postgres password, applied to the running stack
	$(PY) scripts/designer_secrets.py rotate --apply

.PHONY: build
build: ## Build the designer image
	$(COMPOSE) build

.PHONY: up
up: secrets ## Start the designer (UI at /ui/)
	$(COMPOSE) up -d
	@$(MAKE) --no-print-directory wait
	@echo "designer up:  http://localhost:$(PORT)/ui/"

.PHONY: seed
seed: secrets ## Start the designer with the demo organization
	ORGAGENTS_SEED=1 $(COMPOSE) up -d --build
	@$(MAKE) --no-print-directory wait

.PHONY: wait
wait: ## Block until the designer answers /healthz
	@echo -n "waiting for the designer"; \
	for i in $$(seq 1 60); do \
	  if curl -fsS http://localhost:$(PORT)/healthz >/dev/null 2>&1; then \
	    echo " ok"; exit 0; fi; \
	  echo -n "."; sleep 2; \
	done; \
	echo; echo "designer did not become healthy; logs follow:"; \
	$(COMPOSE) logs --tail=50 designer; exit 1

.PHONY: fabric
fabric: ## Start the fabric plane (control plane, observability, IdP)
	$(COMPOSE) -f docker/compose/fabric.yml up -d

.PHONY: tenant-up
tenant-up: generate ## Start the generated tenant stack
	@d=$$(dirname $$(find $(TENANT_DIR) -name 'docker-compose*.y*ml' | head -1)); \
	  echo "starting $$d"; $(COMPOSE) -f $$d/docker-compose.yml up -d --build

.PHONY: smoke
smoke: ## The alpha check: build, start, run one agent, report
	$(PY) scripts/smoke.py

.PHONY: down
down: ## Stop everything this repository starts
	-$(COMPOSE) down -v
	-$(COMPOSE) -f docker/compose/fabric.yml down -v
	@d=$$(dirname $$(find $(TENANT_DIR) -name 'docker-compose*.y*ml' 2>/dev/null | head -1)); \
	  if [ -n "$$d" ]; then $(COMPOSE) -f $$d/docker-compose.yml down -v || true; fi

.PHONY: alpha
alpha: check config build up smoke ## Everything, in order
	@echo "alpha loop complete"

# -- AYC on this workstation (ADR-0109) --------------------------------------
# The AYC example, compiled for the local target with a stub model and mock
# systems, under its own Compose project (`ayc-local`) and its own ports, so it
# runs beside the designer. The same steps run without make:
#   python examples/ayc/local_stack.py up | e2e | ps | down

.PHONY: ayc-generate
ayc-generate: ## Compile AYC for the local target into examples/ayc/generated/local
	$(PY) examples/ayc/local_stack.py generate

.PHONY: ayc-up
ayc-up: ## Build and start AYC locally (chat on :18080)
	$(PY) examples/ayc/local_stack.py up

.PHONY: ayc-e2e
ayc-e2e: ## Run AYC's purchase-to-pay scenario against the running stack
	$(PY) examples/ayc/end_to_end_local.py

.PHONY: ayc-ps
ayc-ps: ## Show AYC's containers
	$(PY) examples/ayc/local_stack.py ps

.PHONY: ayc-down
ayc-down: ## Stop AYC (its volumes are kept; `local_stack.py down --volumes` drops them)
	$(PY) examples/ayc/local_stack.py down

.PHONY: images
images: ## Resolve the image lock against the registry
	./docker/resolve-images.sh
