# ADR-0011: Systems overview

- **Status:** Accepted
- **Date:** 2026-05-23
- **Decider(s):** Karthik Gajjala

## Context

Before code lands, a contributor should be able to answer: *what is host-config, what are its components, and how do they talk to each other?* This ADR establishes the canonical answer and the boundary between this project and everything else.

## Decision

host-config is **a renderer**. It produces cloud-init seed files for hosts. It is **not** an orchestrator, lifecycle manager, or fleet inventory system — those are upper layers that may call it but do not live in this repo (principle #12: lower layers do not know about higher layers).

### Components

| Component | Lives at | Responsibility |
|---|---|---|
| **Renderer service** | `src/host_config/` (this repo) | Given an asset tag, query Netbox, build a typed `HostIntent`, render the three cloud-init files, return them. Stateless. |
| **nginx cache** | `infra/ansible/roles/nginx-cache/` (this repo) | Front the renderer; serve repeat requests from disk cache; provide a stable HTTP endpoint for cloud-init. |
| **Netbox** | external; deployed by `netbox-dev` role | Source of truth for device intent (interfaces, IPs, VLANs, custom fields). |
| **Target host** | external; created by Ansible | Where the rendered config is applied. Tier 1: a QEMU VM. Production: a real bare-metal host. |
| **Cloud-init (on the target)** | external (Ubuntu's cloud-init) | NoCloud datasource fetches the seed at first boot; applies Netplan. |
| **OVS bridge** | `infra/ansible/roles/ovs-harness/` (this repo, Tier 1 only) | Simulates the upstream switch in the test lab — LACP partner + VLAN trunk. Replaced by the real switch fabric in production. |

### Interactions

![Systems overview](../diagrams/systems-overview.svg)

The canonical first-boot render flow:

![Render flow](../diagrams/render-flow.svg)

### Boundaries this project deliberately maintains

- **Renderer takes an asset tag and returns bytes.** It does not know whether the caller is a test harness, a first-boot, a re-provision, an RMA replacement, or anything else. Caller-domain lifecycle concepts (host status, intent type, environment) never enter the renderer's data model.
- **Cloud-init is dumb.** It fetches what it's told. It does not authenticate, does not know about Netbox, does not parse anything beyond what NoCloud requires.
- **nginx is dumb.** It caches HTTP responses. It does not understand the payload, does not implement business logic.
- **Netbox is dumb.** It's a database we query. Updates to Netbox are made by humans, fixtures, or future discovery agents — not by the renderer.

### What lives outside host-config

These are upper layers that may call into host-config but are not part of this repo:

- **Fleet inventory + lifecycle orchestrator** — drives Netbox state transitions (active → RMA → retired), schedules re-provisions, decides when to invoke the renderer. Out of scope at v1.
- **Customer/operator UX** — exposes lifecycle operations to humans.
- **Discovery agents** — write observed state back to Netbox.
- **CNI / K8s overlay** — sits on top of the host network configured by host-config. Future module in this repo (separate from the renderer).

## Consequences

**Easier:**
- Adding a caller (orchestrator, CLI, test harness) doesn't require changes to the renderer.
- Swapping cloud-init for another seed-consumer (Ignition, MAAS curtin) is a runbook change, not a renderer change.
- Replacing the OVS test bridge with a real switch fabric requires no renderer change.

**Harder:**
- Any change that crosses the renderer's boundary (e.g., "the renderer should know if a host is in RMA") is flagged for design review and must produce an ADR — the cost of layering discipline.

**Risks introduced:**
- Future contributors may be tempted to add upper-layer concepts into the renderer for short-term convenience. Mitigation: ADR + this systems-overview is the artifact that holds the line.

**Triggers for re-evaluation:**
- When the orchestrator joins the system and we have practical experience of the renderer-as-citizen pattern.

## Mirror document

This ADR is mirrored to a **living** [`docs/architecture/systems-overview.md`](../architecture/systems-overview.md). The ADR captures the decision (immutable); the mirror is where ongoing operational details accrue.

## References

- Plan §3, §4 (Repo structure), §6.8 (User-facing scenarios), §8.4 (SVG convention).
- Principle #12 (Lower layers do not know about higher layers).
- Diagrams: [systems-overview.svg](../diagrams/systems-overview.svg), [render-flow.svg](../diagrams/render-flow.svg).
