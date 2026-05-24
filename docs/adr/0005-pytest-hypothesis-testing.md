# ADR-0005: `pytest` + Hypothesis for testing

- **Status:** Accepted
- **Date:** 2026-05-23
- **Decider(s):** Karthik Gajjala

## Context

We need a testing framework that supports the full pyramid (unit → component → integration → e2e), parametrization, async test functions, property-based testing for invariants, and component tests against real containers.

## Decision

- **`pytest`** as the test runner.
- **`pytest-asyncio`** for `async def` test functions.
- **`pytest-cov`** for coverage reporting.
- **`Hypothesis`** for property-based testing of invariants (Pydantic models, the renderer).
- **`testcontainers-python`** for component tests against real Netbox / nginx (lands as needed).
- **Mutation testing (`mutmut`) deferred** to a future ADR after M7.5 stabilizes the code surface.

## Consequences

**Easier:**
- Industry-standard tooling; every Python contributor knows `pytest`.
- Parametrization (`@pytest.mark.parametrize`) replaces eight-near-identical-test-functions with one.
- Markers (`fast`, `slow`, `e2e`, `requires_kvm`) let CI shard tests by cost.

**Harder:**
- Hypothesis's "shrinking" can produce confusing minimal counter-examples; learning curve.
- Component tests against real containers are slower; have to budget carefully.

**Risks introduced:**
- Test flakiness from container startup time. Mitigation: session-scoped fixtures where state isolation allows; `pytest-timeout` global cap.

**Triggers for re-evaluation:**
- If mutation testing materially raises quality (after M7.5 we evaluate).

## Alternatives Considered

- **`unittest`** — stdlib, mature, but verbose and lacks parametrization, fixtures, and the ecosystem.
- **`nose2`** — niche.

## References

- Plan §6 (Testing strategy), §6.4 (deferred mutation testing).
- Related ADRs: 0003 (FastAPI testing via `httpx`), 0004 (ruff/mypy).
