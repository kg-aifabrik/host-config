# Code conventions

> The authoritative rulebook for this repo. Every PR is reviewed against this document.
>
> Living document — edits via PR. Major changes (e.g., relaxing a rule) require an ADR.

This document captures the durable conventions for code, tests, logging, and error handling. It is lifted from §5 of the [implementation plan][plan] with minor adaptations as code lands.

[plan]: https://github.com/kg-aifabrik/research/blob/main/host-net-config/implementation-plan.md

## Table of contents

1. [File organization](#1-file-organization)
2. [Function conventions](#2-function-conventions)
3. [Docstring style](#3-docstring-style)
4. [Inline comments](#4-inline-comments)
5. [Naming](#5-naming)
6. [Error handling](#6-error-handling)
7. [Concurrency and side effects](#7-concurrency-and-side-effects)
8. [Testing](#8-testing)
9. [Observability](#9-observability)

---

## 1. File organization

- **Every file declares its scope in a module-level docstring** at the top. Two sentences minimum: what this module is responsible for, and what it explicitly is not.
- **File length budget: ~400 lines (soft cap).** Exceeding this is a smell — the file likely has too much responsibility, or a function inside it is over-budget.
- **One primary class per file** when classes are involved. Tightly coupled helper classes can share a file with the class they serve.
- **Standard intra-file ordering:**
  1. Module docstring
  2. `from __future__ import annotations`
  3. Standard library imports
  4. Third-party imports
  5. First-party imports
  6. Module-level constants
  7. Type aliases
  8. Public functions/classes (in order of importance)
  9. Private functions (prefixed `_`)
  10. `__all__` declaration at the bottom listing the public API

## 2. Function conventions

- **Single responsibility.** A function does one thing. The one thing might be "orchestrate three other functions" — that's fine — but it's still one thing.
- **Function length budget: ~50 lines (soft cap).** If a function exceeds this, factor.
- **Cyclomatic complexity ≤ 10** (enforced by `ruff` rule `C901`).
- **Pure functions preferred.** When a function has side effects (I/O, mutation, time), name and document them explicitly.
- **Dependency injection.** External state (clock, random, network client, file system) is passed in, not imported globally. This makes functions testable without monkeypatching.

## 3. Docstring style

Google style, extended with `Approach` and `Scenarios`. **Every public function has a full docstring.** Private helpers have at least a one-line summary.

The `Scenarios:` block is **load-bearing** — it is the spec the test file implements. A reviewer reading the docstring knows what tests must exist; a contributor writing tests has a checklist.

### Worked example

```python
def render_network_config(
    intent: HostIntent,
    templates_dir: Path,
    *,
    now: Callable[[], datetime] = datetime.utcnow,
) -> bytes:
    """Render the cloud-init network-config YAML for a host.

    Approach:
        Selects the Jinja2 template directory for the host's role,
        constructs a deterministic Jinja environment (autoescape off
        for YAML, undefined raises), and renders the template against
        the intent. The output is post-processed via ruamel.yaml to
        produce stable key ordering, ensuring byte-deterministic
        output across runs given identical input.

    Args:
        intent: A validated `HostIntent` for the target host. Must
            already have passed all cross-field invariants (e.g.,
            exactly one default gateway).
        templates_dir: Root of the templates tree. Expected to contain
            a subdirectory matching `intent.role`.
        now: Callable returning the current UTC datetime. Injected
            for testability. Default: `datetime.utcnow`.

    Returns:
        UTF-8 encoded bytes of the rendered YAML, suitable for direct
        delivery as a cloud-init `network-config` file.

    Raises:
        TemplateNotFoundError: No template directory exists for
            `intent.role`.
        RenderError: The Jinja template raised an `UndefinedError` or
            similar — usually indicates the intent lacks a field the
            template expected.

    Scenarios:
        - Happy path: cpu role intent → produces parseable YAML with
          bond0 + three VLAN children.
        - Happy path: gpu-b300 role intent → produces YAML with all
          10 NICs configured.
        - Missing role template → raises TemplateNotFoundError with
          the offending role name.
        - Intent missing a required field → raises RenderError with
          the field name in the message.
        - Same intent rendered twice → byte-identical output (tested
          via golden-file comparison).
        - now() returning a fixed timestamp → embedded timestamp in
          output matches (tests determinism).

    Example:
        >>> intent = HostIntent(role="cpu", ...)
        >>> output = render_network_config(intent, Path("templates"))
        >>> assert b"bond0.100" in output
    """
```

## 4. Inline comments

- **Prefer "why" over "what."** The code already says what; comments explain the rationale.
- **Use prefixed tags** for searchability:
  - `# WHY: ...` — explains a non-obvious decision
  - `# NOTE: ...` — caller-relevant context (e.g., "this assumes input is sorted")
  - `# SAFETY: ...` — invariant that must hold; explains why something is safe
  - `# TODO(#issue): ...` — must reference an open issue; ungrounded TODOs are blocked by pre-commit
  - `# HACK: ...` — known workaround; should link to the issue tracking proper fix
- **Reference ADRs, RFCs, issue numbers** where context lives elsewhere.
- **Document non-obvious trade-offs at the decision site.** A reader two years from now should not have to do archaeology to understand why we chose `layer3+4` hash policy over `layer2+3`.

### Example

```python
# WHY: We hash on layer3+4 (not the bonding default of layer2)
# because both bond members face the same logical LACP partner
# (the ESI-LAG pair). Layer2 hashing collapses all traffic to
# one slave; layer3+4 spreads flows by 5-tuple. See ADR-0011.
parameters: dict[str, Any] = {
    "mode": "802.3ad",
    "transmit-hash-policy": "layer3+4",
}
```

## 5. Naming

- **Modules:** `snake_case`, descriptive. No `utils.py`, `helpers.py`, `common.py`, `misc.py`. Every module name is a noun describing what lives there.
- **Public functions:** `verb_noun` form. `render_network_config`, `load_host_from_netbox`, `build_intent`.
- **Private functions:** `_prefix` (single underscore).
- **Constants:** `SCREAMING_SNAKE_CASE`, module-level only.
- **Types:** `PascalCase`. `HostIntent`, `BondMember`, `RenderError`.
- **Type aliases:** `PascalCase` followed by `Type` only when ambiguous. Prefer `AssetTag = NewType("AssetTag", str)` over `AssetTagType = ...`.

## 6. Error handling

- **Specific exception classes per failure scenario.** No bare `Exception` raises. Defined in `errors.py` per package.
- **Errors carry context.** Exception messages include the operation being attempted and the relevant identifiers (asset tag, host name, etc.).
- **No bare `except:`.** Specific catches only. Re-raise unless explicitly handled.
- **Retry policies are configurable, not implicit.** Use `tenacity` for retry logic; configure timeouts, max attempts, and backoff explicitly.
- **Errors at module boundaries are typed.** A function that calls Netbox should not let `requests.exceptions.HTTPError` leak out; wrap into `NetboxQueryError`.

### Example

```python
# src/host_config/netbox/errors.py
class NetboxError(Exception):
    """Base class for all Netbox-related errors."""


class NetboxQueryError(NetboxError):
    """Netbox query failed (timeout, 5xx, etc.)."""

    def __init__(self, asset_tag: str, operation: str, cause: Exception) -> None:
        super().__init__(
            f"Netbox query failed for asset_tag={asset_tag} "
            f"during operation={operation!r}: {cause}"
        )
        self.asset_tag = asset_tag
        self.operation = operation
        self.cause = cause
```

## 7. Concurrency and side effects

- **Async is opt-in, not pervasive.** FastAPI routes are `async def`; downstream blocking I/O (pynetbox) runs in a thread pool via `asyncio.to_thread`. Don't make pure logic async.
- **No global mutable state.** Configuration is passed in at startup; runtime state lives in well-defined objects.
- **Time, randomness, and external state are injected.** Functions take `now: Callable[[], datetime]` arguments where time matters; tests substitute fixed values.

## 8. Testing

### Pyramid composition

Full pyramid every milestone. Test the things that matter; don't game the coverage number.

| Level | Scope | Speed | Network/Container |
|---|---|---|---|
| Unit | Single function or tightly coupled function group | <50 ms each | None |
| Component | One module against real downstream container (Netbox, nginx) | <2 s each | testcontainers |
| Integration | Multiple modules wired together; mocks at the system edge | <10 s each | testcontainers |
| E2E | Full pipeline; gate-milestone tests | <5 min each | Full lab via OVS+QEMU |

### Conventions

- **File structure mirrors source.** `src/host_config/render/emitter.py` → `tests/unit/render/test_emitter.py`. Searchability matters.
- **Test names:** `test_<scenario>_<expected>`. Examples: `test_missing_mac_raises_clear_error`, `test_idempotent_render_produces_same_bytes`. A reader scanning failed tests should know what broke from the name alone.
- **Each function's docstring `Scenarios:` block enumerates required tests.** A new test must correspond to a scenario in the docstring; a new scenario must produce a test.
- **Parametrize liberally.** Don't write 8 nearly-identical test functions; parametrize one.
- **One assertion per test (soft rule).** Multi-assertion tests are OK when they describe one logical observation, but prefer splitting.

### User-facing scenarios — integration tests mandatory

Every user-facing scenario **must** have at least one integration test. New user-facing scenarios get new integration tests as part of the same change — no "we'll add the test later." See §6.8 of the [implementation plan][plan] for the full enumeration; in short: HTTP renderer endpoints, nginx cache behavior, cloud-init NoCloud first-boot per role, lab lifecycle end-to-end, fixture/schema idempotency.

### Property-based testing

For functions with non-trivial invariants — model validators, renderers, anything that should hold for "any valid input."

- Generate arbitrary `HostIntent` objects; assert no IP duplications, MTU monotonicity (parent ≥ child), exactly one default gateway.
- Generate arbitrary VLAN sub-interface configurations; assert renderer never emits invalid Netplan YAML.
- Generate arbitrary asset tags; assert the renderer never panics, only raises the documented exception types.

### Coverage stance

- **Unit tests cover key functions and key flows.** Trivial getters, Pydantic-emitted boilerplate, and pure delegation methods don't require dedicated unit tests.
- **Line coverage is reported, not gated at a single number.** Target ~75% on `src/host_config/`; ~60% on Ansible-linted infra. A PR-level *drop* of >2% is reviewed but not auto-blocked.
- **Coverage is a floor, not a ceiling.** Mutation testing (deferred until M7.5; see plan §6.4) will complement coverage once added.
- **Reviewers may require tests for code that lacks them**, regardless of coverage number, if the code is non-trivial.

### What we deliberately don't test

- **Trivial getters/setters.**
- **Pydantic's own serialization.** Pydantic has its own tests.
- **Third-party library contracts.** Trust the contract; test our usage of it.
- **Implementation details that aren't part of the contract.** Tests should survive refactors that don't change behavior.

## 9. Observability

### Logging philosophy

- **Structured exclusively.** Every log line is key-value pairs (JSON in production, console-friendly in dev). No f-string interpolation into a single message string for variables.
- **Logs tell a story.** A reader following a single request through the logs from receive to response should understand exactly what happened, in order, with timing.
- **One source of truth for log config:** `src/host_config/logging_config.py`. All modules use `structlog.get_logger(__name__)`; the config module wires up processors, levels, and outputs.

### Log levels

| Level | When to use |
|---|---|
| `TRACE` | Function entry/exit with arguments. Off in production. Custom level (DEBUG-1). |
| `DEBUG` | Intermediate state inside non-trivial operations. Key decision points. Enable to follow a request. |
| `INFO` | Lifecycle events: process start, request received, request completed, cache miss/hit, render succeeded. Default level in production. |
| `WARN` | Degraded operation. Will continue, but operator should know. |
| `ERROR` | Operation failed with bounded blast radius. Single request errored; system continues. |
| `CRITICAL` | Process must exit or take drastic action. Rare. |

### Correlation IDs

Every request gets:

- `request_id` (UUID, generated in middleware, returned in response header `X-Request-Id`)
- `asset_tag` (from URL path, bound into context for all logs in this request)
- `render_id` (generated when rendering starts; binds the renderer subprocess's logs)

Bound via `structlog.contextvars.bind_contextvars`; every log line in the request scope automatically includes these fields.

### Debug-level traceability requirement

**Concrete acceptance criterion:** with `LOG_LEVEL=DEBUG` set, a single request to `/v1/render/SN12345/network-config` produces logs that let an engineer reconstruct:

1. Request received with what asset tag, what request_id.
2. Cache check, hit/miss, reason.
3. (If miss) Netbox query started, completed in X ms, returned record with what shape.
4. Pydantic model construction started, validators passed.
5. Template selection: which role, which template directory.
6. Render started, completed in X ms, produced N bytes.
7. Response sent with what status, what byte count, total request duration.

This is testable: the test asserts the expected log events occur in order with the right key fields.

### Metrics (Prometheus)

Exposed at `/metrics`. Following Prometheus conventions; every metric is documented in `src/host_config/observability/metrics.py` with the rationale for its existence (what question does this answer?). See plan §7.6 for the canonical list.

### Distributed tracing

Deferred until a second service joins the system (e.g., the CNI module). For Tier 1, structlog correlation IDs already let us follow a single request end-to-end through the one process we have. See plan §7.7.
