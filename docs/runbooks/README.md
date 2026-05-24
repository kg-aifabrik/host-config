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

Runbooks land as their associated operations get implemented:

- *Deploy lab to DigitalOcean* — M6-2 will land at `deploy-do.md`.
- *Debug a failing render* — opens after M2.
- *Force-evict the nginx cache* — opens after M3.

For now this is an empty index awaiting content.
