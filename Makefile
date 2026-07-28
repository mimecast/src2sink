.PHONY: test cov audit lint

# Run the test suite (coverage is reported via pyproject addopts).
test:
	uv run pytest

# Lint with ruff (rule set configured in pyproject [tool.ruff.lint]).
lint:
	uv run ruff check src2sink/ tests/

# Full coverage run with an HTML report in htmlcov/.
cov:
	uv run pytest --cov-report=html --cov-report=term-missing

# Dependency vulnerability audit. CI should run this (see docs/operations-security.md).
audit:
	uv run pip-audit
