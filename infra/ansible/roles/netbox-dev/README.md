# netbox-dev

Brings up a development Netbox instance via the upstream
[`netbox-community/netbox-docker`](https://github.com/netbox-community/netbox-docker)
Compose stack. Used by Tier 1 lab fixtures and the renderer for local
integration.

## Inputs (variables)

| Variable | Default | Purpose |
|---|---|---|
| `netbox_dev_version` | `release-v4.2.2` | Upstream `netbox-docker` git tag to check out. |
| `netbox_dev_workdir` | `~/.host-config/netbox-docker-<version>` | Where to clone the upstream repo. |
| `netbox_dev_http_port` | `8000` | Host port to expose Netbox on. |
| `netbox_dev_superuser_name` | `admin` | First-boot superuser. Skipped if already exists. |
| `netbox_dev_superuser_email` | `admin@example.com` | First-boot superuser email. |
| `netbox_dev_superuser_password` | `admin-dev-only` | Dev-only password. Override via vault for non-local use. |
| `netbox_dev_token_file` | `~/.host-config/netbox-token` | Where to persist the API token (gitignored; mode 0600). |
| `netbox_dev_ready_timeout_seconds` | `600` | Budget for first boot (migrations + image pulls). |
| `netbox_dev_pull_images` | `true` | Disable for offline / restricted-network use. |

## Outputs

- Netbox HTTP endpoint at `http://127.0.0.1:<netbox_dev_http_port>`.
- API token written to `netbox_dev_token_file` (mode 0600). Consumers
  read this file at runtime to authenticate against Netbox.

## Prerequisites

- Docker (Docker Desktop, Rancher Desktop, Colima, or native).
- Docker Compose v2 plugin (`docker compose` not `docker-compose`).
- `git` and HTTPS access to GitHub (to clone the upstream repo).
- Sufficient disk: ~3 GB for images + database.

## Usage

```bash
ansible-playbook -i localhost, -c local playbooks/netbox-dev.yml
```

A minimal playbook:

```yaml
- hosts: localhost
  connection: local
  roles:
    - role: netbox-dev
```

## Idempotency

Re-running the role with the stack already healthy is a no-op:

- `docker compose up -d` is naturally idempotent.
- The superuser-create task is gated on a shell check; skipped if the
  user exists.
- The API token uses `get_or_create`, so the same token is returned.

## Logs

Every task logs at Ansible's default verbosity. Pass `-v` for more
detail. The `Print summary` task at the end emits the connection
information for downstream consumers.

## Notes

- The role is non-destructive: it clones a fresh checkout of
  `netbox-docker` at the pinned tag into a versioned subdirectory.
  Bumping `netbox_dev_version` clones a sibling checkout, leaving
  the previous one in place (in case rollback is desired).
- The dev superuser password is intentionally weak; this role is for
  local development only. Production Netbox is managed separately.
