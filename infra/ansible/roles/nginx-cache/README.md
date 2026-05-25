# nginx-cache

Ansible role that deploys an nginx caching reverse-proxy in front of the
[host-config renderer](../../../..).

## What it does

- Installs nginx from the system package.
- Writes a `proxy_cache_path` configuration that stores renderer responses
  under `nginx_cache_path` for up to `nginx_cache_inactive` and obeys
  `nginx_cache_max_size`.
- Proxies all `/v1/` requests to the renderer; cache hits serve from disk,
  cache misses are fetched from `nginx_cache_renderer_upstream`.
- Caches HTTP 200 responses for `nginx_cache_valid_seconds` (default 300 s),
  matching the renderer's `Cache-Control: max-age=300`.
- Adds `X-Cache-Status` to responses so operators can confirm HIT/MISS/BYPASS.
- Exposes a PURGE location at `nginx_cache_purge_path` for point-in-time
  cache invalidation without an nginx reload.
- Passes `/healthz`, `/readyz`, and `/metrics` through without caching.

## Role variables

| Variable | Default | Description |
|----------|---------|-------------|
| `nginx_cache_renderer_upstream` | `http://127.0.0.1:8080` | Renderer host:port nginx proxies to |
| `nginx_cache_path` | `/var/cache/host-config/seeds` | Disk path for the cache |
| `nginx_cache_keys_zone` | `seedcache:10m` | Shared-memory zone for cache metadata |
| `nginx_cache_inactive` | `24h` | Evict entries not accessed within this window |
| `nginx_cache_max_size` | `1g` | Total disk quota |
| `nginx_cache_valid_seconds` | `300` | How long to serve cached 200 responses |
| `nginx_cache_listen_port` | `80` | HTTP listen port |
| `nginx_cache_server_name` | `_` | nginx `server_name` (catch-all by default) |
| `nginx_cache_purge_path` | `/_purge` | Location prefix for PURGE requests |
| `nginx_cache_access_log` | `/var/log/nginx/host-config-access.log` | Access log path |
| `nginx_cache_error_log` | `/var/log/nginx/host-config-error.log` | Error log path |
| `nginx_cache_log_level` | `warn` | nginx error log level |

## Requirements

- Ubuntu Jammy (22.04) or Noble (24.04).
- The renderer must be reachable at `nginx_cache_renderer_upstream` before or
  immediately after nginx starts (nginx will proxy-pass to it; it does not need
  to be up at install time).

## Example playbook

```yaml
- hosts: seed_servers
  roles:
    - role: nginx-cache
      vars:
        nginx_cache_renderer_upstream: "http://127.0.0.1:8080"
        nginx_cache_listen_port: 80
```

## Cache purge

To invalidate a single host's entry:

```bash
curl -X PURGE http://<seed-server>/_purge/v1/render/<asset_tag>/meta-data
curl -X PURGE http://<seed-server>/_purge/v1/render/<asset_tag>/user-data
curl -X PURGE http://<seed-server>/_purge/v1/render/<asset_tag>/network-config
```

## Idempotency

Running the role twice on the same host produces `changed=0` on the second run
(assuming no variable changes). The `nginx -t` validate step catches template
errors before the reload handler fires.
