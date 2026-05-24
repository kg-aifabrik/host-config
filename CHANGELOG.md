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

### Changed

- `CONTRIBUTING.md` now reflects the solo-dev direct-to-main workflow (no feature branches, no PRs, no commit signing). PR template kept for future use. (M0-2)
