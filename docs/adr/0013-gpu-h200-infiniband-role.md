# ADR-0013: GPU roles gpu-b200 and gpu-h200 (RoCE vs InfiniBand backends)

- **Status:** Accepted
- **Date:** 2026-05-26
- **Decider(s):** host-config maintainers

## Context

The renderer shipped with two roles: `cpu` (N-S only) and `gpu-b300`
(N-S + 8 RoCE-over-Ethernet east-west underlays, with Soft-RoCE as the lab
substrate). Two more GPU platforms need first-boot configs:

- **HGX B200** — topologically identical to the B300 (2 N-S NICs + 8
  ConnectX RoCE east-west NICs). It needs to be a distinct role so the
  renderer can pin different driver/firmware behavior later without
  disturbing the B300 byte-stable goldens.
- **HGX H200** — its east-west compute fabric is **InfiniBand (NDR)**, not
  RoCE-over-Ethernet. This is a genuinely different transport: native HCA
  RDMA (no Soft-RoCE substrate), IPoIB for control-plane IP, no Netplan
  `virtual-function-count`, and IB rails are not LACP-bonded.

The east-west subsystem is the only axis that varies; the N-S subsystem
(2 NICs → LACP bond0 → mgmt/storage/ingress VLANs) is identical across all
GPU roles.

## Decision

Add two roles to `Role`: `gpu-b200` and `gpu-h200`.

- **gpu-b200** reuses the existing `RoceUnderlay` model and a template tree
  that mirrors `gpu-b300`. Both B-series roles run **RoCEv2** (routable,
  UDP/4791): the template creates the rxe devices (Soft-RoCE in the lab,
  hardware RoCE on ConnectX in production) and pins each device's rdma_cm
  `default_roce_mode` to `RoCE v2` so the transport is unambiguous rather
  than dependent on the kernel default. The role-count invariant requires
  exactly 8 RoCE underlays.
- **gpu-h200** introduces a new `InfinibandUnderlay` interface model and a
  new `ib_underlays` field on `HostIntent`. A host carries one east-west
  kind or the other, never both; the `check_roce_count_for_role` /
  `check_ib_count_for_role` invariants enforce the split (h200 → 8 IB, 0
  RoCE; the RoCE roles → 8 RoCE, 0 IB; cpu → neither).

`InfinibandUnderlay` deliberately does **not** extend `SriovParent`:
InfiniBand RDMA is native to the HCA and IB SR-IOV is configured
out-of-band (subnet manager + HCA firmware), not via first-boot Netplan, so
there is no `virtual-function-count` to model or emit.

## Consequences

- **Easier:** a new GPU platform is now "add a `Role` value + a template
  tree (+ a fixture/factory)"; the loader, emitter, service, and metrics
  are all role-generic and needed no per-role changes.
- **Easier:** the RoCE/IB split is enforced at model-construction time, so
  a mis-tagged host fails fast with a typed `InvariantError`
  (`roce-count-gpu-h200`, `ib-count-gpu-b300`, …) rather than producing a
  silently-wrong seed.
- **Harder:** gpu-b200 templates are byte-for-byte structural copies of
  gpu-b300; a change to the RoCE bring-up must be mirrored in both trees
  (the "one tree per role" convention trades DRY for per-role freedom).
- **Risks introduced:** the H200 IB path is not end-to-end testable in the
  QEMU lab (no InfiniBand hardware; IPoIB needs a real HCA + a fabric
  subnet manager). It is covered by unit + golden + structural tests, and a
  hand-authored sample seed (`docs/artifacts/h200-infiniband-sample/`), but
  not by the live VM e2e the RoCE roles enjoy.
- **Triggers for re-evaluation:** if IB SR-IOV (VFs for GPU pods) becomes a
  first-boot requirement, `InfinibandUnderlay` gains a VF field and the
  template emits the appropriate `/sys` or netplan config.

## InfiniBand modelling notes

- **IPoIB lives under Netplan `ethernets:`** — Netplan v2 has no
  first-class `infinibands` key; networkd assigns IP to an IPoIB link the
  same way it does an Ethernet link.
- **Placeholder MAC caveat:** real ConnectX IPoIB ports present a 20-byte
  hardware address / port GUID, not a 6-byte Ethernet MAC. The model keeps
  the inherited 6-byte `mac` field as an identifier so IB and Ethernet
  underlays template uniformly; the rendered network-config carries a note
  that hardware matching should be by name/GUID (`match: {name: "ibp*s0"}`
  or a GUID-keyed udev/.link rule).
- **MTU 2044** is the IPoIB datagram-mode default; connected mode (higher
  MTU) is left out-of-band.

## Alternatives Considered

- **Reuse `roce_underlays` for IB with a `fabric` discriminator.** Rejected:
  RoCE-specific fields (`sriov_vfs`, RoCE TC) don't apply to IB, and a
  discriminated union complicates the templates and the canonicalizer for
  no real gain over a separate field + invariant.
- **One shared `gpu` template tree parameterised by fabric.** Rejected:
  conflicts with the established "one template tree per role" convention
  (ADR-0011 / M2-3) and couples B-series and H-series bring-up changes.
- **Skip gpu-b200; alias it to gpu-b300.** Rejected: a distinct role is
  cheap and lets the platforms' driver/firmware pins diverge later without
  touching b300's goldens.

## References

- Implementation plan: §1 (east-west zone), §M2 (renderer), §7 (observability).
- Related ADRs: ADR-0003 (FastAPI/Pydantic/Jinja), ADR-0011 (systems overview / one-tree-per-role).
- Sample seed: `docs/artifacts/h200-infiniband-sample/`.
