# ADR-0002: `uv` as package and project manager

- **Status:** Accepted
- **Date:** 2026-05-23
- **Decider(s):** Karthik Gajjala

## Context

Python dependency and environment management has historically fragmented across pip, virtualenv, pip-tools, Poetry, pipx, conda. Each tool covers part of the story (deps, lockfile, env, run, project metadata) but not the whole story. We need exactly one tool covering all of it.

Criteria: single binary, fast, well-engineered lockfile, project-aware (`pyproject.toml`-native), maintained by a credible team.

## Decision

**`uv`** from Astral (the maintainers of `ruff`).

`uv` covers env creation, dependency resolution, lockfile (`uv.lock`), `uv run` (replaces `python -m`), `uv sync` (deterministic install), `uv python install` (Python toolchain management), and project metadata via standard `pyproject.toml`. Single Rust-based binary; substantially faster than pip/Poetry; growing ecosystem adoption in 2026.

## Consequences

**Easier:**
- Single command for every dependency operation.
- Lockfile is a real, deterministic artifact (committed in this repo).
- Fast CI: `uv sync` from cold is seconds, not minutes.
- Python version pinning via `.python-version` and `uv python install`.

**Harder:**
- Younger than pip/Poetry; some edge cases may surface.
- Documentation is improving but less broad than pip's.

**Risks introduced:**
- Vendor lock-in to Astral. Mitigation: `uv` reads/writes standard `pyproject.toml`, so migration to pip/Poetry remains a flag-day-not-a-rewrite.
- Bug surface — `uv` is newer code than pip. Mitigation: pinned `uv` version in CI; track Astral release notes.

**Triggers for re-evaluation:**
- If Astral abandons `uv` (unlikely given their commercial model).
- If a critical reproducibility bug surfaces that they don't fix promptly.

## Alternatives Considered

- **Poetry** — mature, but slower, and the `poetry.lock` format is non-standard PEP-621-adjacent rather than the emerging consensus `uv.lock`/PDM format.
- **pip + pip-tools + venv** — battle-tested but split across three tools; the workflow seams accumulate friction.
- **PDM** — close competitor; comparable feature set. We picked `uv` for the Astral track record and performance.

## References

- Plan §3.
- Astral docs: https://github.com/astral-sh/uv
- Related ADRs: 0001 (Python 3.12), 0004 (ruff/mypy from same vendor).
