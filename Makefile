.PHONY: test live

format:
	uv run ruff format .
	uv run ruff check --fix .

check-format:
	uv run ruff format --check .

lint:
	uv run ruff check .
	uv run ty check

test:
	uv run pytest -v

live:
	uv run pytest tests/live -m live -o addopts='' -v

bump:
	uv version --bump $(filter-out $@,$(MAKECMDGOALS))
