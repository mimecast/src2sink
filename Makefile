.PHONY: test cov audit lint typecheck srtm mutation complexity bandit opengrep ci

# Run the test suite (coverage is reported via pyproject addopts).
test:
	uv run pytest

# Lint with ruff (rule set configured in pyproject [tool.ruff.lint]).
# scripts/ is included deliberately: mypy already covers it, and leaving it out
# of lint let an unused import sit in a new gate script unnoticed.
lint:
	uv run ruff check src2sink/ tests/ scripts/

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

# Complexity ratchet: existing hot spots are frozen at their current scores;
# a new one, or an allowlisted one getting worse, fails the build.
complexity:
	uv run python scripts/complexity_check.py

# Mutation gate: every catalogued defect must still break a test. Coverage says
# the tests ran; this says they would notice. ~1s per mutant, so it stays in the
# `ci` chain — the expensive generated sweep (uvx mutmut) is a dev-time tool.
mutation:
	uv run python scripts/mutation_check.py

# Detector identity: changing an extractor without bumping DETECTION_VERSION
# means the fix never reaches a repo that has not itself changed, because the
# incremental scan would keep the old record (OI-16). Content-fingerprinted
# rather than git-diffed, so it behaves the same locally and on a depth-1
# checkout. Re-freeze after a deliberate bump:
#   uv run python scripts/detection_version_check.py --update
detection:
	uv run python scripts/detection_version_check.py

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
ci: lint typecheck test srtm mutation complexity detection bandit audit
