# ADR-0006: Ansible for both cloud provisioning and in-host configuration

- **Status:** Accepted
- **Date:** 2026-05-23
- **Decider(s):** Karthik Gajjala

## Context

The lab needs two operationally distinct things:

1. **Provision cloud resources** — a DigitalOcean Droplet, SSH key, firewall, tags. State: "does this Droplet exist?"
2. **Configure the host** — install Docker, Netbox, OVS, KVM, nginx, the renderer. State: "does this host have the expected packages and services?"

The traditional split is Terraform/OpenTofu for provisioning + Ansible for in-host. Two tools, two languages (HCL + YAML), two state models, two sets of conventions.

For a one-Droplet lab at our v1 scale, that split's value is mostly historical — both ends are idempotent declarative operations.

## Decision

**Ansible** with the `community.digitalocean` collection handles **both** provisioning and configuration.

- `infra/ansible/playbooks/provision.yml` — creates Droplet, SSH key, firewall, tags via `community.digitalocean.digital_ocean_*` modules. Idempotent (checks for existing tagged resources before creating).
- `infra/ansible/playbooks/deploy-lab.yml` — composes the in-host roles (netbox-dev, renderer, nginx-cache, ovs-harness, qemu-host) on the provisioned Droplet.

One tool, one language, one mental model.

## Consequences

**Easier:**
- One tool to install; one config style to learn.
- Provisioning + config in a single playbook chain (`provision.yml` → output inventory → `deploy-lab.yml`).
- No state file management (OpenTofu/Terraform's `.tfstate` and its rough edges around remote backends).

**Harder:**
- Ansible's idempotency for cloud resources is less rigorous than OpenTofu's plan/apply model. For one Droplet, the gap is negligible; for multi-resource production, OpenTofu would be more honest.
- Drift detection is weaker — no `plan` step that lists what would change.

**Risks introduced:**
- At a larger scale (50+ sites with hundreds of resources), Ansible-as-IaC becomes painful. Mitigation: this is explicitly a v1 decision; switching to OpenTofu for provisioning when we outgrow Ansible is a contained refactor (the in-host roles don't change).

**Triggers for re-evaluation:**
- When provisioning involves >5 distinct resource types per environment.
- When multiple environments (dev/staging/prod) need to be managed simultaneously.
- When `community.digitalocean` modules can't express a needed primitive.

## Alternatives Considered

- **OpenTofu (provision) + Ansible (config)** — industry-standard split. Rejected at v1 scale; revisit when needed.
- **Terraform** — same as OpenTofu but with the HashiCorp BUSL licensing risk. We'd never pick this in 2026.
- **Pulumi (Python) + Ansible** — single Python ecosystem story. Compelling, but Pulumi state management has similar complexity to OpenTofu without the broader adoption.

## References

- Plan §3 (Stack choices).
- `community.digitalocean` docs: https://docs.ansible.com/ansible/latest/collections/community/digitalocean/
- Related ADRs: 0001 (Python), 0007 (`just` wrappers around the Ansible commands).
