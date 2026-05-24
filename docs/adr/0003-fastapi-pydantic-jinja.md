# ADR-0003: FastAPI + Pydantic v2 + Jinja2 for the renderer

- **Status:** Accepted
- **Date:** 2026-05-23
- **Decider(s):** Karthik Gajjala

## Context

The renderer is an HTTP service that, given an asset tag, builds a typed intent from Netbox and emits three cloud-init files (`meta-data`, `user-data`, `network-config`). We need:

- An HTTP framework that integrates cleanly with typed Python.
- A schema/validation framework for the intent model.
- A templating engine for the cloud-init outputs.

The renderer is an inert function (given input X, produce output Y) — no state, no orchestration, low complexity surface beyond the schema and the templates themselves.

## Decision

- **FastAPI** for the HTTP layer.
- **Pydantic v2** for `HostIntent` and all schema validation.
- **Jinja2** for the cloud-init templates.
- **All consumer-facing routes are versioned under `/v1/`** from the first commit. Operational endpoints (`/healthz`, `/readyz`, `/metrics`) are unversioned and forever-stable.

### API versioning policy

- Routes are organized under `/vN/`. Today: `/v1/render/{asset}/{file}`.
- **Major version bumps** are for breaking contract changes (route shape, payload semantics, query parameters). New routes go in a new major version; both versions are served in parallel for one cycle (N-1 support window) before the older is removed.
- **Minor changes** are additive: new endpoints, new optional response fields. They do not bump the major version.
- **Operational endpoints** (`/healthz`, `/readyz`, `/metrics`) are forever-stable — never under a version prefix, never broken.

## Consequences

**Easier:**
- Pydantic-FastAPI integration: types declared once, OpenAPI generated automatically.
- Strong invariant enforcement via Pydantic validators (cross-field invariants live in one place).
- Versioning policy documented from day one — no agonizing-rewrite when v2 is needed.

**Harder:**
- FastAPI's async-by-default means downstream blocking I/O (pynetbox) needs `asyncio.to_thread`. Acceptable.
- Pydantic v2 has stricter validation than v1; small fixture data may need refinement to satisfy validators. Acceptable.

**Risks introduced:**
- FastAPI is mature but its release cadence has historically been less stable than Flask's. Mitigation: pin major version in `pyproject.toml`; review breaking-change notes at upgrade time.

**Triggers for re-evaluation:**
- If the renderer becomes performance-critical (unlikely; it's not on any host's data path).
- If we ever need gRPC contracts (no current path).

## Alternatives Considered

- **Flask + marshmallow** — older, less typed.
- **Litestar** — newer, fast, but smaller ecosystem and unclear long-term governance.
- **Direct ASGI + manual validation** — bespoke, no OpenAPI generation, more code.

## References

- Plan §3 (Stack choices), §5 (Code conventions), §6.8 (User-facing scenarios).
- Related ADRs: 0001 (Python), 0009 (observability stack — wraps FastAPI).
