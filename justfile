# justfile — single source of truth for common operations.
#
# Principle #9 ("one way to do common things"): every operation has exactly
# one entry point here. Multiple competing ways would be technical debt.
#
# All recipes auto-load .env (if present) so secrets are available.

set dotenv-load := true
set shell := ["bash", "-uc"]

# Default: list available targets.
default:
    @just --list

# ---------------------------------------------------------------------------
# Code quality.
# ---------------------------------------------------------------------------

# Lint check (read-only).
lint:
    uv run ruff check .
    uv run ruff format --check .

# Auto-fix lint issues and format in place.
format:
    uv run ruff check --fix .
    uv run ruff format .

# Type check.
typecheck:
    uv run mypy

# ---------------------------------------------------------------------------
# Tests.
# ---------------------------------------------------------------------------

# Fast unit tests only.
test-fast:
    uv run pytest -m fast

# Full test suite (unit + component + integration).
test:
    uv run pytest

# Run e2e tests against the current lab (Lima or DO Droplet).
test-e2e:
    uv run pytest -m e2e

# ---------------------------------------------------------------------------
# Pre-commit hooks.
# ---------------------------------------------------------------------------

# Install pre-commit hooks (run once per clone).
hooks:
    uv run pre-commit install --install-hooks
    uv run pre-commit install --hook-type commit-msg

# Run all hooks against the entire codebase (CI-style).
hooks-all:
    uv run pre-commit run --all-files

# ---------------------------------------------------------------------------
# Lab (implemented in M6).
# ---------------------------------------------------------------------------

# Provision + configure the DO lab.
lab-up:
    @echo "TODO: implemented in M6-1 (provision.yml + deploy-lab.yml)"
    @exit 1

# Tear down the DO lab; verify zero residual resources.
lab-down:
    @echo "TODO: implemented in M6-3 (Ansible destroy + DO API verify)"
    @exit 1

# Run e2e tests against the live lab.
lab-test:
    @echo "TODO: implemented in M6-3"
    @exit 1

# Compose: up → test → down with trap-on-exit cleanup (principle #11).
lab:
    @echo "TODO: implemented in M6-3 (trap 'just lab-down' EXIT INT TERM)"
    @exit 1

# Collect logs from the live lab.
lab-logs:
    @echo "TODO: implemented in M6-3"
    @exit 1

# ---------------------------------------------------------------------------
# Hygiene.
# ---------------------------------------------------------------------------

# Remove caches and generated artifacts (no Droplet teardown — use lab-down).
clean:
    rm -rf .pytest_cache .mypy_cache .ruff_cache htmlcov .coverage coverage.xml
    find . -type d -name __pycache__ -exec rm -rf {} +
