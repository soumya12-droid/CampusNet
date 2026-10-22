"""Read-only reconstruction of network state from a finished run's event log.

Nothing here simulates anything. Every answer is derived from the recorded `PacketRecord`s, the
packets' fixed paths, and the topology. A run that has finished is replayed, not re-executed.

Reconstruction rules (they follow the order in which the engine logs events):
  created             the packet exists at its source node.
  queued              waiting in the output queue of (link, sender) until its next record.
  transmission_start  being serialized onto (link, sender) until its next record ("transmitted").
  transmitted         propagating along the link until its next record: arrival at the next node is
                      not logged on its own, but it coincides exactly with that next record (queued,
                      transmission_start, delivered or dropped). A packet still propagating when the
                      run ended arrives after its transmitted time plus the link's propagation delay.
  delivered / dropped terminal.

Queue sizes shown by a replay are the number of packets reconstructed as waiting at that port, so
they agree with the engine's recorded queue lengths (a test checks this at every logged event).

Drop causes are told apart by which record fields are set, using the engine's documented rules:
  queue_full                      arrived at a full queue
  no_route                        created when no path existed
  link_down, with a queue length  flushed from the waiting queue of a link that failed
  link_down, no queue length      reached a node whose next-hop link was already down
  router_down, with a queue length  flushed from a queue on a link attached to a failed router
  router_down, no queue length    reached a failed router
"""

from bisect import bisect_left, bisect_right
from collections import Counter
from dataclasses import dataclass, field

from campusnet.models import Packet, PacketRecord
from campusnet.simulator import SimulationResult
from campusnet.timing import transmission_delay_s

PortKey = tuple[str, str]  # (link_id, sending node)

NAV_FILTERS: dict[str, set[str] | None] = {
    "Any event": None,
    "Drops": {"dropped"},
    "Failures": {"link_failed", "router_failed"},
    "Deliveries": {"delivered"},
    "Transmissions": {"transmission_start"},
    "Packets joining a queue": {"queued"},
    "Packets created": {"created"},
}

DROP_KINDS = ("queue_full", "no_route", "flushed_link", "flushed_router", "next_hop_down", "reached_failed_router")


@dataclass(frozen=True)
class PacketState:
    kind: str  # not_created | at_source | waiting | transmitting | propagating | delivered | dropped
    link_id: str | None = None
    sender: str | None = None
    node: str | None = None
    since_s: float | None = None
    until_s: float | None = None  # when this state ends, if known
    fraction: float | None = None  # progress through transmitting/propagating, 0..1


@dataclass
class PortSnapshot:
    capacity: int
    waiting: list = field(default_factory=list)  # packet ids, in queue order
    transmitting: int | None = None
    propagating: list = field(default_factory=list)  # (packet id, fraction of the propagation done)


@dataclass
class Snapshot:
    index: int  # index of the last record applied; -1 means before any event
    time_s: float
    failed_links: set
    failed_routers: set
    ports: dict  # PortKey -> PortSnapshot, only ports holding packets
    created: int
    delivered: int
    dropped: int

    @property
    def in_network(self) -> int:
        return self.created - self.delivered - self.dropped

    def queue_length(self, key: PortKey) -> int:
        port = self.ports.get(key)
        return len(port.waiting) if port else 0


@dataclass(frozen=True)
class RouteUse:
    path: tuple  # node ids; empty when the packets had no route
    packets: int
    first_created_s: float
    last_created_s: float
    delivered: int
    dropped: int
    unresolved: int


class ReplayIndex:
    """Index over one run's records for fast state lookup at any event."""

    def __init__(self, result: SimulationResult) -> None:
        self.result = result
        self.records: list[PacketRecord] = result.records
        self.times = [r.timestamp_s for r in self.records]
        self.packets: dict = {p.packet_id: p for p in result.packets}
        self.links = result.topology.links
        self.by_packet: dict = {}
        self.by_type: dict = {}
        self.failures: list = []  # (record index, kind, target)
        for i, r in enumerate(self.records):
            if r.packet_id is not None:
                self.by_packet.setdefault(r.packet_id, []).append(i)
            self.by_type.setdefault(r.event_type, []).append(i)
            if r.event_type in ("link_failed", "router_failed"):
                self.failures.append((i, "link" if r.event_type == "link_failed" else "router", r.location))
        self._first = {pid: idxs[0] for pid, idxs in self.by_packet.items()}
        self._terminal = {
            pid: idxs[-1] for pid, idxs in self.by_packet.items()
            if self.records[idxs[-1]].event_type in ("delivered", "dropped")
        }
        self._created_order = sorted(self._first, key=self._first.get)  # type: ignore[arg-type]
        self._created_firsts = [self._first[pid] for pid in self._created_order]
        self._filter_cache: dict = {}

    # --- navigation ---

    def __len__(self) -> int:
        return len(self.records)

    def index_at_time(self, time_s: float) -> int:
        """Index of the last record at or before time_s, or -1 if none."""
        return bisect_right(self.times, time_s) - 1

    def indices(self, filter_name: str) -> list:
        wanted = NAV_FILTERS[filter_name]
        if wanted is None:
            return list(range(len(self.records)))
        if filter_name not in self._filter_cache:
            self._filter_cache[filter_name] = sorted(i for t in wanted for i in self.by_type.get(t, []))
        return self._filter_cache[filter_name]

    def step(self, index: int, filter_name: str, direction: int) -> int | None:
        """Next (+1) or previous (-1) record of the given kind relative to index; None if there is none."""
        if filter_name == "Any event":
            target = index + direction
            return target if 0 <= target < len(self.records) else None
        found = self.indices(filter_name)
        if direction > 0:
            k = bisect_right(found, index)
            return found[k] if k < len(found) else None
        k = bisect_left(found, index) - 1
        return found[k] if k >= 0 else None

    def first(self, filter_name: str) -> int | None:
        found = self.indices(filter_name)
        return found[0] if found else None

    # --- link / node helpers ---

    def receiver(self, link_id: str, sender: str) -> str:
        link = self.links[link_id]
        return link.target if sender == link.source else link.source

    def _failed_at(self, index: int) -> tuple[set, set]:
        links: set = set()
        routers: set = set()
        for i, kind, target in self.failures:
            if i > index:
                break
            if kind == "link":
                links.add(target)
            else:
                routers.add(target)
                links.update(l.link_id for l in self.result.topology.links_of(target))
        return links, routers

    # --- packet state ---

    def packet_state(self, packet_id, index: int, time_s: float | None = None) -> PacketState:
        """State of one packet after the record at `index`. `time_s` (at or after that record, before the
        next one) only affects the progress fraction of a packet that is transmitting or propagating."""
        idxs = self.by_packet.get(packet_id)
        if not idxs or idxs[0] > index:
            return PacketState("not_created")
        k = bisect_right(idxs, index) - 1
        rec = self.records[idxs[k]]
        nxt = self.records[idxs[k + 1]] if k + 1 < len(idxs) else None
        now = time_s if time_s is not None else (self.records[index].timestamp_s if index >= 0 else 0.0)
        kind = rec.event_type
        if kind == "created":
            return PacketState("at_source", node=rec.location, since_s=rec.timestamp_s)
        if kind == "queued":
            return PacketState("waiting", rec.location, rec.sender, rec.sender, rec.timestamp_s,
                               nxt.timestamp_s if nxt else None)
        if kind == "transmission_start":
            if nxt:
                end = nxt.timestamp_s
            else:  # the run ended mid-transmission: serialization still takes size / bandwidth
                end = rec.timestamp_s + transmission_delay_s(self.packets[packet_id].size_bytes,
                                                             self.links[rec.location].bandwidth_bps)  # type: ignore[index]
            return PacketState("transmitting", rec.location, rec.sender, rec.sender, rec.timestamp_s, end,
                               _progress(rec.timestamp_s, end, now))
        if kind == "transmitted":
            link = self.links[rec.location]  # type: ignore[index]
            arrival = nxt.timestamp_s if nxt else rec.timestamp_s + link.propagation_delay_s
            return PacketState("propagating", rec.location, rec.sender, None, rec.timestamp_s, arrival,
                               _progress(rec.timestamp_s, arrival, now))
        if kind == "delivered":
            return PacketState("delivered", node=rec.location, since_s=rec.timestamp_s)
        return PacketState("dropped", rec.location if rec.sender else None, rec.sender, rec.location if not rec.sender else rec.sender,
                           rec.timestamp_s)

    def snapshot(self, index: int, time_s: float | None = None) -> Snapshot:
        """Network state after the record at `index` has been applied (-1: before the first event).

        `time_s` may be any time from that record up to (not including) the next record; the discrete state
        is the same throughout, and only the progress of packets along a link depends on it.
        """
        index = max(-1, min(index, len(self.records) - 1))
        if time_s is None:
            time_s = self.records[index].timestamp_s if index >= 0 else 0.0
        failed_links, failed_routers = self._failed_at(index)
        ports: dict = {}
        upto = bisect_right(self._created_firsts, index)
        for pid in self._created_order[:upto]:
            terminal = self._terminal.get(pid)
            if terminal is not None and terminal <= index:
                continue
            state = self.packet_state(pid, index, time_s)
            if state.link_id is None or state.sender is None:
                continue
            key = (state.link_id, state.sender)
            port = ports.get(key)
            if port is None:
                port = ports[key] = PortSnapshot(self.links[state.link_id].queue_capacity_packets)
            if state.kind == "waiting":
                port.waiting.append(pid)
            elif state.kind == "transmitting":
                port.transmitting = pid
            elif state.kind == "propagating":
                port.propagating.append((pid, state.fraction or 0.0))
        created = bisect_right(self.by_type.get("created", []), index)
        delivered = bisect_right(self.by_type.get("delivered", []), index)
        dropped = bisect_right(self.by_type.get("dropped", []), index)
        return Snapshot(index, time_s, failed_links, failed_routers, ports, created, delivered, dropped)

    # --- drops and explanations ---

    def drop_kind(self, record: PacketRecord) -> str:
        reason = record.drop_reason
        flushed = record.sender is not None and record.queue_length is not None
        if reason == "queue_full":
            return "queue_full"
        if reason == "no_route":
            return "no_route"
        if reason == "link_down":
            return "flushed_link" if flushed else "next_hop_down"
        if reason == "router_down":
            return "flushed_router" if flushed else "reached_failed_router"
        raise ValueError(f"unknown drop reason {reason!r}")

    def _failure_before(self, index: int, link_id: str):
        """The most recent failure at or before index that took this link down (directly or via a router), if any."""
        best = None
        for i, kind, target in self.failures:
            if i > index:
                break
            links = {target} if kind == "link" else {l.link_id for l in self.result.topology.links_of(target)}
            if link_id in links:
                best = (i, kind, target)
        return best

    def explain(self, index: int) -> str:
        """A plain-language description of one recorded event."""
        r = self.records[index]
        pid = r.packet_id
        p: Packet | None = self.packets.get(pid)
        who = f"Packet {pid} ({p.traffic_class}, {p.size_bytes} B)" if p else ""
        link = r.location
        if r.event_type == "created":
            path = " → ".join(p.path) if p and p.path else "no route"
            return f"{who} was created at {r.location}. Its path is fixed now: {path}."
        if r.event_type == "queued":
            return (f"{who} arrived at {link} ({r.sender} → {self.receiver(link, r.sender)}) while the link was busy, "
                    f"and joined the waiting queue ({r.queue_length} waiting).")
        if r.event_type == "transmission_start":
            queued = self._queued_time(pid, link)
            wait = "found the link idle" if queued is None else f"waited {(r.timestamp_s - queued) * 1000:.2f} ms in the queue"
            return (f"{who} {wait} and started transmitting on {link} ({r.sender} → {self.receiver(link, r.sender)}). "
                    f"{r.queue_length} still waiting.")
        if r.event_type == "transmitted":
            return (f"{who} finished transmitting on {link}; it now propagates to {self.receiver(link, r.sender)} "
                    f"and the link can start its next packet.")
        if r.event_type == "delivered":
            return f"{who} reached its destination {r.location}; end-to-end delay {(r.timestamp_s - p.created_at_s) * 1000:.2f} ms."  # type: ignore[union-attr]
        if r.event_type == "dropped":
            return self.explain_drop(index)
        if r.event_type == "link_failed":
            return (f"Link {link} failed. Packets waiting in its queues are dropped; a packet already being transmitted "
                    "finishes that hop; packets created from now on are routed around it if a path exists.")
        if r.event_type == "router_failed":
            return (f"Router {link} failed and every link attached to it went down. Packets waiting at those links are "
                    "dropped; packets already transmitting finish their hop; later packets are routed around it if possible.")
        return r.event_type

    def _queued_time(self, packet_id, link_id) -> float | None:
        for i in self.by_packet.get(packet_id, []):
            rec = self.records[i]
            if rec.event_type == "queued" and rec.location == link_id:
                return rec.timestamp_s
        return None

    def explain_drop(self, index: int) -> str:
        r = self.records[index]
        kind = self.drop_kind(r)
        p = self.packets.get(r.packet_id)
        who = f"Packet {r.packet_id} ({p.traffic_class}, {p.size_bytes} B)" if p else f"Packet {r.packet_id}"
        if kind == "queue_full":
            capacity = self.links[r.location].queue_capacity_packets  # type: ignore[index]
            return (f"{who} arrived at {r.location} ({r.sender} → {self.receiver(r.location, r.sender)}) and the waiting "
                    f"queue was full ({capacity} packets), so it was dropped (drop-tail).")
        if kind == "no_route":
            return f"{who} was created at {r.location} when no path to {p.destination_id if p else 'its destination'} existed, so it was dropped at creation."
        if kind in ("flushed_link", "flushed_router"):
            what = "link" if kind == "flushed_link" else "a router attached to this link"
            return (f"{who} was waiting in the queue of {r.location} ({r.sender} → {self.receiver(r.location, r.sender)}) when "
                    f"{what} failed at {r.timestamp_s:.4f} s, so it was dropped. Packets already transmitting finish their hop.")
        if kind == "next_hop_down":
            failure = self._failure_before(index, link_id=r.location)
            when = f" at {self.records[failure[0]].timestamp_s:.4f} s" if failure else ""
            return (f"{who} had a path through {r.location} and reached {r.sender} after that link went down{when}, so it was "
                    "dropped there. A packet's path is fixed when it is created and is never recomputed.")
        return f"{who} reached router {r.location}, which had failed, so it was dropped."

    # --- packet journeys ---

    def journey(self, packet_id) -> list:
        """Record indices of one packet, in order."""
        return list(self.by_packet.get(packet_id, []))

    # --- routes ---

    def flow_routes(self) -> dict:
        """For each configured flow, the paths its packets actually took (grouped by source, destination, class)."""
        groups: dict = {}
        for p in self.result.packets:
            groups.setdefault((p.source_id, p.destination_id, p.traffic_class), []).append(p)
        out: dict = {}
        for flow in self.result.config.flows:
            packets = groups.get((flow.source_id, flow.destination_id, flow.traffic_class), [])
            by_path: dict = {}
            for p in packets:
                by_path.setdefault(tuple(p.path), []).append(p)
            uses = []
            for path, group in by_path.items():
                status = Counter(p.status for p in group)
                uses.append(RouteUse(
                    path, len(group), min(p.created_at_s for p in group), max(p.created_at_s for p in group),
                    status["delivered"], status["dropped"], len(group) - status["delivered"] - status["dropped"],
                ))
            out[flow.flow_id] = sorted(uses, key=lambda u: u.first_created_s)
        return out

    def route_at(self, flow_id: str, index: int) -> tuple | None:
        """The path of the most recently created packet of a flow at or before index; None if none yet."""
        flow = next((f for f in self.result.config.flows if f.flow_id == flow_id), None)
        if flow is None:
            raise ValueError(f"unknown flow {flow_id!r}")
        best = None
        for p in self.result.packets:
            if (p.source_id, p.destination_id, p.traffic_class) != (flow.source_id, flow.destination_id, flow.traffic_class):
                continue
            first = self._first.get(p.packet_id)
            if first is not None and first <= index:
                best = tuple(p.path)
            elif first is not None and first > index:
                break
        return best

    # --- failures ---

    def failure_summary(self) -> list:
        """For each failure: what happened to packets in the network, using the engine's failure rules.

        Drops after a failure are attributed to the most recent failure before them.
        """
        rows = []
        for n, (fi, kind, target) in enumerate(self.failures):
            affected = {target} if kind == "link" else {l.link_id for l in self.result.topology.links_of(target)}
            snap = self.snapshot(fi)
            in_transit = []
            for key, port in snap.ports.items():
                if key[0] in affected:
                    in_transit += ([port.transmitting] if port.transmitting is not None else []) + [pid for pid, _ in port.propagating]
            outcomes = Counter(self.packets[pid].status for pid in in_transit)
            next_failure = self.failures[n + 1][0] if n + 1 < len(self.failures) else len(self.records)
            counts: Counter = Counter()
            for i in self.by_type.get("dropped", []):
                if i <= fi or i >= next_failure:
                    continue
                dk = self.drop_kind(self.records[i])
                if dk in ("flushed_link", "flushed_router") and self.records[i].location not in affected:
                    continue
                counts[dk] += 1
            rows.append({
                "time_s": self.records[fi].timestamp_s, "kind": kind, "target": target,
                "in_transit_at_failure": len(in_transit),
                "in_transit_delivered": outcomes["delivered"], "in_transit_dropped": outcomes["dropped"],
                "in_transit_unresolved": len(in_transit) - outcomes["delivered"] - outcomes["dropped"],
                "flushed": counts["flushed_link"] + counts["flushed_router"],
                "next_hop_down": counts["next_hop_down"],
                "reached_failed_router": counts["reached_failed_router"],
                "no_route": counts["no_route"],
            })
        return rows


def _progress(start: float, end: float | None, now: float) -> float:
    if end is None or end <= start:
        return 1.0 if end is not None else 0.0
    return max(0.0, min(1.0, (now - start) / (end - start)))
