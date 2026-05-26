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
# Used by lab-image, lab-test, lab-refresh, and lab-logs to target the host.
# NOTE: uses `grep -oE` (POSIX extended regex), NOT `-oP` (Perl) — BSD grep
# on macOS has no -P, which would silently yield an empty IP and break every
# recipe that targets the Droplet.
_lab_ip := `grep -oE '[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+' infra/ansible/inventory/lab 2>/dev/null | head -1 || echo ""`

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

# Prepare the e2e cloud image on the Droplet.
#
# WHY this is its own step (and must run before lab-test): prepare_image
# --prepare injects the e2e SSH public key (tests/e2e/fixtures/
# test_vm_key.pub) into the base image so the tests can SSH into the VMs.
# That key lives under tests/, which the deploy-lab playbook does NOT sync
# (the renderer role syncs only src/ + fixtures/). So we must rsync tests/
# to the Droplet BEFORE running prepare_image, or the image is baked
# without the key and every e2e test fails to connect. This recipe does
# both in the right order.
lab-image:
    #!/usr/bin/env bash
    set -euo pipefail
    LAB_IP="{{ _lab_ip }}"
    if [[ -z "$LAB_IP" ]]; then
        echo "No lab inventory found — run 'just lab-up' first." >&2
        exit 1
    fi
    echo "→ syncing tests/ (carries the e2e SSH key) + fixtures/ to ${LAB_IP}…"
    rsync -az --exclude=__pycache__ --exclude="*.pyc" \
        tests/ root@"${LAB_IP}":/opt/host-config/tests/
    rsync -az --exclude=__pycache__ --exclude="*.pyc" --exclude=images \
        fixtures/ root@"${LAB_IP}":/opt/host-config/fixtures/
    echo "→ preparing cloud image (downloads ~600 MB, injects SSH key)…"
    ssh root@"${LAB_IP}" \
        "cd /opt/host-config && uv run python -m fixtures.vms.prepare_image --prepare"
    echo "✓ e2e image ready."

# Run e2e tests on the Droplet over SSH.
# The tests run on the Droplet itself so they have access to /dev/kvm,
# the OVS bridge, and the local lab services.
#
# Guards that the prepared image exists (prepare_image runs in lab-image);
# if it's missing, points the user at `just lab-image` rather than failing
# deep inside pytest with a skip.
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
    # Guard: the prepared image must exist before pytest launches VMs.
    if ! ssh root@"${LAB_IP}" \
            "test -f /opt/host-config/fixtures/vms/images/ubuntu-noble-base.img"; then
        echo "e2e image not found on the Droplet — run 'just lab-image' first." >&2
        exit 1
    fi
    # Run e2e tests on the Droplet.
    ssh root@"${LAB_IP}" \
        "cd /opt/host-config && \
         PYTHONPATH=/opt/host-config/src:/opt/host-config \
         .venv/bin/pytest tests/e2e/ -v --no-header"

# Sync local code + templates to a live Droplet, restart the renderer,
# flush the nginx-cache, and wait for /healthz to come back. Use this
# during dev iteration when you've edited a Jinja template, a renderer
# Python file, or a Netbox fixture and want the next `just lab-test` to
# pick up the change without a full re-up.
#
# WHY the cache flush: nginx-cache caches rendered seeds for ~5 min.
# Without `rm -rf /var/cache/host-config/seeds/*` the lab VMs will boot
# from the stale rendered bytes and you'll lose ~15 min wondering why
# your template change didn't take effect.
lab-refresh:
    #!/usr/bin/env bash
    set -euo pipefail
    LAB_IP="{{ _lab_ip }}"
    if [[ -z "$LAB_IP" ]]; then
        echo "No lab inventory found — run 'just lab-up' first." >&2
        exit 1
    fi
    echo "→ rsyncing src/, fixtures/, templates/ to ${LAB_IP}…"
    rsync -az --exclude=__pycache__ --exclude="*.pyc" \
        src/ fixtures/ root@"${LAB_IP}":/opt/host-config/{src,fixtures}/ 2>/dev/null \
      || rsync -az --exclude=__pycache__ --exclude="*.pyc" \
            src/ root@"${LAB_IP}":/opt/host-config/src/ \
      && rsync -az --exclude=__pycache__ --exclude="*.pyc" \
            fixtures/ root@"${LAB_IP}":/opt/host-config/fixtures/
    echo "→ restarting host-config-renderer…"
    ssh root@"${LAB_IP}" "systemctl restart host-config-renderer"
    echo "→ flushing nginx-cache…"
    ssh root@"${LAB_IP}" "rm -rf /var/cache/host-config/seeds/* && systemctl reload nginx"
    echo "→ waiting for /healthz to return 200…"
    ssh root@"${LAB_IP}" \
        "for i in \$(seq 1 30); do \
            curl -sf http://127.0.0.1:8080/healthz > /dev/null && exit 0; \
            sleep 1; \
         done; \
         echo 'renderer /healthz did not come back up in 30s' >&2; exit 1"
    echo "✓ lab refreshed."

# Compose: provision → prepare image → test → teardown.
# trap ensures lab-down runs even on test failure or Ctrl-C (principle #11).
lab:
    #!/usr/bin/env bash
    set -euo pipefail
    trap 'just lab-down' EXIT INT TERM
    just lab-up
    just lab-image
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
