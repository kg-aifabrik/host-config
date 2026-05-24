# ADR-0010: GitHub Actions for CI

- **Status:** Accepted
- **Date:** 2026-05-23
- **Decider(s):** Karthik Gajjala

## Context

We need a CI system to run lint, type-check, unit tests, component tests, integration tests, and (eventually) e2e tests on every push. Requirements: integrates natively with the repo host (GitHub), KVM available on runners (for QEMU-based e2e tests), reasonable free tier.

## Decision

**GitHub Actions** hosted runners (`ubuntu-24.04`).

Workflows live in `.github/workflows/`:

- `ci.yml` — lint, type-check, unit + component tests, coverage on every push and PR.
- `e2e.yml` — full pipeline e2e using KVM acceleration; runs on PR and on push to `main`.
- `docs-links.yml` — broken-link + missing-SVG checker on Markdown files (M7-3).

Hosted Ubuntu runners support nested KVM as of 2023+, which makes the QEMU-based e2e tests feasible without self-hosted runners.

## Consequences

**Easier:**
- Native repo integration — `gh` CLI, PR checks, status reporting all just work.
- Free tier (2000 minutes/month for private repos) is sufficient at our scope.
- KVM available — no special runner provisioning needed.
- Familiar YAML syntax.

**Harder:**
- Hosted runners have time/storage limits (6-hour job cap, ~14 GB disk). Our e2e tests target <10 minutes total, so this is fine.
- Caching strategy matters for `uv sync` and testcontainers images to keep CI fast.

**Risks introduced:**
- Vendor lock-in to GitHub. Mitigation: workflows are mostly `uv run …` invocations; migrating to GitLab CI / similar would mean re-expressing in another YAML dialect, not rewriting the test code.

**Triggers for re-evaluation:**
- If hosted-runner KVM support degrades.
- If runtime exceeds the 2000-minute free tier for a sustained period.

## Alternatives Considered

- **CircleCI / Buildkite** — more flexible but adds a vendor dependency outside GitHub.
- **Self-hosted runners** — gives full control of resources but requires us to operate the runner fleet. Premature for v1.

## References

- Plan §3 (Stack choices), §8.3 (CI gates).
- GHA docs: https://docs.github.com/en/actions
- Related ADRs: 0008 (Markdown rendered by GitHub natively — no separate doc-deploy needed).
