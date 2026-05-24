"""host-config: host network configuration pipeline.

This package contains the renderer service that turns a Netbox device record
into a cloud-init seed (meta-data, user-data, network-config) for a host.

See `docs/architecture/systems-overview.md` for the high-level picture and
the implementation plan referenced in `README.md` for milestone tracking.
"""

__version__ = "0.0.0"
__all__ = ["__version__"]
