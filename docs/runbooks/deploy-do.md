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
| `lab_region` | `nyc3` | `-e lab_region=sfo3` |
| `lab_droplet_size` | `s-4vcpu-8gb-amd` | `-e lab_droplet_size=c-4` |

**KVM note:** the default `s-4vcpu-8gb-amd` shape runs on AMD EPYC
hardware and typically exposes `/dev/kvm` via AMD-V. If the E2E tests
skip with `no KVM device`, switch to a CPU-optimized `c-4` or `c2-4vcpu-8gb`
Droplet which reliably enables nested virtualisation.

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

## Teardown

```bash
just lab-down
```

Deletes the Droplet and verifies zero residual resources with the
`host-config-lab` tag via the DO API (see `destroy.yml`). Leaves no
trace on your DO account.

Or let `just lab` do it for you — it wraps `up → test → down` with a
`trap` so teardown runs even if the tests fail or you hit Ctrl-C.

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
