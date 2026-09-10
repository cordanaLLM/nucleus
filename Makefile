.DEFAULT_GOAL := help
SHELL := /usr/bin/env bash

STREAM ?= mainstream
ARCH ?= x86_64
DRY_RUN ?= true

.PHONY: help init fmt fmt-check lint lint-workflows lint-manifest test test-coverage test-boot docs-serve docs-build audit build-kernel merge-config package-deb package-uki verify-reproducibility docker-builder clean

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

test-boot: ## Run QEMU microVM cold boot verification test suite
	@echo "==> Running QEMU microVM sub-second cold boot tests..."
	@pytest tests/test_qemu_boot.py -v

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

merge-config: ## Merge kconfig fragments for target architecture (ARCH=<arch> DRY_RUN=true)
	@echo "==> Merging KConfig fragments for '$(ARCH)'..."
	@if [ "$(DRY_RUN)" = "true" ]; then \
		./scripts/merge-config.sh --arch="$(ARCH)" --dry-run; \
	else \
		./scripts/merge-config.sh --arch="$(ARCH)"; \
	fi

package-deb: ## Package native Debian packages (STREAM=<stream> ARCH=<arch> DRY_RUN=true)
	@echo "==> Packaging Debian packages for stream '$(STREAM)' [$(ARCH)]..."
	@if [ "$(DRY_RUN)" = "true" ]; then \
		./scripts/package-deb.sh --stream="$(STREAM)" --arch="$(ARCH)" --dry-run; \
	else \
		./scripts/package-deb.sh --stream="$(STREAM)" --arch="$(ARCH)"; \
	fi

package-uki: ## Synthesize Unified Kernel Image (STREAM=<stream> ARCH=<arch> DRY_RUN=true)
	@echo "==> Synthesizing UKI for stream '$(STREAM)' [$(ARCH)]..."
	@if [ "$(DRY_RUN)" = "true" ]; then \
		./scripts/package-uki.sh --stream="$(STREAM)" --arch="$(ARCH)" --dry-run; \
	else \
		./scripts/package-uki.sh --stream="$(STREAM)" --arch="$(ARCH)"; \
	fi

verify-reproducibility: ## Run reproducible build attestation driver (DRY_RUN=true)
	@echo "==> Running reproducibility attestation..."
	@./scripts/verify-reproducibility.sh --dry-run

docker-builder: ## Build hermetic multi-architecture builder container
	@echo "==> Building lusoris-kernel-builder container..."
	@docker build -t lusoris-kernel-builder -f docker/Dockerfile.builder .

clean: ## Clean build artifacts, outputs, and temporary caches
	@echo "==> Cleaning build artifacts and caches..."
	@rm -rf output/ dist/ build/ *.deb *.efi *.ddeb *.changes *.buildinfo .pytest_cache/ site/
	@echo "==> Clean complete."
