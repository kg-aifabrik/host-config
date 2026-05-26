# E2E artifacts — 2026-05-26 (DigitalOcean tor1 Premium Intel NVMe)

Reference artifacts from a clean, fully-passing run of the host-config e2e
suite against a DigitalOcean lab Droplet. Capture date: 2026-05-26.

Use these files when triaging future e2e regressions:

- Compare a stale rendered output against `rendered-SN-*` to spot template
  drift between releases.
- Diff a failing boot log against `b300-boot-serial.log` / `cpu-boot-serial.log`
  to see at which boot phase the regression starts.

## Test result

`pytest-output.txt` — **23 / 23 passed in 136 s** (12 CPU + 11 B300).

## Droplet

`droplet-host-info.txt` — uname, OS release, lsblk, CPU/mem.

| Field   | Value                                  |
| ------- | -------------------------------------- |
| Region  | `tor1` (Toronto)                       |
| Size    | `s-4vcpu-8gb-intel` (Premium Intel NVMe) |
| Image   | `ubuntu-24-04-x64`                     |
| Kernel  | `6.8.0-117-generic` (cloud image)      |

AMD slugs were not exposed on the account in tor1; Premium Intel + NVMe was
the closest "performance + NVMe" option. CPU e2e run time on this tier:
**~43 s** (vs. ~76 s on the previous `s-4vcpu-8gb` SATA tier).

## Rendered seed artifacts

What the renderer / nginx-cache served to the lab VMs:

| Asset tag    | Role     | Files                                                                    |
| ------------ | -------- | ------------------------------------------------------------------------ |
| `SN-CPU-001` | `cpu`    | `rendered-SN-CPU-001-{user-data,network-config,meta-data}`              |
| `SN-GPU-001` | `gpu-b300` | `rendered-SN-GPU-001-{user-data,network-config,meta-data}`              |

These are byte-equal to the rendered output that boots the e2e VMs. They
should match the unit-test goldens under `src/host_config/render/golden/`
*except* that the B300 `user-data` here has `virtual-function-count: 0`
(lab Netbox value) while the unit-test golden has `: 16` (production
realism).

## Serial console boot logs

`b300-boot-serial.log` / `cpu-boot-serial.log` — full kernel + cloud-init
output captured via QEMU `-serial file:…`. Useful for verifying that:

- The Soft-RoCE bring-up runs in the expected order (apt-install → netplan
  apply → bond0 wait → modprobe rdma_rxe → `rdma link add`).
- cloud-init exits `done` (or `degraded` w/ recoverable errors — LACP can't
  negotiate without a real switch partner, which is expected in the lab).
- Bond/VLAN/RoCE interfaces come up in <90 s end-to-end.

## What changed in this run vs. the previous baseline

Earlier baseline lived on a SATA `s-4vcpu-8gb` Droplet that the user
destroyed. To get to a green run on the rebuilt Droplet, this session
made the following durable code changes:

1. `infra/ansible/playbooks/provision.yml` — region `tor1`, size
   `s-4vcpu-8gb-intel` (was `nyc3` / `s-4vcpu-8gb`).
2. `fixtures/vms/prepare_image.py` — falls back to a network-free
   virt-customize when libguestfs's appliance VM can't reach
   archive.ubuntu.com (Droplets using systemd-resolved at 127.0.0.53).
3. `fixtures/netbox/data/b300-host.yaml` — GPU NIC `sriov_vfs: 0` in the
   lab fixture (QEMU virtio-net-pci doesn't support SR-IOV; `: 16` blocked
   netplan from creating bond0 / VLANs / gpu addresses).
4. `src/host_config/render/templates/gpu-b300/user-data.j2` — install
   RDMA packages *before* `netplan apply` (the lab netplan installs a
   default route via the bond0.100 gateway 10.42.10.1 which doesn't exist;
   doing apt first uses the SLIRP DHCP default route). Also: enable
   `universe` via deb822 sources edit, `Acquire::ForceIPv4=true` (SLIRP
   is IPv4-only), `linux-modules-extra-$(uname -r)` for the kernel-pinned
   rdma_rxe package, polling loop for bond0 (`networkctl wait-online
   --interface=` / `--timeout=` are not supported in Ubuntu 24.04's
   systemd 255), tolerate `cloud-init status` exit 2 (degraded).
