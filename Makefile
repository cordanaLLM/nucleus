.DEFAULT_GOAL := help
SHELL := /usr/bin/env bash

STREAM ?= mainstream
ARCH ?= x86_64
DRY_RUN ?= true
# package-uki with DRY_RUN=false: the kernel image to wrap, and an optional initramfs.
VMLINUZ ?=
INITRD ?=
# build-kernel and package-deb with DRY_RUN=false: a tree fetch-kernel-source.sh verified
# (build-kernel fetches one when it is empty; package-deb refuses without it).
SOURCE_TREE ?=
# boot-smoke: the kernel image to boot and the release it must report.
KERNEL ?=
KERNELRELEASE ?=

# praetorctl built from the commit PRAETOR_COMMIT in .github/workflows/ci.yml names, and a
# praetor checkout at that commit for the catalog check (docs/repository-governance.md).
PRAETORCTL ?= praetorctl
PRAETOR_SRC ?=

.PHONY: help init fmt fmt-check lint lint-workflows lint-manifest lint-pins test test-coverage test-boot boot-smoke docs-serve docs-build audit build-kernel merge-config fetch-source resolve-config package-deb package-uki verify-reproducibility docker-builder clean context ruleset governance-check

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
		yamllint -c .yamllint.yml .github/; \
	else \
		echo "==> SKIP: yamllint not installed; YAML lint did not run."; \
	fi
	@echo "==> All lint checks passed successfully."

lint-workflows: ## Run actionlint on GitHub Actions workflows
	@echo "==> Validating GitHub Actions workflows with actionlint..."
	@if [ -d .github/workflows ] && [ "$$(find .github/workflows -maxdepth 1 \( -name '*.yml' -o -name '*.yaml' \) | wc -l)" -gt 0 ]; then \
		if command -v actionlint >/dev/null 2>&1; then \
			actionlint .github/workflows/*.yml; \
		elif [ -x "$$HOME/go/bin/actionlint" ]; then \
			"$$HOME/go/bin/actionlint" .github/workflows/*.yml; \
		else \
			echo "==> SKIP: actionlint not installed; workflow lint did not run."; \
		fi \
	fi
	@echo "==> Workflow linting complete."

lint-manifest: ## Validate versions.json against versions.schema.json
	@echo "==> Validating versions.json schema..."
	@python3 -c "import json, jsonschema; jsonschema.validate(json.load(open('versions.json')), json.load(open('versions.schema.json')))"
	@echo "==> versions.json is valid."

lint-pins: ## Verify every SHA-pinned action against its upstream tag (network; needs an authenticated gh)
	@./scripts/check-action-pins.sh

test: ## Run pytest automated test suite
	@echo "==> Running pytest test suite..."
	@pytest tests/ -v

test-coverage: ## Run test suite with coverage report
	@echo "==> Running test suite with coverage..."
	@pytest tests/ -v --cov=scripts --cov=tests --cov-report=term-missing

test-boot: ## Run the hermetic tests of the boot smoke test (no QEMU needed)
	@pytest tests/test_qemu_boot.py -v

boot-smoke: ## Boot a built x86_64 kernel to userspace under QEMU (KERNEL=<vmlinuz> KERNELRELEASE=<release>)
	@python3 scripts/boot_smoke.py --kernel="$(KERNEL)" --kernelrelease="$(KERNELRELEASE)"

docs-serve: ## Serve documentation portal locally via mkdocs
	@echo "==> Serving documentation locally at http://127.0.0.1:8000..."
	@mkdocs serve

docs-build: ## Build documentation portal strictly
	@echo "==> Building documentation in strict mode..."
	@mkdocs build --strict

audit: ## Run 7-stage repository health quality gate audit
	@./scripts/audit-repository-health.sh

context: ## Recompile CLAUDE.md and the other agent context files from AGENTS.md
	@$(PRAETORCTL) compile-context
	@$(PRAETORCTL) compile-context --verify

ruleset: ## Re-render .github/rulesets/main.json from .standards.yaml and the workflows (local only)
	@had_labels=false; \
	if [ -f .config/labels.yaml ]; then had_labels=true; fi; \
	if [ -f .github/rulesets/main.json ]; then mv .github/rulesets/main.json .github/rulesets/main.json.bak; fi; \
	status=0; \
	$(PRAETORCTL) sync || status=$$?; \
	if [ "$$had_labels" = false ]; then rm -f .config/labels.yaml; fi; \
	if [ "$$status" -ne 0 ] && [ -f .github/rulesets/main.json.bak ]; then \
		mv .github/rulesets/main.json.bak .github/rulesets/main.json; \
	else \
		rm -f .github/rulesets/main.json.bak; \
	fi; \
	if [ "$$status" -eq 0 ] && [ -n "$$(tail -c 1 .github/rulesets/main.json)" ]; then \
		echo >> .github/rulesets/main.json; \
	fi; \
	exit "$$status"

governance-check: ## Run the praetor checks CI runs (PRAETOR_SRC=<praetor checkout at the pin> adds the catalog checks)
	@if [ -n "$(PRAETOR_SRC)" ]; then \
		pin=$$(sed -n 's/^  PRAETOR_COMMIT: "\([0-9a-f]\{40\}\)"$$/\1/p' .github/workflows/ci.yml); \
		head=$$(git -C "$(PRAETOR_SRC)" rev-parse HEAD); \
		if [ -z "$$pin" ] || [ "$$head" != "$$pin" ]; then \
			echo "==> ERROR: PRAETOR_SRC is at '$$head', PRAETOR_COMMIT is '$$pin'." >&2; \
			exit 1; \
		fi; \
		$(PRAETORCTL) plan --catalog-root "$(PRAETOR_SRC)"; \
	else \
		echo "==> SKIP: PRAETOR_SRC unset; the catalog check against the praetor pin did not run."; \
	fi
	@had_labels=false; \
	if [ -f .config/labels.yaml ]; then had_labels=true; fi; \
	status=0; \
	$(PRAETORCTL) sync $(if $(PRAETOR_SRC),--catalog-root "$(PRAETOR_SRC)") || status=$$?; \
	if [ "$$had_labels" = false ]; then rm -f .config/labels.yaml; fi; \
	exit "$$status"
	@$(PRAETORCTL) plan
	@$(PRAETORCTL) compile-context --verify

build-kernel: ## Compile a stream into gated Debian packages, or state the plan (STREAM=<stream> ARCH=<arch> DRY_RUN=true [SOURCE_TREE=<dir>])
	@echo "==> Invoking kernel build for stream '$(STREAM)' [$(ARCH)]..."
	@if [ "$(DRY_RUN)" = "true" ]; then \
		./scripts/build_kernel.sh --stream="$(STREAM)" --arch="$(ARCH)" --dry-run; \
	else \
		./scripts/build_kernel.sh --stream="$(STREAM)" --arch="$(ARCH)" $(if $(SOURCE_TREE),--source-tree="$(SOURCE_TREE)"); \
	fi

merge-config: ## Merge the security, architecture and stream kconfig fragments (ARCH=<arch> STREAM=<stream> DRY_RUN=true)
	@echo "==> Merging KConfig fragments for '$(ARCH)', stream '$(STREAM)'..."
	@if [ "$(DRY_RUN)" = "true" ]; then \
		./scripts/merge-config.sh --arch="$(ARCH)" --stream="$(STREAM)" --dry-run; \
	else \
		./scripts/merge-config.sh --arch="$(ARCH)" --stream="$(STREAM)"; \
	fi

fetch-source: ## Fetch and verify a stream's signed kernel source (STREAM=<stream> DEST=<dir, default build/linux-<stream>>)
	@./scripts/fetch-kernel-source.sh --stream="$(STREAM)" --dest="$(or $(DEST),build/linux-$(STREAM))"

resolve-config: ## Resolve the kconfig against a verified tree and check survival (STREAM=<stream> ARCH=<arch> SOURCE_TREE=<dir>)
	@./scripts/merge-config.sh --arch="$(ARCH)" --stream="$(STREAM)" --source-tree="$(or $(SOURCE_TREE),build/linux-$(STREAM))"

package-deb: ## Build Debian packages from a verified tree (STREAM=<stream> ARCH=<arch> DRY_RUN=true; DRY_RUN=false needs SOURCE_TREE=<dir>)
	@echo "==> Packaging Debian packages for stream '$(STREAM)' [$(ARCH)]..."
	@if [ "$(DRY_RUN)" = "true" ]; then \
		./scripts/package-deb.sh --stream="$(STREAM)" --arch="$(ARCH)" --dry-run; \
	else \
		./scripts/package-deb.sh --stream="$(STREAM)" --arch="$(ARCH)" $(if $(SOURCE_TREE),--source-tree="$(SOURCE_TREE)"); \
	fi

package-uki: ## Synthesize Unified Kernel Image (STREAM=<stream> ARCH=<arch> DRY_RUN=true; DRY_RUN=false needs VMLINUZ=<path>, optional INITRD=<path>)
	@echo "==> Synthesizing UKI for stream '$(STREAM)' [$(ARCH)]..."
	@if [ "$(DRY_RUN)" = "true" ]; then \
		./scripts/package-uki.sh --stream="$(STREAM)" --arch="$(ARCH)" --dry-run; \
	else \
		./scripts/package-uki.sh --stream="$(STREAM)" --arch="$(ARCH)" --vmlinuz="$(VMLINUZ)" $(if $(INITRD),--initrd="$(INITRD)"); \
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
