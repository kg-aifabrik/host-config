# Security

This is a private, internal-use repository. If you find a security issue, contact <kg@aifabrik.com> directly. Do **not** open public issues for security matters.

## Reporting

Email a clear description of the issue, including:

- What the vulnerability is.
- How to reproduce it.
- The potential impact.
- Suggested mitigation, if you have one.

Expect an acknowledgment within 3 business days.

## Scope

In scope:

- The renderer service (`src/host_config/`).
- Ansible roles and playbooks (`infra/ansible/`).
- CI workflows (`.github/workflows/`).
- Fixture scripts (`fixtures/`).

Out of scope:

- Third-party dependencies (report to their maintainers; we'll bump versions on advisory).
- Issues that require physical access to a host running the renderer.
- Issues in the deferred / future-work items documented in the implementation plan.
