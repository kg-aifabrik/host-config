# Architecture

Living documentation for the system's shape, interactions, and contracts.

## Contents

- **[systems-overview.md](systems-overview.md)** — the canonical document. Catalogues every component, the boundaries between them, and the interactions (request/response, fixture-time, deploy-time). Mirrors ADR-0011.

Future docs in this directory cover specific subsystems as they land:

- Request flow for a render (after M2)
- Cache behavior and invalidation (after M3)
- VM boot sequence end-to-end (after M4)
- East-west fabric + Soft-RoCE (after M5)
- DigitalOcean deployment topology (after M6)
- CI pipeline (after M7)

Each document includes at least one SVG diagram per the convention in [`docs/diagrams/`](../diagrams/).
