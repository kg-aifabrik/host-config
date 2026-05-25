# qemu-host

Ansible role that installs QEMU/KVM, libvirt, and libguestfs-tools on the
target host and configures KVM group permissions for the lab user.

## What it does

- Asserts `/dev/kvm` is present (optional, disable with `qemu_host_assert_kvm: false`).
- Installs `qemu-system-x86`, `libvirt-daemon-system`, `libvirt-clients`,
  `virtinst`, `libguestfs-tools`, and `python3-libvirt`.
- Adds `qemu_host_lab_user` to the `kvm` group (direct device access) and
  the `libvirt` group (management socket without sudo).
- Enables and starts `libvirtd`.

## Role variables

| Variable | Default | Description |
|----------|---------|-------------|
| `qemu_host_lab_user` | `{{ ansible_user }}` | OS user that will run QEMU/virsh |
| `qemu_host_assert_kvm` | `true` | Assert `/dev/kvm` is present; set `false` for CI hosts |
| `qemu_host_packages` | *(see defaults)* | List of packages to install |

## Requirements

- Ubuntu Jammy (22.04) or Noble (24.04).
- The CPU must have hardware virtualisation extensions (Intel VT-x / AMD-V)
  enabled in firmware, unless `qemu_host_assert_kvm: false`.

## Notes

Group membership (`kvm`, `libvirt`) takes effect on the next SSH login. Lab
automation scripts that need immediate access use `sg libvirt -c "..."`.
