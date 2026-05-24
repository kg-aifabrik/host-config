# Contributing

> Operating principles, branch policy, commit conventions, and definition of done.

## Development setup

1. Install [uv](https://github.com/astral-sh/uv).
2. Install Python 3.12: `uv python install 3.12`.
3. `uv sync` to create the venv and install dependencies.
4. `just hooks` to install pre-commit hooks.
5. Copy `.env.example` to `.env` and fill in values (see [README §Configuration](README.md#configuration)).

## Branch policy

- **Trunk-based**: feature branches off `main`, squash-merge back.
- **Branch naming**: `<type>/<short-slug>` where type is `feat`, `fix`, `docs`, `chore`, `refactor`, `test`. Examples: `feat/m2-1-host-intent-models`, `docs/runbook-do-deploy`.
- **Force-pushes to `main` are blocked** by branch protection (M0-5).

## Commit conventions

- [Conventional Commits](https://www.conventionalcommits.org/) format, enforced by a `commit-msg` pre-commit hook.
- Allowed types: `feat`, `fix`, `docs`, `test`, `refactor`, `chore`, `ci`, `perf`, `build`.
- Body required for non-trivial changes; reference the linked issue (`Closes #N`).
- Squash-merge preserves the PR title as the squashed commit message — write PR titles in the same format.

## Pull request flow

1. Open the issue (or claim an existing one).
2. Create a feature branch.
3. Implement; add tests; update docs/ADR if relevant.
4. Push; open the PR with the issue linked in the description.
5. Wait for CI; address any failures.
6. **Self-review trivial PRs** (docs typos, dependency bumps, generated files). **Substantive PRs** get a deliberate "looks good" review comment before merge.
7. Squash-merge.
8. Issue auto-closes via `Closes #N` in the PR description.

## Definition of done

- Acceptance criteria from the issue all checked off.
- Tests written and passing locally and in CI.
- Coverage does not regress meaningfully (>2% drop triggers review per CODE_CONVENTIONS §6).
- Public-facing changes update docstring/README/ADR where relevant.
- CHANGELOG entry generated via Conventional Commit type.
- Merged to `main`.

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
