import pytest

from campusnet.addressing import find_duplicate_addresses, ip_in_subnet, is_valid_cidr, is_valid_ipv4


@pytest.mark.parametrize("address", ["10.10.1.11", "0.0.0.0", "255.255.255.255", "192.168.0.1"])
def test_valid_ipv4_is_accepted(address: str) -> None:
    assert is_valid_ipv4(address)


@pytest.mark.parametrize(
    "address",
    ["", "10.10.1", "10.10.1.256", "10.10.1.1.1", "a.b.c.d", "10.10.1.01", "10.10.1.1/24", " 10.10.1.1", "::1", None, 5],
)
def test_invalid_ipv4_is_rejected(address: object) -> None:
    assert not is_valid_ipv4(address)


def test_valid_cidr_is_accepted() -> None:
    assert is_valid_cidr("10.10.0.0/16")
    assert is_valid_cidr("10.10.1.0/24")


@pytest.mark.parametrize("cidr", ["10.10.1.0", "10.10.1.5/24", "10.10.1.0/33", "10.10.1.0/", "bad/24", None])
def test_invalid_cidr_is_rejected(cidr: object) -> None:
    assert not is_valid_cidr(cidr)


def test_ip_in_subnet() -> None:
    assert ip_in_subnet("10.10.1.11", "10.10.1.0/24")
    assert not ip_in_subnet("10.10.2.11", "10.10.1.0/24")
    assert not ip_in_subnet("bad", "10.10.1.0/24")
    assert not ip_in_subnet("10.10.1.11", "bad")


def test_duplicate_addresses_are_flagged() -> None:
    assert find_duplicate_addresses(["10.0.0.1", "10.0.0.2", "10.0.0.1", "10.0.0.3", "10.0.0.2"]) == [
        "10.0.0.1",
        "10.0.0.2",
    ]


def test_no_duplicates_returns_empty() -> None:
    assert find_duplicate_addresses(["10.0.0.1", "10.0.0.2"]) == []
    assert find_duplicate_addresses([]) == []
