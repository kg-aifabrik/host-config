# Runbooks

Operational procedures. Each runbook is a step-by-step walkthrough for a specific operational task, written for an engineer who hasn't done it before.

## Conventions

- **Title** — one-line action ("Deploy lab to DigitalOcean").
- **Prerequisites** — what must be in place before starting.
- **Steps** — numbered, copy-pasteable commands.
- **Verification** — how you know the operation succeeded.
- **Teardown** — how to roll back if needed (per principle 11: leave no trace).
- **Troubleshooting** — common failure modes and how to fix them.
- **Estimated cost** — for runbooks that consume external resources (e.g., DO Droplets).

## Index

- [Deploy lab to DigitalOcean](deploy-do.md) — provision a DO Droplet, configure the full stack, run E2E tests, and tear it down cleanly.
- *Debug a failing render* — opens after M2.
- *Force-evict the nginx cache* — opens after M3.
