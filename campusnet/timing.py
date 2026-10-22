"""Link delay formulas (specification section 5.2). Seconds and bits per second internally."""

from campusnet.models import Link


def transmission_delay_s(size_bytes: int, bandwidth_bps: float) -> float:
    """Time to serialize a packet onto a link: (size_bytes * 8) / bandwidth_bps."""
    if size_bytes <= 0:
        raise ValueError("size_bytes must be > 0")
    if bandwidth_bps <= 0:
        raise ValueError("bandwidth_bps must be > 0")
    return (size_bytes * 8) / bandwidth_bps


def link_delay_s(size_bytes: int, link: Link) -> float:
    """Transmission delay plus the link's propagation delay (queueing excluded)."""
    return transmission_delay_s(size_bytes, link.bandwidth_bps) + link.propagation_delay_s
