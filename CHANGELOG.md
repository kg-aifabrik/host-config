# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Initial repository scaffolding: project metadata, code-quality tooling (`ruff`, `mypy --strict`, `pytest`/`pytest-cov`), pre-commit hooks, `justfile`, `.env.example`, `.editorconfig`. (M0-1, #1)
- `CODE_CONVENTIONS.md`: authoritative rulebook for file organization, function conventions, docstring style (Google + Approach + Scenarios), inline comment tag taxonomy, naming, error handling, testing, and observability. (M0-2, #2)
- `docs/` directory structure: `index.md` entry point, `architecture/` skeleton, `adr/` with Nygard-format template and README index, `runbooks/` skeleton, `diagrams/` README documenting the Excalidraw + SVG convention with shared color palette. (M0-3, #3)
- Eleven seed ADRs (0001–0011) covering: Python 3.12, uv, FastAPI/Pydantic/Jinja with `/v1/` API versioning policy, ruff/mypy quality gates, pytest/Hypothesis testing, Ansible for IaC + config, just task runner, GitHub-rendered Markdown for docs, structlog+Prometheus observability, GitHub Actions for CI, systems overview. (M0-4, #4)
- `docs/diagrams/systems-overview.svg` and `docs/diagrams/render-flow.svg` — hand-authored SVG component + sequence diagrams; mirrored in `docs/architecture/systems-overview.md` as living docs. (M0-4)
- `.github/workflows/ci.yml` (lint + type-check + unit/component tests + coverage) and placeholder `.github/workflows/e2e.yml`. (M0-5, #5)
- `.github/dependabot.yml` configured for pip/GHA/Docker weekly updates. (M0-5)
- Issue templates (`layer-task`, `gate-test`, `bug`, `design-discussion`), `PULL_REQUEST_TEMPLATE.md`, `CODEOWNERS`. (M0-5)

- HostIntent Pydantic models + error hierarchy. `src/host_config/models/` exposes `PhysIface`, `BondMember`, `Bond`, `SriovParent`, `RoceUnderlay`, `VlanChild`, `VlanRole`, `HostIntent`, `Role`, plus the `MacAddress` validated type. `src/host_config/errors.py` and `src/host_config/models/errors.py` establish the typed exception hierarchy (`HostConfigError` → `ModelError` → `InvariantError`). Ten cross-field invariants enforce host-level rules (one default gateway, MTU monotonicity, RoCE count per role, etc.). 89 unit tests, 98% line + branch coverage. (M2-1, #11)
- Ansible role `netbox-dev` brings up the upstream `netbox-community/netbox-docker` Compose stack on the local host. Idempotent end-to-end (verified: changed=0 on subsequent runs). Mints a v1 API token persisted to `~/.host-config/netbox-token`. CI runs `ansible-lint` (production profile). (M1-1, #6)
- Netbox custom-field schema: `src/host_config/netbox/schema.py` declares the seven custom fields the host model depends on (bf3_mode, roce_tc, numa_node, sriov_vfs, gpu_affinity, observed_mac, observed_firmware) as immutable `CustomFieldSpec` dataclasses. `apply_schema` is idempotent and distinguishes recoverable from unrecoverable drift. Typed `NetboxError` hierarchy (NetboxQueryError, HostNotFoundError, SchemaError) with contextual fields. (M1-2, #7)

### Deferred

- **Branch protection** on `main`. GitHub's branch protection API is gated behind paid plans for private repos; until the repo is on a paid plan (or goes public), the rules are enforced by local discipline (CONTRIBUTING.md). M7.5-1 revisits when the constraint changes. (M0-5)

### Changed

- `CONTRIBUTING.md` now reflects the solo-dev direct-to-main workflow (no feature branches, no PRs, no commit signing). PR template kept for future use. (M0-2)
