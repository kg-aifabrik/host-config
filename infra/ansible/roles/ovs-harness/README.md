# ovs-harness

Ansible role that installs Open vSwitch and creates the `br-test` test bridge
with LACP partner config, VLAN trunks 100/200/300, and tap interfaces for VM
attachment. Part of the M4 QEMU lab harness.

## What it does

- Installs `openvswitch-switch` and `openvswitch-common`.
- Creates OVS bridge `br-test` (idempotent via `--may-exist`).
- Pre-creates tap interfaces `tap-nsa` and `tap-nsb` (N-S NICs; E-W RoCE taps
  added in M5).
- Attaches taps to the bridge with VLAN trunks 100, 200, and 300.
- Asserts the bridge exists before exiting.

## Test-time topology

```
           OVS br-test
          ┌────────────────────┐
          │  tap-nsa  tap-nsb  │ ← VM virtio NICs attach here
          │      │        │    │
          │  VLAN trunks       │
          │  100 (mgmt)        │
          │  200 (storage)     │
          │  300 (ingress)     │
          └────────────────────┘
```

See [`docs/diagrams/systems-overview.svg`](../../../../docs/diagrams/systems-overview.svg)
for the full test-time topology.

## Role variables

| Variable | Default | Description |
|----------|---------|-------------|
| `ovs_bridge_name` | `br-test` | OVS bridge name |
| `ovs_bond_lacp` | `active` | LACP mode (active / passive / off) |
| `ovs_bond_mode` | `balance-tcp` | OVS bond mode |
| `ovs_vlan_trunks` | `[100, 200, 300]` | VLAN IDs trunked through all tap ports |
| `ovs_tap_interfaces` | `[tap-nsa, tap-nsb]` | Tap interfaces pre-created for VM attachment |

## Idempotency

`ovs-vsctl --may-exist` makes bridge and port creation no-ops on subsequent
runs. Tap creation checks `ip link show` before `ip tuntap add`. Second apply
produces `changed=0`.

## Requirements

- Ubuntu Jammy (22.04) or Noble (24.04).
- Root or sudo access (`become: true`).
