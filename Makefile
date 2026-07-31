.PHONY: test cov audit lint typecheck srtm bandit opengrep ci

# Run the test suite (coverage is reported via pyproject addopts).
test:
	uv run pytest

# Lint with ruff (rule set configured in pyproject [tool.ruff.lint]).
lint:
	uv run ruff check src2sink/ tests/

# Full coverage run with an HTML report in htmlcov/.
cov:
	uv run pytest --cov-report=html --cov-report=term-missing

# Dependency vulnerability audit (TA-011 / SC-1); a CI gate in ci.yml.
# --frozen: audit the lockfile as committed. Without it uv re-resolves first and
# rewrites every package URL to whatever index UV_INDEX / UV_DEFAULT_INDEX points
# at, which on a machine configured for an internal mirror silently replaces all
# the pypi.org URLs (hashes are preserved, so it is easy to miss in review).
audit:
	uv run --frozen pip-audit

# Strict type check (targets + settings in pyproject [tool.mypy]).
typecheck:
	uv run mypy

# SRTM traceability: every requirement in the gap analysis §8 still has evidence.
srtm:
	uv run python scripts/srtm_check.py

# Python SAST over first-party code (tests/ excluded: its fixtures are payloads).
bandit:
	uv run bandit -r src2sink scripts

# Pattern SAST. Needs the opengrep CLI plus a local checkout of the ruleset:
#   git clone https://github.com/opengrep/opengrep-rules ~/opengrep-rules
#   make opengrep OPENGREP_RULES=~/opengrep-rules
# CI pins both the binary and the ruleset commit (see .github/workflows/ci.yml).
OPENGREP_RULES ?= ../opengrep-rules
opengrep:
	opengrep scan --config $(OPENGREP_RULES)/python --severity ERROR --error \
		--timeout 60 src2sink scripts

# Everything CI gates on, minus opengrep (which needs the external ruleset).
ci: lint typecheck test srtm bandit audit
