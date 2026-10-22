from campusnet.models import Link, Node, Packet
from campusnet.topology import Topology


def make_packet(
    packet_id: int | str,
    traffic_class: str = "interactive",
    size_bytes: int = 1000,
    created_at_s: float = 0.0,
) -> Packet:
    return Packet(packet_id, "a", "b", size_bytes, traffic_class, "udp_like", created_at_s)  # type: ignore[arg-type]


def build_topology(
    links: list[tuple[str, str, float, float, float]], routers: tuple[str, ...] = ()
) -> Topology:
    """links are (a, b, bandwidth_bps, propagation_s, cost); link ID is 'a-b'. Queue capacity is set per run."""
    topology = Topology()
    for node_id in sorted({n for a, b, *_ in links for n in (a, b)}):
        topology.add_node(Node(node_id, node_id, "router" if node_id in routers else "student_device"))
    for a, b, bandwidth_bps, propagation_s, cost in links:
        topology.add_link(Link(f"{a}-{b}", a, b, bandwidth_bps, propagation_s, cost, 10))
    return topology


def make_topology(edges: list[tuple[str, str, float]], routers: tuple[str, ...] = ()) -> Topology:
    """Build a small topology; node IDs come from the edges. Link ID is 'a-b'."""
    topology = Topology()
    node_ids = sorted({n for a, b, _ in edges for n in (a, b)})
    for node_id in node_ids:
        node_type = "router" if node_id in routers else "student_device"
        topology.add_node(Node(node_id, node_id, node_type))
    for a, b, cost in edges:
        topology.add_link(Link(f"{a}-{b}", a, b, 1e6, 0.001, cost, 10))
    return topology
