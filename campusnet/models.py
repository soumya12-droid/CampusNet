"""Shared data classes. Field names follow the specification (section 6.1)."""

from dataclasses import dataclass, field
from typing import Literal, get_args

NodeType = Literal["student_device", "switch", "router", "server"]
TrafficClass = Literal["realtime", "interactive", "bulk"]
TransportMode = Literal["tcp_like", "udp_like"]
PacketStatus = Literal["created", "queued", "in_transit", "delivered", "dropped"]

NODE_TYPES: tuple[str, ...] = get_args(NodeType)
TRAFFIC_CLASSES: tuple[str, ...] = get_args(TrafficClass)
TRANSPORT_MODES: tuple[str, ...] = get_args(TransportMode)


@dataclass(frozen=True)
class Node:
    node_id: str
    name: str
    node_type: NodeType
    ip_address: str | None = None
    subnet: str | None = None


@dataclass
class Link:
    """A bidirectional point-to-point link between two nodes."""

    link_id: str
    source: str
    target: str
    bandwidth_bps: float
    propagation_delay_s: float
    routing_cost: float
    queue_capacity_packets: int
    is_up: bool = True


@dataclass
class Packet:
    packet_id: int | str
    source_id: str
    destination_id: str
    size_bytes: int
    traffic_class: TrafficClass
    transport_mode: TransportMode
    created_at_s: float
    status: PacketStatus = "created"
    path: list[str] = field(default_factory=list)  # ordered node IDs
    drop_reason: str | None = None
    delivered_at_s: float | None = None


@dataclass
class Flow:
    flow_id: str
    source_id: str
    destination_id: str
    traffic_class: TrafficClass
    transport_mode: TransportMode
    packet_size_bytes: int
    start_time_s: float
    end_time_s: float
    packet_interval_s: float


@dataclass
class PacketRecord:
    """One metrics/log event. packet_id is None for events not about a packet (e.g. a link failure)."""

    packet_id: int | str | None
    event_type: str
    timestamp_s: float
    location: str | None = None  # node or link ID
    queue_length: int | None = None  # packets waiting at the output port after this event
    drop_reason: str | None = None
    sender: str | None = None  # for output-port events: the node sending on `location`, i.e. which direction
