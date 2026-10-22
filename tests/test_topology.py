import pytest

from campusnet.models import Link, Node
from campusnet.topology import Topology, build_default_topology, validate_topology


def _two_node_topology() -> Topology:
    topology = Topology()
    topology.add_node(Node("a", "A", "student_device", "10.0.0.1", "10.0.0.0/24"))
    topology.add_node(Node("b", "B", "server", "10.0.0.2", "10.0.0.0/24"))
    topology.add_link(Link("a-b", "a", "b", 1e6, 0.001, 1, 10))
    return topology


def test_default_topology_is_valid() -> None:
    assert validate_topology(build_default_topology()) == []


def test_default_topology_has_specified_devices() -> None:
    nodes = build_default_topology().nodes.values()
    counts = {t: sum(1 for n in nodes if n.node_type == t) for t in ("student_device", "switch", "router", "server")}
    assert counts == {"student_device": 5, "switch": 2, "router": 2, "server": 2}  # 4 students + classroom client


def test_default_topology_ips_are_unique_private_ipv4() -> None:
    ips = [n.ip_address for n in build_default_topology().nodes.values() if n.ip_address]
    assert len(ips) == len(set(ips))
    assert all(ip.startswith("10.10.") for ip in ips)


def test_default_topology_is_a_fresh_object_each_call() -> None:
    first, second = build_default_topology(), build_default_topology()
    first.links["r1-r2"].is_up = False
    assert second.links["r1-r2"].is_up


def test_duplicate_ids_are_rejected() -> None:
    topology = _two_node_topology()
    with pytest.raises(ValueError):
        topology.add_node(Node("a", "again", "switch"))
    with pytest.raises(ValueError):
        topology.add_link(Link("a-b", "a", "b", 1e6, 0.001, 1, 10))


def test_invalid_ip_is_reported() -> None:
    topology = _two_node_topology()
    topology.nodes["a"] = Node("a", "A", "student_device", "10.0.0.300")
    assert any("invalid IPv4" in p for p in validate_topology(topology))


def test_duplicate_ip_is_reported() -> None:
    topology = _two_node_topology()
    topology.nodes["b"] = Node("b", "B", "server", "10.0.0.1")
    assert any("duplicate IPv4" in p for p in validate_topology(topology))


def test_ip_outside_subnet_is_reported() -> None:
    topology = _two_node_topology()
    topology.nodes["a"] = Node("a", "A", "student_device", "10.9.9.9", "10.0.0.0/24")
    assert any("outside subnet" in p for p in validate_topology(topology))


def test_invalid_subnet_is_reported() -> None:
    topology = _two_node_topology()
    topology.nodes["a"] = Node("a", "A", "student_device", "10.0.0.1", "10.0.0.1/24")
    assert any("invalid subnet" in p for p in validate_topology(topology))


def test_invalid_node_type_is_reported() -> None:
    topology = _two_node_topology()
    topology.nodes["a"] = Node("a", "A", "toaster")  # type: ignore[arg-type]
    assert any("invalid node_type" in p for p in validate_topology(topology))


@pytest.mark.parametrize(
    ("field_name", "value", "fragment"),
    [
        ("bandwidth_bps", 0, "bandwidth_bps"),
        ("bandwidth_bps", -5, "bandwidth_bps"),
        ("propagation_delay_s", -0.1, "propagation_delay_s"),
        ("routing_cost", -1, "routing_cost"),
        ("queue_capacity_packets", -1, "queue_capacity_packets"),
        ("queue_capacity_packets", 2.5, "queue_capacity_packets"),
        ("bandwidth_bps", float("nan"), "bandwidth_bps"),
    ],
)
def test_bad_link_attributes_are_reported(field_name: str, value: float, fragment: str) -> None:
    topology = _two_node_topology()
    setattr(topology.links["a-b"], field_name, value)
    assert any(fragment in p for p in validate_topology(topology))


def test_zero_cost_delay_and_queue_capacity_are_valid() -> None:
    topology = _two_node_topology()
    link = topology.links["a-b"]
    link.routing_cost, link.propagation_delay_s, link.queue_capacity_packets = 0, 0, 0
    assert validate_topology(topology) == []


def test_unknown_endpoint_self_loop_and_parallel_links_are_reported() -> None:
    topology = _two_node_topology()
    topology.add_link(Link("a-x", "a", "x", 1e6, 0.001, 1, 10))
    topology.add_link(Link("a-a", "a", "a", 1e6, 0.001, 1, 10))
    topology.add_link(Link("b-a", "b", "a", 1e6, 0.001, 1, 10))
    problems = validate_topology(topology)
    assert any("unknown node 'x'" in p for p in problems)
    assert any("same node" in p for p in problems)
    assert any("parallel" in p for p in problems)
