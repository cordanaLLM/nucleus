.DEFAULT_GOAL := all
SHELL := /bin/bash

.PHONY: all
all: lint test

.PHONY: lint
lint:
	@echo "==> Running ShellCheck on scripts..."
	@shellcheck -s bash scripts/*.sh
	@echo "==> Running Yamllint..."
	@yamllint -c .yamllint.yml .github/workflows/ || true
	@echo "==> Validating versions.json..."
	@python3 -c "import json, jsonschema; jsonschema.validate(json.load(open('versions.json')), json.load(open('versions.schema.json'))); print('versions.json is valid')"
	@echo "==> Linting complete."

.PHONY: test
test:
	@echo "==> Running pytest test suite..."
	@pytest tests/ -v

.PHONY: clean
clean:
	@echo "==> Cleaning build artifacts..."
	@rm -rf output/ dist/ build/ *.deb *.ddeb *.changes *.buildinfo .pytest_cache/
	@echo "==> Clean complete."
