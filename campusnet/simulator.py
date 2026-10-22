"""Discrete-event simulation engine (specification sections 5 and 11, Phase 3).

Packet lifecycle (Packet.status): created -> queued / in_transit -> delivered | dropped.
Packets still queued or in transit when the simulation ends are "unresolved"; they are
never counted as dropped. Every generated packet gets exactly one of: a "delivered"
record, a "dropped" record, or neither (unresolved).

Failure rule for packets already in the network (paths are never recomputed):
  * A packet currently being transmitted on a link that fails finishes serialization
    and propagation, then continues along its fixed path.
  * Packets waiting in the output queues of a failed link are dropped at the failure
    time ("link_down"); for a router failure, on every link attached to the router
    ("router_down").
  * A packet that reaches a failed router is dropped ("router_down").
  * A packet whose next-hop link is down when it is ready to be forwarded is dropped
    ("link_down").
  * Packets created after a failure are routed on the updated topology; if no route
    exists they are created and immediately dropped ("no_route").

Simulation ends at config.duration_s: events at or before that time are processed,
later ones are not. Packet generation uses the half-open window described in traffic.py.
The engine is deterministic and uses no randomness yet, so config.seed has no effect.
"""

import copy
from collections.abc import Callable
from dataclasses import dataclass

from campusnet.config import ExperimentConfig, FailureEvent, validate_config
from campusnet.events import Event, EventKind, EventQueue
from campusnet.models import Flow, Link, Packet, PacketRecord
from campusnet.queues import OutputPort
from campusnet.routing import shortest_path
from campusnet.topology import (
    Topology,
    build_default_topology,
    disable_link,
    disable_router,
    validate_topology,
)
from campusnet.traffic import generate_flows, packet_times

DROP_NO_ROUTE = "no_route"
DROP_LINK_DOWN = "link_down"
DROP_ROUTER_DOWN = "router_down"
# queue_full is defined in campusnet.queues.

PortKey = tuple[str, str]  # (link_id, sending node): one per directed link


@dataclass
class SimulationResult:
    config: ExperimentConfig
    topology: Topology  # final state, after any failures
    packets: list[Packet]  # every generated packet, in creation order
    records: list[PacketRecord]  # time-ordered event log
    duration_s: float

    @property
    def generated(self) -> int:
        return len(self.packets)

    @property
    def delivered(self) -> int:
        return sum(1 for p in self.packets if p.status == "delivered")

    @property
    def dropped(self) -> int:
        return sum(1 for p in self.packets if p.status == "dropped")

    @property
    def unresolved(self) -> int:
        return self.generated - self.delivered - self.dropped


def run_simulation(config: ExperimentConfig, topology: Topology | None = None) -> SimulationResult:
    """Run one experiment and return its packets and event log.

    The topology comes from `topology` if given, otherwise from config.topology
    ("default" is the only built-in name). It is copied, so the caller's object is
    never modified. config.queue_capacity_packets is applied to every link, and
    config.bandwidth_overrides are applied by link ID.
    """
    base = topology if topology is not None else _named_topology(config.topology)
    working = copy.deepcopy(base)
    problems = validate_config(config) + validate_topology(working) + _reference_problems(config, working)
    if problems:
        raise ValueError("; ".join(problems))
    for link in working.links.values():
        link.queue_capacity_packets = config.queue_capacity_packets
    for link_id, bandwidth_bps in config.bandwidth_overrides.items():
        working.links[link_id].bandwidth_bps = bandwidth_bps
    return _Simulation(config, working).run()


def _named_topology(name: str) -> Topology:
    if name == "default":
        return build_default_topology()
    raise ValueError(f"unknown topology {name!r}")


def _reference_problems(config: ExperimentConfig, topology: Topology) -> list[str]:
    problems: list[str] = []
    for flow in config.flows:
        for endpoint in (flow.source_id, flow.destination_id):
            if endpoint not in topology.nodes:
                problems.append(f"flow {flow.flow_id}: unknown node {endpoint!r}")
        if flow.source_id == flow.destination_id:
            problems.append(f"flow {flow.flow_id}: source and destination must differ")
    for link_id in config.bandwidth_overrides:
        if link_id not in topology.links:
            problems.append(f"bandwidth override: unknown link {link_id!r}")
    for event in config.failure_events:
        if event.kind == "link" and event.target_id not in topology.links:
            problems.append(f"failure event: unknown link {event.target_id!r}")
        if event.kind == "router":
            node = topology.nodes.get(event.target_id)
            if node is None or node.node_type != "router":
                problems.append(f"failure event: {event.target_id!r} is not a router")
    return problems


class _Simulation:
    def __init__(self, config: ExperimentConfig, topology: Topology) -> None:
        self.config = config
        self.topology = topology
        self.events = EventQueue()
        self.packets: list[Packet] = []
        self.records: list[PacketRecord] = []
        self.flows: list[Flow] = generate_flows(config)
        self.times: list[list[float]] = [packet_times(f, config.duration_s) for f in self.flows]
        self.link_between: dict[frozenset[str], Link] = {
            frozenset((link.source, link.target)): link for link in topology.links.values()
        }
        self.ports: dict[PortKey, OutputPort] = {}
        for link in topology.links.values():
            for sender in (link.source, link.target):
                self.ports[(link.link_id, sender)] = OutputPort(link, config.queue_policy)
        self.next_packet_id = 1
        self.handlers: dict[EventKind, Callable[[Event], None]] = {
            EventKind.FAILURE: self.on_failure,
            EventKind.TX_COMPLETE: self.on_tx_complete,
            EventKind.NODE_ARRIVE: self.on_node_arrive,
            EventKind.GENERATE: self.on_generate,
        }

    def run(self) -> SimulationResult:
        for failure in self.config.failure_events:
            self.events.push(failure.time_s, EventKind.FAILURE, failure)
        for index, times in enumerate(self.times):
            if times:
                self.events.push(times[0], EventKind.GENERATE, (index, 0))
        while (next_time := self.events.peek_time_s()) is not None and next_time <= self.config.duration_s:
            event = self.events.pop()
            self.handlers[event.kind](event)
        return SimulationResult(self.config, self.topology, self.packets, self.records, self.config.duration_s)

    # --- logging and terminal states ---

    def log(
        self,
        event_type: str,
        packet: Packet | None = None,
        location: str | None = None,
        queue_length: int | None = None,
        drop_reason: str | None = None,
        sender: str | None = None,
    ) -> None:
        packet_id = packet.packet_id if packet is not None else None
        self.records.append(
            PacketRecord(packet_id, event_type, self.events.now_s, location, queue_length, drop_reason, sender)
        )

    def drop(
        self, packet: Packet, reason: str, location: str, queue_length: int | None = None, sender: str | None = None
    ) -> None:
        packet.status = "dropped"
        packet.drop_reason = reason
        self.log("dropped", packet, location, queue_length, reason, sender)

    # --- event handlers ---

    def on_generate(self, event: Event) -> None:
        flow_index, k = event.payload  # type: ignore[misc]
        flow = self.flows[flow_index]
        now = self.events.now_s
        packet = Packet(
            packet_id=self.next_packet_id,
            source_id=flow.source_id,
            destination_id=flow.destination_id,
            size_bytes=flow.packet_size_bytes,
            traffic_class=flow.traffic_class,
            transport_mode=flow.transport_mode,
            created_at_s=now,
        )
        self.next_packet_id += 1
        self.packets.append(packet)
        self.log("created", packet, flow.source_id)
        path = shortest_path(self.topology, flow.source_id, flow.destination_id)
        if path is None:
            self.drop(packet, DROP_NO_ROUTE, flow.source_id)
        else:
            packet.path = path
            self.arrive_at_node(packet, 0)
        if k + 1 < len(self.times[flow_index]):
            self.events.push(self.times[flow_index][k + 1], EventKind.GENERATE, (flow_index, k + 1))

    def on_node_arrive(self, event: Event) -> None:
        packet, index = event.payload  # type: ignore[misc]
        self.arrive_at_node(packet, index)

    def arrive_at_node(self, packet: Packet, index: int) -> None:
        node = packet.path[index]
        now = self.events.now_s
        if node in self.topology.failed_routers:
            self.drop(packet, DROP_ROUTER_DOWN, node)
            return
        if index == len(packet.path) - 1:
            packet.status = "delivered"
            packet.delivered_at_s = now
            self.log("delivered", packet, node)
            return
        link = self.link_between[frozenset((node, packet.path[index + 1]))]
        if not link.is_up:
            self.drop(packet, DROP_LINK_DOWN, link.link_id, sender=node)
            return
        port = self.ports[(link.link_id, node)]
        result = port.arrive(packet, now)
        if result.outcome == "transmitting":
            self.log("transmission_start", packet, link.link_id, len(port.queue), sender=node)
            assert result.transmission is not None
            self.events.push(result.transmission.end_s, EventKind.TX_COMPLETE, (link.link_id, node))
        elif result.outcome == "queued":
            self.log("queued", packet, link.link_id, len(port.queue), sender=node)
        else:
            self.log("dropped", packet, link.link_id, len(port.queue), packet.drop_reason, sender=node)

    def on_tx_complete(self, event: Event) -> None:
        key: PortKey = event.payload  # type: ignore[assignment]
        port = self.ports[key]
        finished = port.transmission
        assert finished is not None
        packet = finished.packet
        self.log("transmitted", packet, key[0], len(port.queue), sender=key[1])
        next_index = packet.path.index(key[1]) + 1
        self.events.push(finished.reaches_next_node_s, EventKind.NODE_ARRIVE, (packet, next_index))
        started = port.complete_transmission(self.events.now_s)
        if started is not None:
            self.log("transmission_start", started.packet, key[0], len(port.queue), sender=key[1])
            self.events.push(started.end_s, EventKind.TX_COMPLETE, key)

    def on_failure(self, event: Event) -> None:
        failure: FailureEvent = event.payload  # type: ignore[assignment]
        if failure.kind == "link":
            disable_link(self.topology, failure.target_id)
            self.log("link_failed", location=failure.target_id)
            self.flush_link(failure.target_id, DROP_LINK_DOWN)
        else:
            disable_router(self.topology, failure.target_id)
            self.log("router_failed", location=failure.target_id)
            for link in self.topology.links_of(failure.target_id):
                self.flush_link(link.link_id, DROP_ROUTER_DOWN)

    def flush_link(self, link_id: str, reason: str) -> None:
        """Drop every packet waiting (not transmitting) on either direction of a link."""
        link = self.topology.links[link_id]
        for sender in (link.source, link.target):
            port = self.ports[(link_id, sender)]
            for packet in port.drain_waiting():
                self.drop(packet, reason, link_id, len(port.queue), sender)
