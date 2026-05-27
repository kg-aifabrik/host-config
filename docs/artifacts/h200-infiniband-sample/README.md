# H200 + InfiniBand — sample cloud-init seed

A hand-authored NoCloud seed for test-provisioning an **NVIDIA HGX H200**
host whose backend compute fabric is **InfiniBand (NDR)**, not the
RoCE-over-Ethernet the `gpu-b300` role uses. Drop-in for a cloud-init
NoCloud datasource; all MACs/IPs are **placeholders** to substitute.

> The renderer now supports a `gpu-h200` role natively (see
> [ADR-0013](../../adr/0013-gpu-h200-infiniband-role.md)) — it emits an
> equivalent seed from a Netbox-backed `HostIntent`
> (`fixtures/netbox/data/h200-host.yaml`). This hand-authored sample is kept
> as a standalone, self-documenting reference (placeholders, IB caveats, and
> the `cloud-localds` recipe) for quick test provisioning without the full
> Netbox → renderer pipeline.

## Files

| File | Purpose |
|---|---|
| `user-data` | Cloud-config: hostname, RDMA memlock, IB module load, packages, and the netplan (delivered via `write_files` + `netplan apply`). **Self-contained** — works on any NoCloud datasource. |
| `network-config` | The same netplan as a standalone NoCloud network-config (for local/ISO seeds that read it). Byte-identical network body to `user-data`. |
| `meta-data` | `instance-id` + `local-hostname` (NoCloud requires it). |

## Topology

```
                 ┌─ nsa ─┐
   management ───┤        ├─ bond0 (802.3ad) ─┬─ bond0.100  mgmt     10.42.10.10/24  (default route → .1)
   (Ethernet)    └─ nsb ─┘                    ├─ bond0.200  storage  10.42.20.10/24  (MTU 9000)
                                              └─ bond0.300  ingress  10.42.30.10/24

   compute       ib0 … ib7  (8x InfiniBand NDR HCA ports, IPoIB)
   (InfiniBand)  10.42.100.10/24 … 10.42.107.10/24   one /24 per rail, MTU 2044
```

### Why this differs from the B300 (RoCE) role

| | gpu-b300 (RoCE) | this sample (InfiniBand) |
|---|---|---|
| Backend transport | Ethernet + RoCEv2 | InfiniBand (IPoIB for IP) |
| RDMA | Soft-RoCE `rdma_rxe` (lab substitute) | native, in HCA hardware — **no `rdma_rxe`** |
| Backend bonding | none (per-NIC) | none (multi-rail via NCCL/UCX, not LACP) |
| Backend MTU | 9000 (jumbo Ethernet) | 2044 (IPoIB datagram default) |
| Key modules | `rdma_rxe` | `mlx5_ib`, `ib_ipoib`, `ib_umad`, `rdma_ucm` |
| Verify with | `ibv_devinfo`, `rping` | `ibstat`, `ibv_devinfo`, `ibping` |

## Placeholders to substitute

- **`aa:bb:cc:00:00:01` / `:02`** — the two management Ethernet port MACs.
- **`aa:bb:cc:00:0e:00` … `:07`** — the 8 InfiniBand ports. **Caveat:** real
  ConnectX-7/NDR IPoIB ports do **not** have a 6-byte Ethernet MAC — they
  have a 20-byte IPoIB hardware address and a port GUID. The placeholder MAC
  form is kept only so a templating system can fill all ports uniformly. For
  real hardware, replace each `ibN` `match:` with either:
  - `match: {name: "ibp*s0"}` (predictable kernel name), or
  - a systemd `.link` / udev rule keyed on the port GUID.
- **IPs / hostname / `instance-id`** — adjust to the target pod/site.

## Feeding it to cloud-init

**Local / ISO NoCloud seed** (cloud-init reads all three files):

```bash
cloud-localds -N network-config seed.iso user-data meta-data
# attach seed.iso as a CD-ROM to the H200 VM/host on first boot
```

**HTTP NoCloud seed** (e.g. `ds=nocloud-net;s=http://<server>/`): cloud-init
25.x does **not** fetch `network-config` over HTTP — but `user-data` already
embeds the netplan via `write_files`, so serving just `user-data` +
`meta-data` is sufficient and applies the identical network config.

## Verify after boot

```bash
cloud-init status --wait                 # expect: done (or degraded w/o a switch peer)
ip -br link | grep -E 'ib[0-7]|bond0'    # ib0..ib7 up, bond0 + VLANs present
ibstat                                   # HCA ports, LinkUp, rate (NDR = 400 Gb/s)
ibv_devinfo                              # verbs devices present
ibping / ib_write_bw                     # RDMA traffic between hosts (needs a peer + SM)
```

> A **subnet manager** (typically `opensm` on the Quantum-2 switch) must be
> running on the fabric for IB links to reach `LinkUp`/`Active`. The host
> normally does not run `opensm`.
