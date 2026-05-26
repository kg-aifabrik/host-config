# Deploy lab to DigitalOcean

Provisions a single DigitalOcean (DO) Droplet and configures it as a
complete host-config lab: Netbox, renderer, nginx-cache, OVS bridge, and
QEMU. The E2E tests run on the Droplet itself (they need `/dev/kvm` and
the OVS bridge present locally).

## Prerequisites

| Requirement | Check |
|---|---|
| DO account with a read+write API token | `echo $DIGITALOCEAN_TOKEN` |
| SSH key registered with DO | `doctl compute ssh-key list` |
| `doctl` CLI installed | `doctl version` |
| `ansible-galaxy` collections installed | `cd infra/ansible && ansible-galaxy collection install -r requirements.yml` |
| `.env` populated from `.env.example` | `cat .env` |

**`.env` keys required for this runbook:**

```
DIGITALOCEAN_TOKEN=<your token>
SSH_KEY_FINGERPRINT=<fingerprint shown by doctl compute ssh-key list>
```

## Configuration

| Variable | Default | Override with |
|---|---|---|
| `lab_droplet_name` | `host-config-lab` | `-e lab_droplet_name=my-lab` |
| `lab_region` | `tor1` | `-e lab_region=sfo3` |
| `lab_droplet_size` | `s-4vcpu-8gb-intel` | `-e lab_droplet_size=c-4` |

**Size + region note:** the default is the Premium Intel + NVMe tier in
`tor1`. AMD Droplet slugs (`s-4vcpu-8gb-amd`, `c-4-amd`) are not exposed
on most DO accounts; if you have one that does, `c-4-amd` (in `blr1` or
`lon1`) gives dedicated AMD EPYC + NVMe and is the fastest option for
nested KVM e2e runs. `c-4` (CPU-Optimized Intel) in `lon1` / `blr1` is
the next best. If you stay in `tor1`, `s-4vcpu-8gb-intel` is the only
NVMe option.

## Provisioning

Provisions the Droplet and writes `infra/ansible/inventory/lab`:

```bash
just lab-up
```

Under the hood this runs:
1. `ansible-playbook -i localhost, infra/ansible/playbooks/provision.yml`
2. `ansible-playbook -i infra/ansible/inventory/lab infra/ansible/playbooks/deploy-lab.yml`

Expect ~8–12 minutes for a clean provision (Docker install + Netbox first-boot is the slow step).

**Verification after provisioning:**

```bash
# Get the Droplet IP from the inventory file.
DROPLET_IP=$(grep -oP '\d+\.\d+\.\d+\.\d+' infra/ansible/inventory/lab | head -1)

# renderer health
curl -s http://$DROPLET_IP/healthz

# nginx-cache serving a render (replace SN-CPU-001 with your asset tag)
curl -sv http://$DROPLET_IP/v1/render/SN-CPU-001/meta-data
```

## Deployment (smoke test)

```bash
just lab-test
```

Runs `pytest -m e2e` on the Droplet over SSH. Tests marked `@requires_kvm`
skip automatically if `/dev/kvm` is absent.

## Canonical acceptance test (M6.5-1)

The full end-to-end acceptance test for the host-config project:

```bash
just lab       # up → test → down; trap ensures teardown on any exit
```

Expected outcome on `s-4vcpu-8gb-amd` with `/dev/kvm` present:
- `test_cpu_host_boot.py` (M4.5-1): all 12 assertions pass — bond0 LACP,
  VLAN IPs + MTUs, default route, nsa/nsb enslaved.
- `test_b300_host_boot.py` (M5.5-1): all 12 assertions pass — bond0 UP,
  VLAN IPs, 8× gpu0..7 at MTU 9000, rdma_rxe loaded, 8 rxe devices,
  rping between rxe_gpu0 ↔ rxe_gpu1.

If `/dev/kvm` is absent (Droplet shape doesn't expose it), tests marked
`@requires_kvm` skip automatically; the test run still exits 0.

**Total run time:** ~25–35 minutes wall-clock.  
**Cost per burn:** $0.04–$0.07 (see cost table below).

## Teardown

```bash
just lab-down
```

Deletes the Droplet and verifies zero residual resources with the
`host-config-lab` tag via the DO API (see `destroy.yml`). Leaves no
trace on your DO account.

`just lab` wraps `up → test → down` with `trap 'just lab-down' EXIT INT TERM`
so teardown runs even if the tests fail or you hit Ctrl-C.

## Teardown integrity (M6.5-2)

Principle #11 (leave no trace): every cycle must start and end with
zero `host-config-lab`-tagged DO resources.

**Automated check** (runs in the E2E suite when `DIGITALOCEAN_TOKEN` and
`doctl` are present):

```bash
pytest tests/e2e/test_do_teardown.py -v
```

The test suite:
1. Asserts zero tagged Droplets/volumes/snapshots at the start.
2. Runs `just lab-down` (idempotent on clean state) and re-asserts zero.
3. Calls `just lab-down` twice to verify idempotent destroy.

**Manual inventory check** (before and after a `just lab` cycle):

```bash
# Before.
doctl compute droplet list --tag-name host-config-lab
doctl compute volume list
doctl compute snapshot list

# After just lab completes.
doctl compute droplet list --tag-name host-config-lab  # must be empty
```

**Failure path (trap verification):**

To manually verify the trap fires on failure:

```bash
# Start a lab-up, kill it partway through, then verify lab-down cleans up.
just lab-up &
LAB_PID=$!
sleep 60          # let provisioning start
kill $LAB_PID
sleep 5
just lab-down     # must exit 0 and leave zero resources
doctl compute droplet list --tag-name host-config-lab  # must be empty
```

## Estimated cost

| Shape | $/hr | Typical run (up → test → down) | ≈ total |
|---|---|---|---|
| `s-4vcpu-8gb-amd` | $0.071 | 25–35 min | $0.04 |
| `c-4` (CPU-optimized) | $0.119 | 25–35 min | $0.07 |

DO bills by the hour, minimum 1 hour per Droplet lifetime. If you
provision and immediately destroy, you pay for 1 hour. Running the full
`just lab` pipeline typically costs < $0.15 end to end.

## Troubleshooting

### Provisioning fails: `DIGITALOCEAN_TOKEN is undefined`

`.env` is not loaded or is missing the token. Verify:

```bash
grep DIGITALOCEAN_TOKEN .env      # must be non-empty
just --list                       # if no output, just can't read .env
```

`just` loads `.env` automatically via `set dotenv-load`. If running
ansible-playbook directly, export the variable first:

```bash
set -a; source .env; set +a
ansible-playbook -i localhost, infra/ansible/playbooks/provision.yml
```

### Ansible run hangs at "Clone netbox-docker"

GitHub is rate-limiting the git clone (common on ephemeral DO IPs). The
`netbox-dev` role clones `netbox-community/netbox-docker`. To diagnose:

```bash
ssh root@$DROPLET_IP "git clone --depth 1 https://github.com/netbox-community/netbox-docker.git /tmp/nb-test"
```

If this hangs or returns `fatal: repository not found`, the Droplet has
no outbound HTTPS. Check the DO firewall rules (port 443 egress must be
open — DO's default policy allows all egress).

### E2E test fails on Droplet but passes on Lima

Most likely cause: the Droplet doesn't have `/dev/kvm` (the
`@requires_kvm` tests would have skipped, not failed). A failing test
that passes locally points to a timing or resource issue:

1. Check cloud-init serial output: `ssh root@$DROPLET_IP "cat /tmp/b300-boot.log"` (B300 test) or `/tmp/cpu-boot.log` (CPU test).
2. Increase the `_CLOUD_INIT_TIMEOUT_S` constant in the test if the
   Droplet is slower than the Lima VM.
3. Verify OVS bridge: `ssh root@$DROPLET_IP "ovs-vsctl show"`.
4. Check the renderer log: `ssh root@$DROPLET_IP "journalctl -u host-config-renderer -n 50"`.
