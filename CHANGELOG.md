# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Lab e2e bring-up on DigitalOcean — full suite green (23/23).** Brought the
  B300 RDMA e2e suite from 0/11 to 11/11 (and kept CPU at 12/12) on a real DO
  Droplet, then proved the whole path is hands-off from a cold start. Captured
  reference artifacts under `docs/artifacts/e2e-run-2026-05-26/` (rendered seed
  bytes for both host shapes, both serial boot logs, pytest output, droplet
  host info) so future regressions can be triaged by diffing against a
  known-good baseline.
- `docs/runbooks/debug-cloud-init.md`: diagnostic runbook for e2e VM
  boot/network/RDMA failures — failure-classification table, the
  dump-to-`/dev/ttyS0` trick for capturing inside-VM state after the fixture
  tears the VM down, bond0/SR-IOV/apt troubleshooting matrices, the load-bearing
  cold-start ordering, and the renderer + nginx-cache dev-iteration loop.
- `just lab-image`: syncs `tests/` (which carries the e2e SSH key) to the
  Droplet then runs `prepare_image --prepare`, in that order — closes the
  chicken-and-egg where `prepare_image` needs a key that `lab-up` never synced.
  `just lab` now sequences up → image → test → down.
- `just lab-refresh`: dev-iteration recipe — rsync `src/` + `fixtures/`, restart
  the renderer, flush the nginx-cache seeds, reload nginx, and poll `/healthz`.
  Eliminates the "edit template, re-test, silently get stale cached bytes"
  foot-gun.

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
- Netbox fixture loader: `fixtures/netbox/populate.py` is an idempotent CLI loading YAML fixtures (one B300 host + one CPU host) into Netbox. Handles Netbox 4.2+'s first-class MACAddress endpoint correctly. Typed `FixtureError` hierarchy with conflict detection. (M1-3, #8)
- Component test infrastructure: `tests/component/conftest.py` provides "live Netbox or skip" fixtures (NETBOX_URL env or default + ~/.host-config/netbox-token); auto-marks all component tests with `@slow` and `@requires_netbox`. (M1-4, #9)
- Integration gate test: `tests/integration/test_netbox_fixtures.py` proves the schema + fixtures pipeline round-trips correctly through Netbox — both CPU and B300 host shapes (10 NICs, MACs, IPs, VLANs, custom fields, LAG membership) re-emerge intact. The canonical "is Netbox set up for the renderer" smoke test. **M1 milestone closed.** (M1.5-1, #10)
- Netbox → `HostIntent` loader: `src/host_config/netbox/loader.py` exposes the pure function `load_host_intent(client, asset_tag) -> HostIntent`. Maps Netbox device + interfaces + IPs + VLAN assignments + custom fields to a fully validated intent. Centralizes the naming conventions (`bond0`, `nsa`/`nsb`, `gpu0..gpu7`, `bond0.<vid>`) and derives the mgmt-VLAN gateway as the first usable IP in its prefix. Typed errors with full context (`HostNotFoundError`, `NetboxQueryError`, `InvariantError`). 12 unit tests with mocked pynetbox + 3 component tests against live Netbox covering CPU and gpu-b300 fixtures. (M2-2, #12)
- Jinja template tree per host role: `src/host_config/render/templates/{cpu,gpu-b300}/{meta-data,user-data,network-config}.j2`. `src/host_config/render/environment.py` exposes `make_environment()` configured with `StrictUndefined`, `trim_blocks`/`lstrip_blocks`, and `keep_trailing_newline` (load-bearing for M2.5 byte-stable goldens). Templates carry inline `{# WHY: … #}` markers explaining non-obvious decisions (NoCloud instance-id, cloud-config marker, match-by-MAC + set-name, RoCE NICs not bonded, etc.). 19 unit tests parse rendered output as YAML, assert the structural keys (matched MACs, exactly-one default route, all 8 RoCE underlays with SR-IOV VF counts), and prove render-determinism. (M2-3, #13)
- Renderer emitter: `src/host_config/render/emitter.py` exposes `render_for(intent, file_kind, *, now=...) -> bytes`. Pure function that canonicalizes list-shaped intent fields (sorts NICs/VLANs/bond members by name) before rendering, guaranteeing byte-identical output regardless of upstream order. `FileKind` enum closes the door on typo'd file names. `now` is injectable for tests / future audit-trail use. Six golden files under `src/host_config/render/golden/{cpu,gpu-b300}/` are the regression net: any template or emitter change must update them deliberately and the diff IS the review. 15 unit tests cover byte-equality with goldens, determinism under list permutation, signature contract, Hypothesis-driven property check (rendered network-config parses as Netplan v2 YAML for any sampled intent), and StrictUndefined propagation through the emitter. (M2-4, #14)
- **Unit factory alignment:** `make_cpu_intent()` and `make_b300_intent()` in `tests/unit/models/test_intent.py` updated to exactly mirror the YAML fixtures (`cpu-host.yaml`, `b300-host.yaml`) — hostname FQDNs, per-host MACs, per-host IPs. Goldens regenerated to match. This ensures the unit-test goldens are byte-equal to what the live pipeline produces, making the M2.5 gate meaningful. (fix, no new issue)
- FastAPI service: `src/host_config/service/{app,routes,middleware,dependencies}.py` exposes the renderer over HTTP. Consumer routes under `/v1/`: `GET /v1/render/{asset_tag}/{meta-data,user-data,network-config}` return the cloud-init payload bytes as `text/plain`. Operational (unversioned) routes: `/healthz` (liveness), `/readyz` (Netbox reachability → 503 on outage), `/metrics` (Prometheus). `RequestContextMiddleware` mints a UUID per request (or honors caller-supplied `X-Request-Id`), binds it + method/path into structlog contextvars, and stamps the ID into the response header. Typed errors translate to a canonical JSON envelope `{error: {type, message, context}}`: `HostNotFoundError` → 404, `InvariantError` → 422, `NetboxQueryError` → 502. OpenAPI doc at `/docs` + `/openapi.json`. 18 unit tests with stubbed Netbox cover every route × happy path × representative error, request-ID handling, operational endpoints, and OpenAPI surface. (M2-5, #15)

- Ansible role `nginx-cache` (`infra/ansible/roles/nginx-cache/`): deploys an nginx caching reverse-proxy in front of the renderer. `proxy_cache_path` stores 200 responses on disk for 300 s (matching the renderer's `Cache-Control: max-age=300`), backed by a 1 GB quota with a 24 h inactive-eviction window. The rendered `nginx.conf.j2` template surfaces `X-Cache-Status` for HIT/MISS visibility, exposes a PURGE location at `/_purge/` for single-key invalidation without restart, and passes `/healthz`/`readyz`/`metrics` through uncached. `nginx -t` is run after the site symlink is created so config bugs are caught before the reload handler fires. `ansible-lint` (production profile) passes. (M3-1, #18)

- `.github/workflows/e2e.yml` implemented (M7-1): two parallel jobs `cpu-host-boot` (M4.5-1, 25 min timeout) and `b300-host-boot` (M5.5-1, 35 min timeout) on `ubuntu-24.04` runners with KVM. Each job installs system packages, starts Netbox via the `netbox-dev` role, starts the renderer as a background uvicorn process, configures nginx-cache, sets up OVS + tap interfaces, downloads (or restores from cache) the Ubuntu Noble cloud image, runs the E2E test, and always tears down on completion. Cloud image cached across runs by workflow file hash. (M7-1, #37)
- Coverage comment on PRs (M7-2): `ci.yml` extended with `py-cov-action/python-coverage-comment-action@v3` — posts coverage delta to PR comments; drop >2% triggers a warning annotation (does not block, per CODE_CONVENTIONS §6.7). Coverage XML archived as CI artifact for 30 days. (M7-2, #38)
- `.github/workflows/docs-links.yml` (M7-3): `lychee-action` scans all Markdown for broken relative links and image references; blocks merge on failure. External URL failures are warnings only. ADR index completeness check: every `docs/adr/*.md` (except README.md + template.md) must appear in `docs/adr/README.md`. `.lychee.toml` config pins timeouts + excludes login-gated URLs. (M7-3, #39)
- `CONTRIBUTING.md` updated (M7.5-1 + M7.5-2): branch protection rules documented (linear history, no force-push, all CI checks required — enforced by local discipline until paid plan). CI gate failure reference table maps each gate to its typical error message and remedy (failing test, coverage drop, bad commit message, broken docs link, ADR not in index, unsigned commit, force-push). **M7 + M7.5 milestones closed.** (M7.5-1 + M7.5-2, #40 + #41)

- DO E2E gate `tests/e2e/test_do_teardown.py` (M6.5-2): verifies principle #11 (leave no trace). `TestDOInventoryClean` asserts zero tagged resources at start and after `just lab-down`. `TestDOTrapOnFailure` asserts `lab-down` exits 0 and is idempotent (two consecutive calls). Both classes skip cleanly when `DIGITALOCEAN_TOKEN` or `doctl` are absent. `docs/runbooks/deploy-do.md` extended with "Canonical acceptance test" (M6.5-1 procedure and expected pass criteria) and "Teardown integrity" sections (automated + manual inventory checks, trap verification recipe). **M6.5 milestone closed.** (M6.5-1 + M6.5-2, #35 + #36)

- Ansible role `renderer` (`infra/ansible/roles/renderer/`): deploys the FastAPI renderer as a systemd unit (`host-config-renderer`). Installs the pinned `uv` binary, creates a `host-config` system user, rsyncs `src/` + `fixtures/` + `pyproject.toml` + `uv.lock` from the control node, runs `uv sync --frozen`, writes a `renderer.env` EnvironmentFile (token resolved from the `netbox-dev` token file or an explicit var), and validates start via `/healthz`. `ansible-lint` production profile passes. (M6-1, #32)
- Ansible playbook `infra/ansible/playbooks/provision.yml`: creates a DigitalOcean Droplet (`s-4vcpu-8gb-amd`, Ubuntu 24.04, tagged `host-config-lab`) via `community.digitalocean`, waits for SSH, writes `infra/ansible/inventory/lab`. Idempotent via `unique_name: true`. (M6-1, #32)
- Ansible playbook `infra/ansible/playbooks/deploy-lab.yml`: composes Docker install → `netbox-dev` → `renderer` → `nginx-cache` → `ovs-harness` → `qemu-host` → Netbox fixture population in the correct dependency order. Idempotent second run. (M6-1, #32)
- Ansible playbook `infra/ansible/playbooks/destroy.yml`: deletes the Droplet, removes the local inventory file, and verifies zero residual DO resources with the `host-config-lab` tag. (M6-1, #32)
- `infra/ansible/requirements.yml`: pins `community.digitalocean ≥ 1.27.0` and `ansible.posix ≥ 1.5.4`. (M6-1, #32)
- Runbook `docs/runbooks/deploy-do.md` (M6-2): step-by-step guide covering prerequisites, configuration, provisioning, deployment verification, smoke test, teardown, cost table (`s-4vcpu-8gb-amd` ≈ $0.04/run), and three troubleshooting scenarios (missing token, Docker clone rate-limit, E2E test fails on Droplet but passes on Lima). Linked from `docs/runbooks/README.md`. (M6-2, #33)
- `justfile` lab targets implemented (M6-3): `just lab-up` (provision + deploy), `just lab-down` (destroy + verify), `just lab-test` (rsync tests to Droplet + run e2e over SSH), `just lab-logs` (renderer/nginx/OVS/cloud-init logs), `just lab` (up → test → down with `trap 'just lab-down' EXIT INT TERM`). All targets source `.env` via `set dotenv-load`. **M6 milestone closed.** (M6-3, #34)

- E2E test `tests/e2e/test_b300_host_boot.py` (M5.5-1): full first-boot gate for the B300 host — the canonical "lab fully working" smoke test. Launches `SN-GPU-001` via the QEMU launcher (port-forward 2223→22, distinct from CPU test's 2222), waits up to 300 s for cloud-init, then asserts over SSH: `bond0` UP, `bond0.100`/`.200`/`.300` with correct IPs, all 8 `gpu0..gpu7` E-W NICs UP at MTU 9000 with per-NIC IPs, `rdma_rxe` kernel module loaded, `ibv_devinfo` lists 8 rxe devices, and `rping` between `rxe_gpu0` (server) and `rxe_gpu1` (client) succeeds — proving RDMA verbs work end-to-end through the Soft-RoCE substrate. **M5 milestone closed.** (M5.5-1, #31)
- Soft-RoCE user-data for gpu-b300 (M5-3): `gpu-b300/user-data.j2` extended with `write_files` (writes `/etc/security/limits.d/rdma.conf` with `memlock unlimited`) and `runcmd` (loads `rdma_rxe` kernel module, creates one `rxe_*` device per RoCE NIC with idempotency guard). Golden regenerated (1 500 bytes). 2 new unit tests assert `runcmd` entries count + per-NIC device names, and `write_files` memlock content. (M5-3, #30)
- Extended QEMU launcher for B300 10-NIC shape (M5-1): `build_cmdline` accepts optional `roce_nics: list[tuple[nic_name, mac, tap]]`; adds one virtio tap NIC per entry after the N-S NICs. `launch_host` populates this automatically from `intent.roce_underlays` (sorted deterministically). 5 new unit tests cover total NIC count (11), per-NIC MAC/tap correctness, CPU-shape backward compatibility, and B300 determinism. OVS harness role extended with `ovs_ew_tap_interfaces` (default: tap-gpu0..tap-gpu7) — creates bare tap interfaces (not bridged, no VLAN trunk) for E-W underlays. (M5-1, #28)
- gpu-b300 renderer templates and goldens (M5-2): **already delivered** in M2-3 (template tree) and M2-4 (golden files + emitter). Issue closes here as the criteria were met by those milestones. (M5-2, #29)

- E2E test `tests/e2e/test_cpu_host_boot.py` (M4.5-1): full first-boot gate for the CPU host. Launches `SN-CPU-001` via the QEMU launcher against a live Netbox + renderer + nginx-cache, waits up to 240 s for `cloud-init status --wait` to return 0, then asserts over SSH: `bond0` UP with LACP mode, both `nsa`/`nsb` enslaved, `bond0.100`/`.200`/`.300` up with correct IPs and MTUs, default route via the mgmt VLAN gateway. Skips cleanly if any prerequisite (KVM, OVS bridge, Netbox, renderer, nginx-cache, cloud image) is absent. `tests/e2e/conftest.py` wires up session-scoped fixtures and auto-marks all e2e tests with `@e2e`, `@requires_kvm`, and `@slow`. (M4.5-1, #27)

- Ansible role `ovs-harness` (`infra/ansible/roles/ovs-harness/`): installs Open vSwitch, creates bridge `br-test`, pre-creates tap interfaces `tap-nsa` / `tap-nsb`, and sets VLAN trunks 100/200/300. Idempotent via `ovs-vsctl --may-exist` and `ip link show` guards. `ansible-lint` production profile passes. (M4-1, #23)
- Ansible role `qemu-host` (`infra/ansible/roles/qemu-host/`): installs `qemu-system-x86`, `libvirt-daemon-system`, `libvirt-clients`, `virtinst`, `libguestfs-tools`, `python3-libvirt`; adds the lab user to `kvm` and `libvirt` groups; enables `libvirtd`. KVM assertion gated by `qemu_host_assert_kvm` (disable for CI). `ansible-lint` production profile passes. (M4-4, #26)
- QEMU launcher: `fixtures/vms/launch.py` exposes `launch_host(asset_tag, *, netbox_client, seed_server, image_path) -> VMHandle`. Reads N-S NIC MACs from Netbox via the renderer's own `load_host_intent` (DRY). Constructs the QEMU command line deterministically: SMBIOS type=1 `serial=<asset_tag>`, type=3 `asset=<asset_tag>`, `fw_cfg` NoCloud seed URL, SLIRP out-of-band NIC, two virtio tap NICs wired to OVS. `VMHandle` holds PID + MACs + `shutdown()` / `destroy()`. 16 unit tests for `build_cmdline` (pure function, no QEMU invocation). (M4-2, #24)
- Cloud image preparation: `fixtures/vms/prepare_image.py` idempotent CLI that downloads Ubuntu 24.04 Noble cloud image, verifies SHA256 against the mirror's `SHA256SUMS`, and optionally pre-installs `lldpd`, `chrony`, `ethtool` via `virt-customize`. Typed error hierarchy (`ChecksumMismatchError`, `NetworkError`, `VirtCustomizeError`). Atomic rename after verify. 12 unit tests with mocked fetch and subprocess. (M4-3, #25)

- ADR-0012 `docs/adr/0012-deferred-signed-seed-delivery.md`: documents the decision to serve seed files over plain HTTP on the management VLAN in v1, with three named threat vectors, their accepted risk, and explicit re-evaluation triggers (mgmt VLAN shared with untrusted devices, production workloads). Reserves the middleware seam (`_inject_cache_headers` in `middleware.py`) and lifespan hook for a future HMAC or mTLS implementation. nginx-cache template extended with a commented-out TLS `:443` server block (including optional mTLS directives) as a step-by-step enablement guide. (M3-4, #21)

- Cache behavior component tests (M3-2): `tests/component/nginx/test_cache.py` exercises the live nginx-cache tier — cold→warm transition (`X-Cache-Status: MISS` then `HIT`), the `X-Purge` header refresh (`BYPASS`, then `HIT` with the re-stored body), operational endpoints (`/healthz`, `/metrics`) served uncached, and the freshness contract (`Cache-Control: public, max-age=300` + stable `ETag`). Determinism via a per-test cache-buster query arg so each run gets a guaranteed-cold key. 7 tests; skip cleanly unless the seed server is reachable. Validated live on a DO Droplet (7/7). (M3-2, #19)
- Cache-friendly HTTP headers (M3-3): `RequestContextMiddleware` now stamps `Cache-Control: public, max-age=300`, `ETag` (SHA-256 of the body, hex-encoded), and `Last-Modified` (service start time) on 200 responses to `/v1/render/…` routes. Operational endpoints (`/healthz`, `/readyz`, `/metrics`) and error responses deliberately carry no `Cache-Control: public` so transient failures are never cached. 6 unit tests cover header presence, ETag stability across renders, and absence on error/operational routes. Fixed a test-isolation bug: `_restore_loader` autouse fixture ensures unit-test loader monkey-patch is always cleaned up so it never leaks into the integration test session. (M3-3, #20)
- Observability — logs + metrics (M2-6): wired structlog + Prometheus through the render path. `logging_config.py` is the single source of truth for structlog config (`app.py` no longer carries a duplicate); `cache_logger_on_first_use=False` so a level change takes effect immediately. `observability/metrics.py` defines the §7.6 contract — counters `host_config_requests_total{method,path,status}` / `host_config_renders_total{role,outcome}` / `host_config_cache_events_total{type}`, histograms `host_config_request_duration_seconds{method,path}` / `host_config_netbox_query_duration_seconds{endpoint}` / `host_config_render_duration_seconds{role}`, gauge `host_config_active_requests` (cardinality-safe: the `path` label is the *route template*, never the asset-tag-bearing concrete path; `cache_events_total` is declared for the nginx tier and not incremented by the renderer). Middleware emits the HTTP metrics + `request.received`/`request.completed`; the render handler binds `render_id`, times the Netbox load + render, classifies outcome (success/netbox_error/validation_error/template_error), and emits the DEBUG traceability story (§7.5). 12 unit tests: ordered-event traceability under `LOG_LEVEL=DEBUG`, per-outcome counter increments, route-template cardinality guard, duration histograms, `/metrics` exposure, active-requests balance. (M2-6, #16)
- Integration gate (M2.5-1): `tests/integration/test_renderer_e2e.py` exercises the full Netbox → loader → renderer → FastAPI HTTP path against a live Netbox. All six cloud-init payloads for both fixture hosts return HTTP 200 byte-equal to the on-disk goldens. Error path (unknown asset tag → 404 JSON envelope), operational endpoints (`/healthz` 200, `/readyz` 200 with live Netbox, `/metrics` Prometheus content-type) all verified. **M2 milestone closed.** (M2.5-1, #17)

### Deferred

- **Branch protection** on `main`. GitHub's branch protection API is gated behind paid plans for private repos; until the repo is on a paid plan (or goes public), the rules are enforced by local discipline (CONTRIBUTING.md). M7.5-1 revisits when the constraint changes. (M0-5)

### Changed

- `CONTRIBUTING.md` now reflects the solo-dev direct-to-main workflow (no feature branches, no PRs, no commit signing). PR template kept for future use. (M0-2)
- Default lab Droplet shape is now `s-4vcpu-8gb-intel` (Premium Intel + NVMe)
  in `tor1`, was `s-4vcpu-8gb-amd` in `nyc3`. AMD slugs aren't exposed on most
  DO accounts; the Intel NVMe tier roughly halves CPU e2e wall time vs. the
  basic SATA tier. `provision.yml`, `deploy-do.md` updated.
- `netbox-dev` role no longer does a single `docker compose up -d`. It starts
  `netbox` alone, polls its HTTP endpoint, then polls the container's Docker
  healthcheck, then brings up the worker — so a cold start with ~5 min of
  first-run Django migrations no longer fails with "dependency netbox … is
  unhealthy".
- `gpu-b300` user-data runcmd now installs RDMA packages *before* `netplan
  apply` (the lab netplan installs a default route via a non-existent gateway
  that breaks egress), enables `universe`, forces IPv4 (QEMU SLIRP is
  IPv4-only), uses `linux-modules-extra-$(uname -r)`, and waits for bond0 via a
  polling loop. Both `cpu` and `gpu-b300` deliver Netplan via `write_files` +
  `netplan apply` (cloud-init 25.x never fetches `network-config` from HTTP
  NoCloud seeds). Goldens regenerated.
- Lab Netbox B300 fixture sets `sriov_vfs: 0` for GPU NICs (QEMU virtio-net-pci
  has no SR-IOV; `virtual-function-count: 16` blocked netplan from creating
  bond0/VLANs/addresses). Production fixtures keep the real value (e.g. 16).
- nginx-cache manual invalidation switched from a (non-functional) `/_purge/`
  PURGE location to an `X-Purge` request header on the render URL
  (`proxy_cache_bypass`). The canonical `proxy_cache_purge` directive needs the
  third-party `ngx_cache_purge` module, which isn't packaged for Ubuntu 24.04;
  the M3-1 stub returned 501. The header bypass ships with stock nginx, meets
  the operational need (force one key fresh without a restart), and — being a
  header not a query arg — leaves the cache key unchanged so it overwrites the
  same entry. `nginx_cache_purge_path` var removed; role README + ADR-0012
  updated. (M3-2, #19)

### Fixed

- `prepare_image.py` falls back to a network-free `virt-customize` when the
  libguestfs appliance VM can't reach archive.ubuntu.com on DO Droplets using
  the systemd-resolved stub (127.0.0.53). The SSH key still gets injected;
  RDMA packages install in-guest at boot.
- `justfile` `_lab_ip` used `grep -oP` (Perl regex) — BSD grep on macOS has no
  `-P`, silently yielding an empty IP and breaking every Droplet-targeting
  recipe (`lab-image`, `lab-test`, `lab-refresh`, `lab-logs`). Switched to
  POSIX `grep -oE`. Same fix applied to the snippet in `deploy-do.md`.
- `test_cloud_init_exit_zero` (both host shapes) accepts cloud-init exit 2
  (`done` with recoverable errors) — LACP can't negotiate without a real switch
  partner in the lab, which is expected, not a failure.
