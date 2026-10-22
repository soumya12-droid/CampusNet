"""Topology container, default campus topology, validation and failure state."""

import math
from dataclasses import dataclass, field

from campusnet.addressing import find_duplicate_addresses, ip_in_subnet, is_valid_cidr, is_valid_ipv4
from campusnet.config import DEFAULT_QUEUE_CAPACITY_PACKETS
from campusnet.models import NODE_TYPES, Link, Node


@dataclass
class Topology:
    nodes: dict[str, Node] = field(default_factory=dict)
    links: dict[str, Link] = field(default_factory=dict)
    failed_routers: set[str] = field(default_factory=set)

    def add_node(self, node: Node) -> None:
        if node.node_id in self.nodes:
            raise ValueError(f"duplicate node_id {node.node_id!r}")
        self.nodes[node.node_id] = node

    def add_link(self, link: Link) -> None:
        if link.link_id in self.links:
            raise ValueError(f"duplicate link_id {link.link_id!r}")
        self.links[link.link_id] = link

    def links_of(self, node_id: str) -> list[Link]:
        return [link for link in self.links.values() if node_id in (link.source, link.target)]


def build_default_topology() -> Topology:
    """Starter campus: 4 students + classroom client, 2 switches, 2 routers, 2 servers.

    Primary path from either switch to the servers is via r1 and the r1-r2 core
    link (the bottleneck). Each switch also has a higher-cost backup uplink
    straight to r2, so a core failure still leaves a route. The server links are
    single links (bridges): losing one disconnects that server.
    """
    topology = Topology()
    lan1, lan2, core, servers = "10.10.1.0/24", "10.10.2.0/24", "10.10.0.0/24", "10.10.3.0/24"
    for node in [
        Node("stu1", "Student 1", "student_device", "10.10.1.11", lan1),
        Node("stu2", "Student 2", "student_device", "10.10.1.12", lan1),
        Node("stu3", "Student 3", "student_device", "10.10.2.11", lan2),
        Node("stu4", "Student 4", "student_device", "10.10.2.12", lan2),
        Node("classroom", "Classroom Client", "student_device", "10.10.2.20", lan2),
        Node("sw1", "Access Switch 1", "switch"),
        Node("sw2", "Access Switch 2", "switch"),
        Node("r1", "Router 1", "router", "10.10.0.1", core),
        Node("r2", "Router 2", "router", "10.10.0.2", core),
        Node("portal", "Learning Portal", "server", "10.10.3.10", servers),
        Node("files", "File Server", "server", "10.10.3.11", servers),
    ]:
        topology.add_node(node)

    def connect(a: str, b: str, bandwidth_bps: float, propagation_s: float, cost: float) -> None:
        topology.add_link(
            Link(f"{a}-{b}", a, b, bandwidth_bps, propagation_s, cost, DEFAULT_QUEUE_CAPACITY_PACKETS)
        )

    for host, switch in [("stu1", "sw1"), ("stu2", "sw1"), ("stu3", "sw2"), ("stu4", "sw2"), ("classroom", "sw2")]:
        connect(host, switch, 100e6, 0.0001, 1)  # access links
    connect("sw1", "r1", 100e6, 0.0005, 1)  # primary uplinks
    connect("sw2", "r1", 100e6, 0.0005, 1)
    connect("r1", "r2", 10e6, 0.002, 1)  # core / bottleneck
    connect("sw1", "r2", 10e6, 0.002, 3)  # backup uplinks
    connect("sw2", "r2", 10e6, 0.002, 3)
    connect("r2", "portal", 100e6, 0.0001, 1)  # server links (bridges)
    connect("r2", "files", 100e6, 0.0001, 1)
    return topology


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def validate_topology(topology: Topology) -> list[str]:
    """Return a list of problems found; an empty list means the topology is valid."""
    problems: list[str] = []

    for key, node in topology.nodes.items():
        if key != node.node_id:
            problems.append(f"node key {key!r} does not match node_id {node.node_id!r}")
        if node.node_type not in NODE_TYPES:
            problems.append(f"node {node.node_id}: invalid node_type {node.node_type!r}")
        if node.ip_address is not None and not is_valid_ipv4(node.ip_address):
            problems.append(f"node {node.node_id}: invalid IPv4 address {node.ip_address!r}")
        if node.subnet is not None and not is_valid_cidr(node.subnet):
            problems.append(f"node {node.node_id}: invalid subnet {node.subnet!r}")
        elif (
            node.ip_address is not None
            and node.subnet is not None
            and is_valid_ipv4(node.ip_address)
            and not ip_in_subnet(node.ip_address, node.subnet)
        ):
            problems.append(f"node {node.node_id}: {node.ip_address} is outside subnet {node.subnet}")

    for duplicate in find_duplicate_addresses(n.ip_address for n in topology.nodes.values() if n.ip_address):
        problems.append(f"duplicate IPv4 address {duplicate}")

    seen_pairs: dict[frozenset[str], str] = {}
    for key, link in topology.links.items():
        name = f"link {link.link_id}"
        if key != link.link_id:
            problems.append(f"link key {key!r} does not match link_id {link.link_id!r}")
        for endpoint in (link.source, link.target):
            if endpoint not in topology.nodes:
                problems.append(f"{name}: unknown node {endpoint!r}")
        if link.source == link.target:
            problems.append(f"{name}: source and target are the same node")
        elif link.source in topology.nodes and link.target in topology.nodes:
            pair = frozenset((link.source, link.target))
            if pair in seen_pairs:
                problems.append(f"{name}: parallel to link {seen_pairs[pair]} (not supported)")
            else:
                seen_pairs[pair] = link.link_id
        if not _is_number(link.bandwidth_bps) or link.bandwidth_bps <= 0:
            problems.append(f"{name}: bandwidth_bps must be > 0")
        if not _is_number(link.propagation_delay_s) or link.propagation_delay_s < 0:
            problems.append(f"{name}: propagation_delay_s must be >= 0")
        if not _is_number(link.routing_cost) or link.routing_cost < 0:
            problems.append(f"{name}: routing_cost must be >= 0")
        if (
            not isinstance(link.queue_capacity_packets, int)
            or isinstance(link.queue_capacity_packets, bool)
            or link.queue_capacity_packets < 0
        ):
            problems.append(f"{name}: queue_capacity_packets must be an integer >= 0")
        if not isinstance(link.is_up, bool):
            problems.append(f"{name}: is_up must be a bool")

    for router_id in topology.failed_routers:
        node = topology.nodes.get(router_id)
        if node is None or node.node_type != "router":
            problems.append(f"failed_routers contains {router_id!r}, which is not a router")

    return problems


def disable_link(topology: Topology, link_id: str) -> None:
    """Mark a link as down (both directions)."""
    if link_id not in topology.links:
        raise ValueError(f"unknown link {link_id!r}")
    topology.links[link_id].is_up = False


def disable_router(topology: Topology, node_id: str) -> None:
    """Mark a router as failed and take all of its attached links down."""
    node = topology.nodes.get(node_id)
    if node is None:
        raise ValueError(f"unknown node {node_id!r}")
    if node.node_type != "router":
        raise ValueError(f"node {node_id!r} is a {node.node_type}, not a router")
    topology.failed_routers.add(node_id)
    for link in topology.links_of(node_id):
        link.is_up = False
