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

# Lint Ansible roles + playbooks (must run from infra/ansible/ so the
# local ansible.cfg + roles_path resolve correctly).
lint-ansible:
    cd infra/ansible && uv run ansible-lint

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
# Lab — provision → configure → test → destroy on DigitalOcean.
#
# Requires DIGITALOCEAN_TOKEN and SSH_KEY_FINGERPRINT in .env.
# See docs/runbooks/deploy-do.md for full usage.
# ---------------------------------------------------------------------------

# _lab_ip: read the Droplet IP from the dynamic inventory file.
# Used by lab-test and lab-logs to target the correct host.
_lab_ip := `grep -oP '\d+\.\d+\.\d+\.\d+' infra/ansible/inventory/lab 2>/dev/null | head -1 || echo ""`

# Provision + configure the DO lab.
# Step 1: create Droplet + write inventory/lab.
# Step 2: deploy the full stack (Docker, Netbox, renderer, nginx, OVS, QEMU).
lab-up:
    #!/usr/bin/env bash
    set -euo pipefail
    cd infra/ansible
    ansible-galaxy collection install -r requirements.yml --force-with-deps
    ansible-playbook -i localhost, playbooks/provision.yml
    ansible-playbook -i inventory/lab playbooks/deploy-lab.yml

# Tear down the DO lab; verify zero residual resources via the DO API.
lab-down:
    #!/usr/bin/env bash
    set -euo pipefail
    cd infra/ansible
    ansible-playbook -i localhost, playbooks/destroy.yml

# Run e2e tests on the Droplet over SSH.
# The tests run on the Droplet itself so they have access to /dev/kvm,
# the OVS bridge, and the local lab services.
lab-test:
    #!/usr/bin/env bash
    set -euo pipefail
    LAB_IP="{{ _lab_ip }}"
    if [[ -z "$LAB_IP" ]]; then
        echo "No lab inventory found — run 'just lab-up' first." >&2
        exit 1
    fi
    # Sync the test suite to the Droplet (renderer role syncs src/ + fixtures/;
    # we additionally need tests/ to run pytest on the Droplet).
    rsync -az --exclude=__pycache__ --exclude="*.pyc" \
        tests/ root@"${LAB_IP}":/opt/host-config/tests/
    rsync -az conftest.py root@"${LAB_IP}":/opt/host-config/conftest.py 2>/dev/null || true
    # Run e2e tests on the Droplet.
    ssh root@"${LAB_IP}" \
        "cd /opt/host-config && \
         PYTHONPATH=/opt/host-config/src:/opt/host-config \
         .venv/bin/pytest tests/e2e/ -v --no-header"

# Compose: provision → test → teardown.
# trap ensures lab-down runs even on test failure or Ctrl-C (principle #11).
lab:
    #!/usr/bin/env bash
    set -euo pipefail
    trap 'just lab-down' EXIT INT TERM
    just lab-up
    just lab-test

# Collect renderer / nginx / OVS / cloud-init logs from the Droplet.
lab-logs:
    #!/usr/bin/env bash
    set -euo pipefail
    LAB_IP="{{ _lab_ip }}"
    if [[ -z "$LAB_IP" ]]; then
        echo "No lab inventory found — run 'just lab-up' first." >&2
        exit 1
    fi
    echo "=== renderer (last 100 lines) ==="
    ssh root@"${LAB_IP}" "journalctl -u host-config-renderer -n 100 --no-pager"
    echo "=== nginx access log (last 50 lines) ==="
    ssh root@"${LAB_IP}" "tail -n 50 /var/log/nginx/host-config-access.log 2>/dev/null || echo '(no log yet)'"
    echo "=== OVS bridge state ==="
    ssh root@"${LAB_IP}" "ovs-vsctl show"
    echo "=== CPU cloud-init serial log ==="
    ssh root@"${LAB_IP}" "cat /tmp/cpu-boot.log 2>/dev/null || echo '(not found)'"
    echo "=== B300 cloud-init serial log ==="
    ssh root@"${LAB_IP}" "cat /tmp/b300-boot.log 2>/dev/null || echo '(not found)'"

# ---------------------------------------------------------------------------
# Hygiene.
# ---------------------------------------------------------------------------

# Remove caches and generated artifacts (no Droplet teardown — use lab-down).
clean:
    rm -rf .pytest_cache .mypy_cache .ruff_cache htmlcov .coverage coverage.xml
    find . -type d -name __pycache__ -exec rm -rf {} +
