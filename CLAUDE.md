# CLAUDE.md

> Read this first when starting a fresh Claude Code session against this repo.
> Optimized for resumability: a new session can come up to speed in under
> five minutes from this document plus a quick scan of the linked artifacts.

## What this is

Production-grade implementation of the host network configuration pipeline:
Netbox-driven intent → typed Pydantic model → Jinja-rendered cloud-init seed →
applied at first boot. Tier 1 lab for an Inference-as-a-Service platform
buildout. Solo-dev project, private repo, no OSS license.

Current state at a glance:

```
ls docs/                  → architecture, ADRs (11), runbooks, diagrams
ls src/host_config/       → errors, models, netbox (renderer pieces)
ls infra/ansible/         → netbox-dev role + playbook
ls fixtures/netbox/       → populate.py + 2 YAML host fixtures
ls tests/                 → unit / component / integration / e2e
```

## The two load-bearing documents

1. **Implementation plan** (the design source of truth) — lives in the
   companion **research repo**: <https://github.com/kg-aifabrik/research/blob/main/host-net-config/implementation-plan.md>.
   Milestone breakdown, ~41 GitHub issues, dependency graph, charter, the
   12 engineering principles. **Immutable once seeded**; changes here happen
   via ADRs, not by editing the plan.

2. **[CODE_CONVENTIONS.md](./CODE_CONVENTIONS.md)** — the rulebook for code,
   tests, logging, error handling, observability. Every commit is reviewed
   against this. If you change a convention, edit this file and explain
   what changed in the commit body.

## Workflow conventions

**Solo-dev direct-to-main.** No feature branches, no PRs, no review ceremony.

- Commit straight to `main` with `git commit -m "<conventional message>" && git push`.
- Message ends with `Closes #N` so the linked issue auto-closes.
- **No commit signing required.** GH free tier doesn't support branch protection
  on private repos anyway — discipline is the gate.
- **Conventional Commits** enforced by `commit-msg` pre-commit hook
  (`feat:`, `fix:`, `docs:`, `test:`, `refactor:`, `chore:`, `ci:`, `perf:`, `build:`).

When a second contributor joins, the first move is to revisit this convention
(write a new ADR).

## Before every commit

Run these locally — CI runs them on push, but local feedback is faster:

```bash
just lint        # ruff check + ruff format --check
just typecheck   # mypy --strict
just test        # pytest (unit suite by default)
```

All four must be green. If you wrote new code, you wrote new tests for it
(per CODE_CONVENTIONS §8).

## When you close an issue (the established pattern)

Every closed issue in this repo follows this pattern — keep it.

1. **Commit with `Closes #N`** in the message body. The issue auto-closes
   on push.
2. **Tick the acceptance-criteria checkboxes** in the issue body:
   ```bash
   REPO=kg-aifabrik/host-config
   BODY=$(gh issue view N --repo $REPO --json body --jq .body | sed 's/- \[ \]/- [x]/g')
   gh issue edit N --repo $REPO --body "$BODY"
   ```
3. **Post a closing comment** with the commit SHA, what landed in concrete
   terms, links to relevant files / ADRs / future issues. The closing
   comment is the durable record after the issue is archived.
4. **Update `CHANGELOG.md`** under `## [Unreleased]` with an `### Added` /
   `### Changed` / `### Deferred` entry referencing the issue number.

Look at any of issues #1–#11 to see the pattern in action.

## Where to find what

| Question | Where |
|---|---|
| What's the next issue to tackle? | Implementation plan §9 + GH issue tracker filtered by milestone (look for unclosed `Mx-y` titles) |
| What's done so far? | `CHANGELOG.md` + closed issues with closing comments |
| Why did we choose X? | `docs/adr/` — eleven seed ADRs cover stack choices |
| How do the components fit? | `docs/architecture/systems-overview.md` (mirrors ADR-0011) |
| What does code look like? | `CODE_CONVENTIONS.md` + look at `src/host_config/models/` as the reference |
| How do I run tests? | `just test` for unit; `just lint-ansible` for Ansible; component/integration require Netbox running |
| How do I bring up Netbox? | `cd infra/ansible && uv run ansible-playbook -i localhost, -c local playbooks/netbox-dev.yml` |
| How do I populate fixtures? | `uv run python -m fixtures.netbox.populate` (Netbox must be up) |
| What env vars does the dev setup need? | `.env.example` lists them; README §Configuration explains |

## Common operations

```bash
# Install deps after a pyproject.toml change
uv sync

# Run a single test file
uv run pytest tests/unit/models/test_intent.py -v

# Run only tests that need Netbox (will skip if not reachable)
uv run pytest -m requires_netbox

# Format everything (writes changes)
just format

# Bring up local Netbox (Docker-Compose stack via Ansible)
cd infra/ansible && uv run ansible-playbook -i localhost, -c local playbooks/netbox-dev.yml

# Apply schema + load fixtures into the running Netbox
uv run python -m fixtures.netbox.populate

# Inspect an issue's acceptance criteria
gh issue view N --repo kg-aifabrik/host-config

# See what merged recently
git log --oneline --decorate -20
```

## Cross-session pitfalls (learned the hard way; don't relearn)

- **Netbox 4.2+ moved MAC addresses** to a first-class
  `/api/dcim/mac-addresses/` endpoint. The interface POST payload silently
  ignores `mac_address`. `fixtures/netbox/populate.py` handles this in a
  separate `_ensure_mac_address` pass.
- **Netbox 4.x SELECT custom fields** require a separate `ChoiceSet`
  object. We sidestep by making `bf3_mode` TEXT; the `BF3_MODES` constant
  in `src/host_config/netbox/schema.py` is the source of truth, enforced
  at the loader layer (forthcoming in M2-2).
- **Netbox v1 vs v2 API tokens.** Netbox 4.6+ defaults to v2 tokens which
  require server-side `API_TOKEN_PEPPERS`. The `netbox-dev` Ansible role
  mints v1 tokens (40-char plaintext) to keep dev config-free. Production
  token format is a future ADR.
- **Pydantic `strict=True` is intentionally NOT set** on our models —
  it rejects legitimate string→IPv4Interface coercion from JSON inputs.
  See `src/host_config/models/interface.py` `_StrictModel` docstring.
- **The seed SVG diagrams** in `docs/diagrams/` were hand-authored as SVG
  without `.excalidraw` sources. First edit → import the SVG into
  excalidraw.com, save both `.excalidraw` source and re-exported `.svg`.
- **Branch protection** is deferred per GH free-tier limits. M7.5-1
  revisits when the repo upgrades to Pro or goes public.
- **Ansible CLI** must run from `infra/ansible/` (the `ansible.cfg` there
  sets `roles_path = roles`). The `just lint-ansible` target handles this;
  manual invocations need `cd infra/ansible` first.

## Repo invariants — don't break these

1. **Principle #12 (lower layers don't know about upper layers).** The
   renderer renders. It does not know about lifecycle states,
   environments, or anything above its layer. If you find yourself
   wanting to add caller-domain concepts to a model or schema, stop
   and write an ADR first.
2. **Tests before commit.** Every commit landing code has tests that pass.
3. **Issue closure ritual.** Tick boxes, closing comment, CHANGELOG entry.
   See "When you close an issue" above.
4. **Conventional commit messages.** Pre-commit rejects otherwise.
5. **No commit signing.** Don't suddenly turn it on; if it should change,
   write an ADR.
6. **Idempotent infrastructure.** Ansible roles re-apply as no-ops; fixture
   loaders re-apply as no-ops; schema applies re-apply as no-ops. Verified
   by the M1.5-1 integration test pattern.
7. **Public APIs are versioned.** Renderer routes under `/v1/`; operational
   endpoints (`/healthz`, `/readyz`, `/metrics`) are forever-stable
   (no version prefix). See ADR-0003 for the versioning policy.

## Quick sanity check (do this on session start)

```bash
# Confirm tools
uv --version && gh auth status && docker info | head -3

# Confirm repo state
git log --oneline -5 && git status

# Confirm tests are green
just lint && just typecheck && just test

# If working on something that needs Netbox:
curl -sf http://127.0.0.1:8000/api/ -o /dev/null && echo "netbox up" || echo "netbox down — run the netbox-dev role"
```

If all of these pass, you're ready to pick up the next issue.

## What to do if you're stuck

1. Read the relevant ADR (`docs/adr/`).
2. Read the relevant section of the implementation plan (link at top).
3. Search closing comments on closed issues — many subtle decisions are
   documented there.
4. Check `git log -p <file>` — the commit message that introduced the
   code usually explains why.
