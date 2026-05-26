# Debug a failing lab VM cloud-init

The e2e suite launches QEMU VMs whose first boot is driven by cloud-init
fetching seed bytes from the on-Droplet renderer. When a host shape's e2e
tests fail at fixture setup or in early network/RDMA assertions, the cause
is almost always inside the VM's cloud-init runcmd. This runbook collects
the diagnostic patterns that worked during the 2026-05-26 cold-start
session so the next debugger doesn't re-derive them from scratch.

## First, classify the failure

| Symptom | Likely class |
|---|---|
| `pytest` ERROR at fixture setup, `cloud-init status --wait exited 1` | cloud-init runcmd failed fatally. See [Read what cloud-init actually did](#read-what-cloud-init-actually-did). |
| `pytest` ERROR at fixture setup, `cloud-init status --wait exited 2` and the assertion still fires | cloud-init exited `done with recoverable errors` (degraded). The e2e `_wait_for_cloud_init` helper accepts exit 2 — if you're seeing this in the test code, update the test to accept "done OR degraded". |
| `bond0` / VLANs / gpu addresses missing but gpu MTU is set | netplan generated the networkd files but networkd hasn't acted on them yet, OR `virtual-function-count: 16` is choking SR-IOV on a virtio-net-pci NIC. See [bond0 doesn't appear](#bond0-doesnt-appear). |
| `lsmod \| grep rdma_rxe` returns nothing | The cloud-init apt-install of `linux-modules-extra-$(uname -r)` failed. See [apt-install fails inside the VM](#apt-install-fails-inside-the-vm). |
| Tests pass locally but fail on a freshly-provisioned Droplet | Either the renderer is serving stale cached bytes (see [Renderer + nginx-cache iteration](#renderer--nginx-cache-iteration)) or Netbox's first-run migrations haven't finished. Re-run `just lab-up`; the playbook is idempotent and the second run almost always lands. |

## Read what cloud-init actually did

The fastest way to see what happened inside the VM during the failing
cloud-init run is the **QEMU serial console**, which the e2e fixtures
already redirect to `/tmp/{cpu,b300}-boot.log` on the Droplet:

```bash
ssh root@$LAB_IP 'tail -200 /tmp/b300-boot.log'
```

Key things to grep for:

```bash
ssh root@$LAB_IP 'grep -i "apt-get\|locate\|FATAL\|networkctl\|netplan apply\|E:" /tmp/b300-boot.log'
```

If cloud-init ran but the error is opaque, get the structured cloud-init
status from inside the VM **while it's still up** (the fixture tears it
down on test failure — see next section):

```bash
ssh -p 2223 -i tests/e2e/fixtures/test_vm_key ubuntu@$LAB_IP \
    'sudo cloud-init status --long; sudo tail -80 /var/log/cloud-init-output.log'
```

## Capture inside-VM state when the fixture won't keep the VM alive

The pytest fixture (`b300_vm` / `cpu_vm`) calls `handle.shutdown()` in
its `finally:` block, so the VM dies the moment any test fails. To grab
inside-VM diagnostics anyway, **pipe them to the serial console from
inside the runcmd** — the serial console is captured to a host file
that survives the VM's death:

In `templates/gpu-b300/user-data.j2`, temporarily add:

```yaml
runcmd:
  - …
  - ip link show 2>&1 | tee /dev/ttyS0
  - ls /run/systemd/network/ 2>&1 | tee /dev/ttyS0
  - cat /run/systemd/network/10-netplan-bond0.network 2>&1 | tee /dev/ttyS0 \
      || echo "no bond0 network file" | tee /dev/ttyS0
  - sudo networkctl status 2>&1 | tee /dev/ttyS0
  - journalctl -u systemd-networkd --no-pager -n 50 | tee /dev/ttyS0
  - …
```

Then `just lab-refresh` (to push the new template + flush nginx-cache),
`just lab-test`, and grep the boot log on the Droplet:

```bash
ssh root@$LAB_IP 'cat /tmp/b300-boot.log' | less
```

**Strip these lines before committing.** They make cloud-init noisy and
slow down boot by ~2 s per dump.

## bond0 doesn't appear

This usually traces to one of three causes (in order of how often we hit
them):

1. **`virtual-function-count` on a non-SR-IOV NIC** (the 2026-05-26 root
   cause). netplan generates `SRIOVVirtualFunctions=N` in the gpu NICs'
   networkd `.network` files; networkd refuses to bring up the entire
   bond + VLAN stack because the underlying NICs don't have
   `/sys/class/net/gpuN/device/sriov_totalvfs`.

   **Confirm**: `grep "failed parsing sriov_totalvfs" /tmp/b300-boot.log`
   on the Droplet.

   **Fix**: lab Netbox fixture sets `sriov_vfs: 0` for GPU NICs. If you
   see this on a fresh Droplet, the fixture got reverted or a new role
   inherited the wrong value. Check `fixtures/netbox/data/b300-host.yaml`.

2. **Polling race**: netplan generates files but networkd hasn't created
   bond0 yet when cloud-init returns. The gpu-b300 user-data has a
   `timeout 60 sh -c 'until ip link show bond0 …'` after `netplan apply`
   to plaster over this. If you see `timeout` in the log and bond0
   *still* doesn't exist after 60 s, the bond config itself is broken —
   not a timing issue.

3. **Default-route hairball** that breaks egress. The lab netplan
   installs a default route via `bond0.100`'s 10.42.10.1, which doesn't
   exist in the lab. Anything that needs outbound HTTP (apt, in
   particular) must run **before** `netplan apply` — see the runcmd
   ordering in `templates/gpu-b300/user-data.j2` and the comment block
   above it.

## apt-install fails inside the VM

Four flavors of failure, all hit during 2026-05-26:

| Error | Cause | Fix |
|---|---|---|
| `Temporary failure resolving 'archive.ubuntu.com'` from `virt-customize` (host-side) | libguestfs appliance VM can't reach the systemd-resolved stub at 127.0.0.53 | `prepare_image.py` already falls back to a network-free customize. Look for the `image.install_failed_falling_back` warning. RDMA packages install inside the guest via cloud-init runcmd instead. |
| `Unable to locate package linux-modules-extra-virtual` | No such meta-package exists | Use `linux-modules-extra-$(uname -r)` (kernel-pinned). Already in the template. |
| `Unable to locate package ibverbs-utils` / `rdmacm-utils` | These live in `universe`, which the noble cloud image doesn't enable | `sed -i 's/^Components: main.*$/Components: main universe/' /etc/apt/sources.list.d/ubuntu.sources`. Already in the template. |
| `Unable to connect to archive.ubuntu.com [IP: 2606:…]` or intermittent `No route to host` | QEMU SLIRP is IPv4-only; archive.ubuntu.com resolves to a Cloudflare IPv6 address | `apt-get -o Acquire::ForceIPv4=true …`. Already in the template. **Also** check: are you running apt *before* `netplan apply`? After netplan apply, the default route is broken (see "Default-route hairball" above). |

## Renderer + nginx-cache iteration

The renderer caches Jinja templates at process startup, and nginx-cache
caches rendered responses for ~5 min. **Editing a template locally and
re-running `just lab-test` will silently use the stale cached output**
unless you flush both.

The supported iteration loop:

```bash
# edit src/host_config/render/templates/…
just lab-refresh    # rsync, restart renderer, flush nginx-cache, wait /healthz
just lab-test
```

`lab-refresh` is the only reliable way to pick up a template change
without a full `lab-down` + `lab-up`.

## When in doubt

```bash
just lab-logs    # renderer journal + nginx access log + OVS state + boot logs
```

The boot logs (`/tmp/{cpu,b300}-boot.log`) are the high-signal source.
The nginx access log proves whether the VM actually fetched a fresh
seed (look for the `GET /v1/render/SN-…/user-data` line).

## Known-good baseline

`docs/artifacts/e2e-run-2026-05-26/` snapshots a fully-passing run. Diff
a failing rendered seed or boot log against the matching artifact to
isolate what changed.
