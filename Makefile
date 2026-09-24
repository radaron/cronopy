.PHONY: test

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

bump:
	uv version --bump $(filter-out $@,$(MAKECMDGOALS))
