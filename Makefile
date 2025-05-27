.PHONY: clean install-dev lint type-check unit-test format

DIRS_WITH_CODE = src/ tests/

clean:
	rm -rf .mypy_cache .pytest_cache .ruff_cache build dist htmlcov .coverage

install-dev:
	uv sync --all-extras

lint:
	uv run ruff check $(DIRS_WITH_CODE)

type-check:
	uv run mypy $(DIRS_WITH_CODE)

format:
	uv run ruff format $(DIRS_WITH_CODE)

unit-test:
	uv run pytest tests/
