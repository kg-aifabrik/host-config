# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Initial repository scaffolding: project metadata, code-quality tooling (`ruff`, `mypy --strict`, `pytest`/`pytest-cov`), pre-commit hooks, `justfile`, `.env.example`, `.editorconfig`. (M0-1, #1)
- `CODE_CONVENTIONS.md`: authoritative rulebook for file organization, function conventions, docstring style (Google + Approach + Scenarios), inline comment tag taxonomy, naming, error handling, testing, and observability. (M0-2, #2)

### Changed

- `CONTRIBUTING.md` now reflects the solo-dev direct-to-main workflow (no feature branches, no PRs, no commit signing). PR template kept for future use. (M0-2)
