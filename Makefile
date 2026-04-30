.DEFAULT_GOAL := help
SHELL := /bin/bash

PROJECT      ?= andela-mcp
PYTHON       ?= 3.12
TF_DIR       := infra/terraform
ENV          ?= dev

.PHONY: help
help: ## Show this help.
	@awk 'BEGIN {FS = ":.*##"; printf "Targets:\n"} /^[a-zA-Z_-]+:.*##/ {printf "  %-18s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

.PHONY: install
install: ## Sync dev dependencies into a uv-managed venv.
	uv python install $(PYTHON)
	uv sync --all-extras
	uv run --active pre-commit install

.PHONY: lint
lint: ## Ruff lint + format check.
	uv run --active ruff check .
	uv run --active ruff format --check .

.PHONY: fmt
fmt: ## Auto-format and apply lint fixes.
	uv run --active ruff check --fix .
	uv run --active ruff format .

.PHONY: typecheck
typecheck: ## Static type-check src/.
	uv run --active mypy src

.PHONY: test
test: ## Run unit tests with coverage.
	uv run --active pytest -m "not integration"

.PHONY: test-integration
test-integration: ## Run integration tests (require external services).
	uv run --active pytest -m integration

.PHONY: check
check: lint typecheck test ## Lint, type-check, test.

.PHONY: run
run: ## Run the service locally.
	uv run --active python -m andela_mcp

.PHONY: docker-build
docker-build: ## Build the production container image.
	docker build -t $(PROJECT):local .

.PHONY: docker-run
docker-run: docker-build ## Build and run the container locally.
	docker run --rm -p 8080:8080 --env-file env.example $(PROJECT):local

.PHONY: tf-init
tf-init: ## terraform init for $(ENV).
	cd $(TF_DIR) && terraform init -backend-config="bucket=$$TF_STATE_BUCKET" -backend-config="prefix=$(PROJECT)/$(ENV)"

.PHONY: tf-plan
tf-plan: ## terraform plan for $(ENV).
	cd $(TF_DIR) && terraform plan -var-file="envs/$(ENV).tfvars"

.PHONY: tf-apply
tf-apply: ## terraform apply for $(ENV).
	cd $(TF_DIR) && terraform apply -var-file="envs/$(ENV).tfvars"

.PHONY: tf-fmt
tf-fmt: ## terraform fmt -recursive.
	cd $(TF_DIR) && terraform fmt -recursive

.PHONY: clean
clean: ## Remove caches and build artifacts.
	rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage htmlcov dist build
	find . -type d -name __pycache__ -exec rm -rf {} +
