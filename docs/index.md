# host-config — documentation

Entry point for project documentation. GitHub renders these Markdown files natively; no build step.

## Sections

- **[Architecture](architecture/)** — systems overview, component contracts, sequence flows. The systems overview is the canonical "what is host-config and how do its parts fit" document.
- **[Architecture Decision Records (ADRs)](adr/)** — the durable record of why we made each load-bearing decision. Immutable once landed; superseded by new ADRs.
- **[Runbooks](runbooks/)** — operational procedures for deploying, debugging, and tearing down the lab.
- **[Diagrams](diagrams/)** — SVG diagrams with Excalidraw sources. Referenced inline from Markdown via relative paths.

## How to navigate

- New contributor? Start with the [README](../README.md) for setup, then [CODE_CONVENTIONS.md](../CODE_CONVENTIONS.md) for the rulebook.
- Trying to understand why we chose X over Y? Search [`adr/`](adr/) for the relevant ADR.
- Trying to deploy or operate? Start with the relevant runbook in [`runbooks/`](runbooks/).
- Trying to understand the system shape? Read [`architecture/systems-overview.md`](architecture/systems-overview.md).

## Design intent

This repo's design intent — what we're building, why, and how — lives in the [implementation plan][plan] in the companion research repo. The plan is the durable source of truth; this repo's documentation is the operational layer on top of it.

[plan]: https://github.com/kg-aifabrik/research/blob/main/host-net-config/implementation-plan.md
