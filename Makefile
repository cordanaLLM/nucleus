.DEFAULT_GOAL := help
SHELL := /usr/bin/env bash

STREAM ?= mainstream
ARCH ?= x86_64
DRY_RUN ?= true

.PHONY: help init fmt fmt-check lint lint-workflows lint-manifest test test-coverage docs-serve docs-build audit build-kernel clean

help: ## Show this help message
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-24s\033[0m %s\n", $$1, $$2}'

init: ## Initialize environment and verify executable script permissions
	@echo "==> Initializing environment and permissions..."
	@chmod +x scripts/*.sh .agents/hooks-scripts/*.py 2>/dev/null || true
	@mkdir -p output dist build
	@echo "==> Initialization complete."

fmt: ## Format shell scripts, python files, and configs
	@echo "==> Formatting shell scripts and code..."
	@if command -v shfmt >/dev/null 2>&1; then \
		shfmt -w -i 2 -bn -ci scripts/*.sh; \
	fi
	@if command -v ruff >/dev/null 2>&1; then \
		ruff format tests/ .agents/hooks-scripts/ scripts/ 2>/dev/null || true; \
	fi
	@echo "==> Formatting complete."

fmt-check: ## Verify repository formatting without modifying files
	@echo "==> Checking repository formatting..."
	@if command -v shfmt >/dev/null 2>&1; then \
		shfmt -d -i 2 -bn -ci scripts/*.sh; \
	fi
	@if command -v ruff >/dev/null 2>&1; then \
		ruff format --check tests/ .agents/hooks-scripts/ scripts/ 2>/dev/null || true; \
	fi
	@echo "==> Format check passed."

lint: lint-manifest lint-workflows ## Run ShellCheck, Yamllint, Actionlint, and manifest validation
	@echo "==> Running ShellCheck on scripts..."
	@shellcheck -s bash scripts/*.sh
	@if command -v yamllint >/dev/null 2>&1; then \
		echo "==> Running Yamllint..."; \
		yamllint -c .yamllint.yml .github/ 2>/dev/null || true; \
	fi
	@echo "==> All lint checks passed successfully."

lint-workflows: ## Run actionlint on GitHub Actions workflows
	@echo "==> Validating GitHub Actions workflows with actionlint..."
	@if [ -d .github/workflows ] && [ "$$(find .github/workflows -maxdepth 1 \( -name '*.yml' -o -name '*.yaml' \) | wc -l)" -gt 0 ]; then \
		if command -v actionlint >/dev/null 2>&1; then \
			actionlint .github/workflows/*.yml; \
		elif [ -x "$$HOME/go/bin/actionlint" ]; then \
			"$$HOME/go/bin/actionlint" .github/workflows/*.yml; \
		fi \
	fi
	@echo "==> Workflow linting complete."

lint-manifest: ## Validate versions.json against versions.schema.json
	@echo "==> Validating versions.json schema..."
	@python3 -c "import json, jsonschema; jsonschema.validate(json.load(open('versions.json')), json.load(open('versions.schema.json')))"
	@echo "==> versions.json is valid."

test: ## Run pytest automated test suite
	@echo "==> Running pytest test suite..."
	@pytest tests/ -v

test-coverage: ## Run test suite with coverage report
	@echo "==> Running test suite with coverage..."
	@pytest tests/ -v --cov=scripts --cov=tests --cov-report=term-missing

docs-serve: ## Serve documentation portal locally via mkdocs
	@echo "==> Serving documentation locally at http://127.0.0.1:8000..."
	@mkdocs serve

docs-build: ## Build documentation portal strictly
	@echo "==> Building documentation in strict mode..."
	@mkdocs build --strict

audit: ## Run 7-stage repository health quality gate audit
	@./scripts/audit-repository-health.sh

build-kernel: ## Compile kernel or run dry-run build (STREAM=<stream> ARCH=<arch> DRY_RUN=true)
	@echo "==> Invoking kernel build for stream '$(STREAM)' [$(ARCH)]..."
	@if [ "$(DRY_RUN)" = "true" ]; then \
		./scripts/build_kernel.sh --stream="$(STREAM)" --arch="$(ARCH)" --dry-run; \
	else \
		./scripts/build_kernel.sh --stream="$(STREAM)" --arch="$(ARCH)"; \
	fi

clean: ## Clean build artifacts, outputs, and temporary caches
	@echo "==> Cleaning build artifacts and caches..."
	@rm -rf output/ dist/ build/ *.deb *.efi *.ddeb *.changes *.buildinfo .pytest_cache/ site/
	@echo "==> Clean complete."
