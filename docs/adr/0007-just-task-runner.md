# ADR-0007: `just` as the task runner

- **Status:** Accepted
- **Date:** 2026-05-23
- **Decider(s):** Karthik Gajjala

## Context

Engineering principle #9: every common operation has exactly one entry point. We need a task runner that provides those entry points (`lab-up`, `lab-down`, `test`, `lint`, …) — readable, cross-platform, single-file.

## Decision

**`just`** (https://github.com/casey/just).

A single `justfile` at the repo root declares every common operation as a recipe. Recipes can compose, accept arguments, and source `.env` automatically via `set dotenv-load`.

## Consequences

**Easier:**
- Self-documenting: `just` with no args lists all available targets.
- Cross-platform — works on macOS, Linux, WSL.
- Cleaner than Makefile for non-build tasks (no tab/space gotchas; no implicit shell-out quirks).
- Auto-loads `.env` so secrets are available to every recipe.

**Harder:**
- Contributors need to install `just` (single static binary via Homebrew, cargo, or release tarball).

**Risks introduced:**
- Minimal. `just` is a single binary by a stable maintainer; the recipe DSL is simple enough that switching to Make would be a contained migration if ever needed.

**Triggers for re-evaluation:**
- If `just` is ever abandoned or breaks compatibility (no signal of either).

## Alternatives Considered

- **Make** — universal but historically painful for non-build operations. Tab/space gotchas; recipe variable scoping is awkward.
- **invoke / nox** — Python-based, but adds a Python invocation layer to every command.
- **`uv run` directly** — possible but loses the named-target abstraction; users have to remember each tool's invocation.

## References

- Plan §3 (Stack choices), Principle #9 (one way to do common things).
- `just` docs: https://just.systems/
- Related ADRs: 0006 (Ansible — wrapped by `just lab-*` targets).
