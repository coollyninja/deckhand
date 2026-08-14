.PHONY: check test lint format run

check: lint test

test:
	uv run pytest

lint:
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy apps/broker/src

format:
	uv run ruff check --fix .
	uv run ruff format .

run:
	uv run deckhand-api

