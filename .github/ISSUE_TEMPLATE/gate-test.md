---
name: Gate test (integration milestone)
about: An integration-gate test proving a vertical slice works (M1.5, M2.5, …, M7.5)
title: "Mx.5-y: gate test description"
labels: ["type:gate", "kind:test"]
assignees: []
---

**Plan reference:** [§9 — Mx.5 — Gate: <name> (integration)](https://github.com/kg-aifabrik/research/blob/main/host-net-config/implementation-plan.md#mx5-gate-name-integration)

## Goal

What slice does this gate prove? Be specific.

## Acceptance criteria

- [ ] Test exists at `tests/integration/` or `tests/e2e/` mirroring the source layout
- [ ] Test runs in CI within the budgeted time
- [ ] Asserts on observable outcomes (HTTP status, byte-equality with golden, log events, metric increments)
- [ ] Test teardown is reliable (principle #11: leave no trace)

## Definition of done

Per §8 of the plan: acceptance criteria checked; merged to `main`; CHANGELOG entry generated.
