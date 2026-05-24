# ADR-0009: structlog + Prometheus for observability

- **Status:** Accepted
- **Date:** 2026-05-23
- **Decider(s):** Karthik Gajjala

## Context

Engineering principle #5: logs are the post-mortem evidence. From day one, the renderer service produces structured logs and Prometheus metrics — debugging affordances baked into the design, not retrofitted.

Three observability concerns to consider:

1. **Logs** — narrative of what happened.
2. **Metrics** — aggregated counts, distributions, gauges.
3. **Traces** — distributed request lineage across services.

## Decision

- **`structlog`** for logs. JSON in production, console-friendly in dev. All modules use `structlog.get_logger(__name__)`; configuration lives in `src/host_config/logging_config.py` as the single source of truth.
- **`prometheus-client`** for metrics. Exposed at `/metrics` in standard Prometheus text exposition format; no agent dependency.
- **Distributed tracing (OpenTelemetry) is deferred.** Adds value when there's a second service to correlate with; for a single-service system, structlog correlation IDs (`request_id`, `asset_tag`, `render_id`) already let an engineer follow a request end-to-end through the one process we have.

## Observability primitives this ADR establishes

- **Correlation IDs** bound via `structlog.contextvars.bind_contextvars` in middleware so every log line in a request scope inherits `request_id`, `asset_tag`, etc.
- **Log levels** with disciplined use (TRACE/DEBUG/INFO/WARN/ERROR/CRITICAL) — see [CODE_CONVENTIONS.md §9](../../CODE_CONVENTIONS.md#9-observability).
- **Debug-level traceability** acceptance criterion: with `LOG_LEVEL=DEBUG`, a single request to a render endpoint produces logs that let an engineer reconstruct the full journey (request received → cache check → Netbox query → model build → template render → response).
- **Metrics named with rationale** — every metric in `metrics.py` documented with the question it answers.

## Consequences

**Easier:**
- Structured logs are searchable in any log aggregator (Loki, Splunk, Cloudwatch).
- Prometheus text format is the de-facto standard; any scraper works.
- Adding OTel later is additive (instrumented spans don't conflict with existing logs).

**Harder:**
- structlog's pattern (key=value not f-strings) is a discipline contributors must internalize.

**Risks introduced:**
- Without distributed tracing, cross-service debugging is harder — but we have one service.

**Triggers for re-evaluation:**
- When a second service joins the system (CNI module, orchestrator). Add OpenTelemetry instrumentation; structlog correlation IDs become trace IDs.

## Alternatives Considered

- **Python stdlib `logging`** — works but key=value formatting requires opt-in handlers; structlog's processor pipeline is cleaner.
- **`loguru`** — friendly API but its structured-logging story is less mature than structlog's.
- **OpenTelemetry from day one** — overkill for a single service; would still need structlog or equivalent for log formatting anyway.

## References

- Plan §7 (Observability strategy), §7.7 (deferred tracing).
- structlog docs: https://www.structlog.org/
- prometheus-client docs: https://prometheus.github.io/client_python/
- CODE_CONVENTIONS.md §9.
- Related ADRs: 0003 (FastAPI — instrumented in middleware).
