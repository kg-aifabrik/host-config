# ADR-0004: `ruff` + `mypy --strict` as quality gates

- **Status:** Accepted
- **Date:** 2026-05-23
- **Decider(s):** Karthik Gajjala

## Context

We need automated enforcement of code quality. Two distinct concerns:

1. **Style + lint** — formatting, import ordering, anti-patterns, security smells.
2. **Static type checking** — catch type errors before runtime.

Both must run in pre-commit (fast local feedback) and CI (canonical gate).

## Decision

- **`ruff`** for lint + format. Replaces `flake8 + isort + pyupgrade + black + bandit` with a single fast Rust binary.
- **`mypy --strict`** for static type checking. Strict mode requires explicit types on every function signature; no implicit `Any`.

### `ruff` ruleset

Curated for signal-to-noise:

- `E` — pycodestyle errors
- `F` — pyflakes (unused imports, undefined names)
- `I` — isort (import ordering)
- `UP` — pyupgrade (modern syntax)
- `B` — flake8-bugbear (common bug patterns)
- `S` — flake8-bandit (security)
- `RUF` — Ruff-specific
- `PL` — pylint
- `TID` — tidy imports
- `SIM` — flake8-simplify

Ignored:
- `PLR0913` — "too many arguments." Fine when callers use named parameters; signal-to-noise low.
- Per-file: `tests/**` ignores `S101` (assert) and `PLR2004` (magic numbers).

### `mypy --strict` extras

- `warn_unreachable = true`
- `warn_unused_ignores = true`
- `show_error_codes = true`

## Consequences

**Easier:**
- One lint command (`ruff check`) replaces multiple tools.
- Format on save / pre-commit is effectively instant.
- Type errors surface at commit time, not in production.

**Harder:**
- `--strict` requires careful annotation; learning curve for contributors new to typed Python.
- Some libraries lack type stubs; we whitelist them in `[[tool.mypy.overrides]]`.

**Risks introduced:**
- `ruff` ruleset evolves; we may need to re-tune on `ruff` upgrades. Mitigation: pinned version; pre-commit hook autoupdate is opt-in.

**Triggers for re-evaluation:**
- If we adopt a different language for a subsystem.
- If `ruff` ever produces incorrect results we can't work around — unlikely given its track record.

## Alternatives Considered

- **flake8 + black + isort + bandit** — works but is four tools where one suffices. Slow in aggregate.
- **pyright** instead of mypy — faster, Microsoft-maintained. We picked mypy for broader ecosystem support and tighter ergonomics with FastAPI/Pydantic; revisit if we need the speed.

## References

- Plan §5 (Code conventions), §6 (Testing).
- `ruff` ruleset reference: https://docs.astral.sh/ruff/rules/
- `mypy --strict` reference: https://mypy.readthedocs.io/en/stable/command_line.html#cmdoption-mypy-strict
- Related ADRs: 0002 (`uv` — same vendor as `ruff`), 0001 (Python).
