# Contributing

> Operating principles, branch policy, commit conventions, and definition of done.

## Development setup

1. Install [uv](https://github.com/astral-sh/uv).
2. Install Python 3.12: `uv python install 3.12`.
3. `uv sync` to create the venv and install dependencies.
4. `just hooks` to install pre-commit hooks.
5. Copy `.env.example` to `.env` and fill in values (see [README §Configuration](README.md#configuration)).

## Branch policy

The repo is currently a **solo-dev project**. Commits land **directly on `main`** — no feature branches, no PRs, no review ceremony. When a second contributor joins, this is the first convention to revisit (via a new ADR).

- **No feature branches in v1.** Work straight on `main`.
- **No merge commits.** Use rebase if pulling in remote changes.
- **Branch protection deferred.** GitHub's branch protection API is gated behind paid plans for private repos. Until the repo is on a paid plan (or goes public), the rules below are enforced by **local discipline**: don't force-push, don't merge with red CI. CI workflows (`ci.yml`, `e2e.yml`) run on every push and are the post-hoc safety net.

## Commit conventions

- [Conventional Commits](https://www.conventionalcommits.org/) format, enforced by a `commit-msg` pre-commit hook.
- Allowed types: `feat`, `fix`, `docs`, `test`, `refactor`, `chore`, `ci`, `perf`, `build`.
- Body required for non-trivial changes; reference the linked issue with `Closes #N` so it auto-closes.
- **No commit signing.** Repo is private; access-controlled at the GitHub layer.

## Workflow per issue

1. Claim the issue (assign yourself).
2. Run `just lint && just typecheck && just test` to confirm a green starting point.
3. Implement; add tests for any new code; update docstring/README/ADR if relevant.
4. Run `just lint && just typecheck && just test` again — all must pass.
5. Commit with a Conventional Commit message containing `Closes #N`.
6. `git push origin main`.
7. Issue auto-closes; CI runs on the push and is the final gate.

## Definition of done

- Acceptance criteria from the issue all checked off.
- Tests written and passing locally and in CI.
- Coverage does not regress meaningfully (>2% drop triggers review per CODE_CONVENTIONS §6).
- Public-facing changes update docstring/README/ADR where relevant.
- CHANGELOG entry generated via Conventional Commit type.
- Pushed to `main`; CI green.

## Working with issues and milestones

Issues are organized by GitHub Milestones (M0, M1, M1.5, …, M7.5) corresponding to sections of the [implementation plan][plan]. Each issue body links back to the relevant plan section via commit-pinned URL.

[plan]: https://github.com/kg-aifabrik/research/blob/main/host-net-config/implementation-plan.md

The `.5` milestones (M1.5, M2.5, …, M7.5) are **integration gates** — they prove a vertical slice works end-to-end before the next horizontal layer starts.

## Code conventions

See [CODE_CONVENTIONS.md](CODE_CONVENTIONS.md) for the authoritative rulebook covering:

- File organization (file length budget, intra-file ordering)
- Function conventions (single responsibility, length budget, DI for testability)
- Docstring style (Google + Approach + Scenarios)
- Inline comment tag taxonomy (`# WHY:`, `# NOTE:`, `# SAFETY:`, `# TODO(#issue):`, `# HACK:`)
- Naming rules
- Error handling
- Testing strategy
- Observability conventions

## Local quality gates

Pre-commit hooks run on every commit:

- `ruff check` + `ruff format`
- `mypy --strict` on changed files
- `gitleaks` (secret scanning)
- Commitlint (Conventional Commits format)
- File-size cap (1 MB)
- YAML/JSON validation

If a hook fails, fix the underlying issue and commit again. Do **not** bypass with `--no-verify` unless explicitly approved.

## Reporting issues

For bugs or questions, open an issue with the appropriate template. For security issues, see [SECURITY.md](SECURITY.md).
