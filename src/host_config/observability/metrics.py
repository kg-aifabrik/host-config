"""Prometheus metrics for the host-config renderer service.

The metric set is the contract in §7.6 of the implementation plan:

Counters:
    host_config_requests_total{method, path, status}
    host_config_renders_total{role, outcome}
        outcome ∈ {success, validation_error, netbox_error, template_error}
    host_config_cache_events_total{type}
        type ∈ {hit, miss, evict, error}

Histograms:
    host_config_request_duration_seconds{method, path}
    host_config_netbox_query_duration_seconds{endpoint}
    host_config_render_duration_seconds{role}

Gauge:
    host_config_active_requests

Naming convention (CODE_CONVENTIONS §7.6):
    ``host_config_<subsystem>_<metric>_<unit>``.

WHY the ``path`` label is the *route template* (``/v1/render/{asset_tag}/
{file_kind}``) and never the concrete path: the concrete path embeds the
asset tag, which is unbounded — using it would explode Prometheus
cardinality. The middleware resolves the matched route template before
labelling. See ``service/middleware.py``.

WHY cache_events_total is defined here but not incremented by the
renderer: the cache tier is nginx (M3-1), which serves warm hits without
ever reaching this process — so the renderer cannot observe them. The
series is declared for contract completeness and is populated by the
nginx layer / a future log exporter, not here. A labelled counter that is
never incremented simply does not appear on ``/metrics`` until first use,
so declaring it is harmless.

WHY module-level singletons + ``_safe_*`` guards: Prometheus requires
each collector be registered exactly once per process. Module-level
definition guarantees that in production (single uvicorn process). The
guards return the already-registered collector if the module is
re-imported in the same process (e.g. ``importlib.reload`` across test
sessions), instead of raising ``ValueError: Duplicated timeseries``.
"""

from __future__ import annotations

from prometheus_client import REGISTRY, Counter, Gauge, Histogram


def _safe_counter(name: str, doc: str, labels: list[str]) -> Counter:
    """Create a Counter, returning the existing one on duplicate registration."""
    try:
        return Counter(name, doc, labels)
    except ValueError:
        return REGISTRY._names_to_collectors[name]  # type: ignore[return-value]


def _safe_histogram(
    name: str, doc: str, labels: list[str], *, buckets: tuple[float, ...] | None = None
) -> Histogram:
    """Create a Histogram, returning the existing one on duplicate registration."""
    try:
        if buckets is not None:
            return Histogram(name, doc, labels, buckets=buckets)
        return Histogram(name, doc, labels)
    except ValueError:
        return REGISTRY._names_to_collectors[name]  # type: ignore[return-value]


def _safe_gauge(name: str, doc: str) -> Gauge:
    """Create a Gauge, returning the existing one on duplicate registration."""
    try:
        return Gauge(name, doc)
    except ValueError:
        return REGISTRY._names_to_collectors[name]  # type: ignore[return-value]


# Sub-second-dominated latency buckets: the render path is a Netbox query
# (tens of ms) + Jinja render (sub-ms). Default Prometheus buckets
# under-resolve the 1-50 ms band we care about.
_LATENCY_BUCKETS: tuple[float, ...] = (
    0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0,
)

# ---------------------------------------------------------------------------
# HTTP-level (middleware).
# ---------------------------------------------------------------------------

REQUESTS_TOTAL: Counter = _safe_counter(
    "host_config_requests_total",
    "Total HTTP requests, by method, matched route template, and status code.",
    ["method", "path", "status"],
)

REQUEST_DURATION: Histogram = _safe_histogram(
    "host_config_request_duration_seconds",
    "End-to-end HTTP request duration, by method and matched route template.",
    ["method", "path"],
    buckets=_LATENCY_BUCKETS,
)

ACTIVE_REQUESTS: Gauge = _safe_gauge(
    "host_config_active_requests",
    "Number of HTTP requests currently in flight.",
)

# ---------------------------------------------------------------------------
# Render-level (route handler).
# ---------------------------------------------------------------------------

RENDERS_TOTAL: Counter = _safe_counter(
    "host_config_renders_total",
    "Total renders, by host role and outcome "
    "(success|validation_error|netbox_error|template_error).",
    ["role", "outcome"],
)

RENDER_DURATION: Histogram = _safe_histogram(
    "host_config_render_duration_seconds",
    "Render duration (Netbox load + template emit), by host role.",
    ["role"],
    buckets=_LATENCY_BUCKETS,
)

# ---------------------------------------------------------------------------
# Netbox client.
# ---------------------------------------------------------------------------

NETBOX_QUERY_DURATION: Histogram = _safe_histogram(
    "host_config_netbox_query_duration_seconds",
    "Duration of Netbox queries, by logical endpoint.",
    ["endpoint"],
    buckets=_LATENCY_BUCKETS,
)

# ---------------------------------------------------------------------------
# Cache tier (nginx — declared for contract completeness; see module docstring).
# ---------------------------------------------------------------------------

CACHE_EVENTS_TOTAL: Counter = _safe_counter(
    "host_config_cache_events_total",
    "Cache events by type (hit|miss|evict|error). Populated by the nginx "
    "cache tier / log exporter, not by the renderer process.",
    ["type"],
)


__all__ = [
    "ACTIVE_REQUESTS",
    "CACHE_EVENTS_TOTAL",
    "NETBOX_QUERY_DURATION",
    "RENDERS_TOTAL",
    "RENDER_DURATION",
    "REQUESTS_TOTAL",
    "REQUEST_DURATION",
]
