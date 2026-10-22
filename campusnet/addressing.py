"""IPv4 / CIDR validation helpers (IPv4 only in version 1)."""

import ipaddress
from collections import Counter
from collections.abc import Iterable


def is_valid_ipv4(address: object) -> bool:
    if not isinstance(address, str):
        return False
    try:
        ipaddress.IPv4Address(address)
    except ValueError:
        return False
    return True


def is_valid_cidr(cidr: object) -> bool:
    """True for an IPv4 network such as '10.10.1.0/24' with no host bits set."""
    if not isinstance(cidr, str) or "/" not in cidr:
        return False
    try:
        ipaddress.IPv4Network(cidr, strict=True)
    except ValueError:
        return False
    return True


def ip_in_subnet(address: str, cidr: str) -> bool:
    """True if both inputs are valid and the address lies inside the subnet."""
    if not (is_valid_ipv4(address) and is_valid_cidr(cidr)):
        return False
    return ipaddress.IPv4Address(address) in ipaddress.IPv4Network(cidr)


def find_duplicate_addresses(addresses: Iterable[str]) -> list[str]:
    """Return each address that appears more than once, sorted."""
    counts = Counter(addresses)
    return sorted(address for address, count in counts.items() if count > 1)
