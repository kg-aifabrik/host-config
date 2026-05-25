# tests/e2e — End-to-end tests requiring the full lab stack.
#
# These tests are marked @e2e and @requires_kvm; they are skipped
# by default in CI and only run on-demand on a host with:
#   - KVM acceleration (/dev/kvm)
#   - OVS bridge (via ovs-harness Ansible role)
#   - QEMU/libvirt (via qemu-host Ansible role)
#   - A running Netbox (via netbox-dev Ansible role)
#   - A running renderer + nginx-cache
