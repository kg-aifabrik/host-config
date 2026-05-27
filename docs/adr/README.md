# Architecture Decision Records

Durable record of every load-bearing design choice.

## Format

Michael Nygard format. Each ADR follows the [template](template.md):

- **Context** — what's the situation that demands a decision
- **Decision** — what we chose
- **Consequences** — what falls out from that choice (good and bad)
- *(Optional)* **Alternatives Considered** — what we rejected and why

## Discipline

- **Immutable once landed.** To change a decision, write a new ADR that supersedes the old one. The old ADR stays in the repo with a `Superseded by ADR-NNNN` note added at the top.
- **Numbered sequentially.** Filename: `NNNN-short-slug.md` (e.g., `0001-python-3-12.md`).
- **When to write one** — any decision that crosses module boundaries, affects public contracts, or chooses between credible alternatives. If you'd struggle to explain the choice to a new contributor in two minutes, write the ADR.

## Index

The eleven seed ADRs landing in M0-4:

- `0001` — Python 3.12 as implementation language
- `0002` — `uv` as package and project manager
- `0003` — FastAPI + Pydantic v2 + Jinja2 for the renderer (includes `/v1/` versioning policy)
- `0004` — `ruff` + `mypy --strict` as quality gates
- `0005` — `pytest` + Hypothesis for testing (mutation testing deferred)
- `0006` — Ansible (with `community.digitalocean`) for both provisioning and config
- `0007` — `just` as the task runner
- `0008` — GitHub-rendered Markdown + SVG diagrams in `docs/diagrams/`
- `0009` — structlog + Prometheus for observability (distributed tracing deferred)
- `0010` — GitHub Actions for CI
- `0011` — Systems overview: component catalog + interactions + SVG diagrams

Later ADRs land as decisions cross the bar.

- `0012` — Deferred signed-seed delivery (TLS + HMAC): v1 serves seeds over plain HTTP on the management VLAN; TLS placeholder in nginx config reserved for promotion to production
- `0013` — GPU roles `gpu-b200` (RoCE, mirrors b300) and `gpu-h200` (InfiniBand/IPoIB backend): new `InfinibandUnderlay` model + `ib_underlays` field; RoCE/IB count invariants enforce the per-role split
