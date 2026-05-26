# ADR-0012: Deferred Signed-Seed Delivery (TLS + HMAC)

- **Status:** Accepted
- **Date:** 2026-05-25
- **Decider(s):** Karthik Gajjala

## Context

Cloud-init seed files (`meta-data`, `user-data`, `network-config`) carry the
complete network identity of a host — IP addresses, MAC-to-name mappings,
SR-IOV VF counts, VLAN assignments. A compromised or spoofed seed file can
silently misconfigure any host that boots against it.

Three threat vectors are worth naming:

1. **Network-level MITM on the management VLAN.** cloud-init's NoCloud HTTP
   source fetches seeds over plain HTTP. An attacker with a foothold on the
   mgmt VLAN can intercept or replay responses.
2. **Renderer compromise.** The renderer process itself could be tampered
   with (supply-chain, CVE). A host has no way to distinguish a legitimate
   response from a maliciously crafted one.
3. **Cache poisoning.** nginx's `proxy_cache` serves responses from disk.
   A compromised cache entry persists until evicted (`inactive=24h`), even
   if the renderer is later patched.

For v1 (lab / pre-production environment) these risks are accepted because:

- All hosts that consume seeds and the seed server itself are on a
  physically separate management VLAN with no untrusted ingress.
- The lab environment has no production workloads; an incorrect seed causes
  a boot failure, not data exfiltration.
- Implementing TLS + mTLS correctly (certificate issuance, rotation,
  smallstep CA integration, cloud-init configuration to verify peer cert)
  carries meaningful operational overhead that would slow down the core
  renderer and IaC work.

## Decision

**Deliver seed files over plain HTTP on the management VLAN in v1.** No
TLS, no HMAC signatures on seed payloads, no client certificate requirement.

The nginx-cache config (`infra/ansible/roles/nginx-cache/templates/nginx.conf.j2`)
contains a commented-out TLS server block (`:443`) with self-signed-cert
directives. This block serves as a placeholder: it is syntactically valid
nginx but disabled, so a future contributor can uncomment, wire up real
certificates, and test without rebuilding the config from scratch.

The seams for signature verification on the renderer side are deliberately
reserved:

- `src/host_config/service/middleware.py` — `_inject_cache_headers` already
  produces a per-response SHA-256 ETag. Signing the body with an HMAC key
  (or embedding an `X-Seed-Signature` header) would be a small addition here.
- `src/host_config/service/app.py` — the lifespan event is the right place
  to load an HMAC signing key or TLS client cert.

Neither seam needs code today; the naming and placement are sufficient for a
future contributor to locate them.

## Consequences

- **Easier:** v1 deployment is operationally simple. cloud-init's built-in
  NoCloud HTTP fetcher works without custom certificates or CA bundles baked
  into the boot image.
- **Harder:** Promoting to a production environment where the management VLAN
  is shared with untrusted devices requires revisiting this decision before
  any host is enrolled.
- **Risks introduced:**
  - A compromised management VLAN allows seed replay or substitution.
    Blast radius: any host that reboots during the compromise window could
    receive a wrong network config, causing it to join the wrong VLAN or
    lose connectivity.
  - Cached entries in nginx (up to 24 h inactive) can perpetuate a poisoned
    seed. Mitigation: an `X-Purge` request header on the render URL forces an
    individual entry to refresh immediately (`proxy_cache_bypass`).
- **Triggers for re-evaluation:**
  - Any host outside the physically isolated mgmt VLAN is able to reach the
    seed server.
  - The lab transitions to carrying production traffic or sensitive workloads.
  - The boot image gains the ability to verify a peer TLS certificate
    (smallstep `step-ca` + `step` CLI injected via base image).

## Alternatives Considered

- **HTTPS with a self-signed CA baked into the boot image.** Provides
  server authentication at low operational cost. Rejected for v1 because
  it requires rebuilding the base image and distributing a CA bundle, both
  of which are M4+ (QEMU lab) concerns.
- **HMAC-signed payloads (`X-Seed-Signature: sha256=<hex>`).**  Provides
  integrity checking if cloud-init's user-data is extended to run a
  verification hook. Rejected for v1 because cloud-init does not natively
  verify custom headers; a verification hook would run after the seed is
  already applied.
- **mTLS via smallstep (`step-ca` + short-lived client certs).** Strong
  mutual authentication; each host presents a cert tied to its asset tag.
  Attractive for production. Rejected for v1 due to CA operational burden
  and cloud-init's lack of native mTLS support for the NoCloud HTTP datasource.

## References

- Implementation plan: §9 M3-4
- Related ADRs: ADR-0003 (FastAPI/Pydantic/Jinja service), ADR-0006 (Ansible IaC)
- nginx-cache role: `infra/ansible/roles/nginx-cache/`
- Middleware seam: `src/host_config/service/middleware.py` → `_inject_cache_headers`
- Smallstep CA (future reference): https://smallstep.com/docs/step-ca
