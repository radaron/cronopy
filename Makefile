.DEFAULT_GOAL := help
PART ?= patch

.PHONY: help format check-format lint test check bump build

help:
	@echo "format        - ruff format + autofix imports/lint"
	@echo "check-format  - ruff format --check (no changes)"
	@echo "lint          - ruff check + ty check"
	@echo "test          - pytest"
	@echo "check         - check-format + lint + test"
	@echo "build         - build sdist + wheel into dist/"
	@echo "bump          - bump version (PART=patch|minor|major, default patch)"

format:
	uv run ruff format .
	uv run ruff check --fix .

check-format:
	uv run ruff format --check .

lint:
	uv run ruff check .
	uv run ty check

test:
	uv run pytest

check: check-format lint test

build:
	rm -rf dist
	uv build

bump:
	uv version --bump $(PART)
