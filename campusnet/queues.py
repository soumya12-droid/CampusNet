"""Output queues and the per-link transmission server (specification sections 5.3 and 6.2).

Nothing here owns a clock or schedules events. Callers pass the current simulation
time, and the discrete-event engine (a later phase) turns the returned times into events.

Rules:
- One OutputPort per directed link: one transmission server plus a finite waiting queue.
- Queue capacity counts waiting packets only; the packet being transmitted is not counted.
- A packet that finds the server idle starts transmitting at once and never occupies the queue.
- A waiting packet leaves the queue exactly when its transmission starts.
- Drop-tail: if the waiting queue is full on arrival, the arriving packet is dropped.
- Strict priority picks among waiting packets only and never interrupts a transmission.
"""

import math
from collections import deque
from dataclasses import dataclass
from typing import Literal

from campusnet.config import QUEUE_POLICIES, QueuePolicy
from campusnet.models import TRAFFIC_CLASSES, Link, Packet
from campusnet.timing import transmission_delay_s

DROP_REASON_QUEUE_FULL = "queue_full"

# Lower rank is served first: realtime > interactive > bulk.
TRAFFIC_CLASS_RANK: dict[str, int] = {"realtime": 0, "interactive": 1, "bulk": 2}
assert set(TRAFFIC_CLASS_RANK) == set(TRAFFIC_CLASSES)


class PacketQueue:
    """Finite waiting queue with a FIFO or strict-priority service order."""

    def __init__(self, policy: QueuePolicy, capacity_packets: int) -> None:
        if policy not in QUEUE_POLICIES:
            raise ValueError(f"invalid queue policy {policy!r}")
        if isinstance(capacity_packets, bool) or not isinstance(capacity_packets, int) or capacity_packets < 0:
            raise ValueError("capacity_packets must be an integer >= 0")
        self.policy = policy
        self.capacity_packets = capacity_packets
        self.drop_count = 0
        # FIFO uses one lane; strict priority uses one FIFO lane per traffic class.
        self._lanes: dict[int, deque[Packet]] = {}
        self._size = 0

    def __len__(self) -> int:
        return self._size

    @property
    def is_full(self) -> bool:
        return self._size >= self.capacity_packets

    def _lane_key(self, packet: Packet) -> int:
        if self.policy == "fifo":
            return 0
        try:
            return TRAFFIC_CLASS_RANK[packet.traffic_class]
        except KeyError:
            raise ValueError(f"invalid traffic_class {packet.traffic_class!r}") from None

    def _push(self, packet: Packet) -> None:
        self._lanes.setdefault(self._lane_key(packet), deque()).append(packet)
        self._size += 1

    def _pop(self) -> Packet | None:
        for key in sorted(self._lanes):
            if self._lanes[key]:
                self._size -= 1
                return self._lanes[key].popleft()
        return None


def enqueue_packet(queue: PacketQueue, packet: Packet) -> bool:
    """Add a packet to the waiting queue. Return False and drop it if the queue is full."""
    if queue.is_full:
        packet.status = "dropped"
        packet.drop_reason = DROP_REASON_QUEUE_FULL
        queue.drop_count += 1
        return False
    queue._push(packet)
    packet.status = "queued"
    return True


def dequeue_packet(queue: PacketQueue) -> Packet | None:
    """Remove and return the next waiting packet per the queue policy, or None if empty."""
    return queue._pop()


@dataclass(frozen=True)
class Transmission:
    packet: Packet
    start_s: float  # transmission starts (packet leaves the waiting queue)
    end_s: float  # serialization finishes; the server is free again
    reaches_next_node_s: float  # end_s + propagation delay
    queueing_delay_s: float  # start_s minus arrival at this output port


@dataclass(frozen=True)
class ArrivalResult:
    outcome: Literal["transmitting", "queued", "dropped"]
    transmission: Transmission | None = None  # set only when outcome == "transmitting"


class OutputPort:
    """Transmission server plus waiting queue for one directed link."""

    def __init__(self, link: Link, policy: QueuePolicy) -> None:
        self.link = link
        self.queue = PacketQueue(policy, link.queue_capacity_packets)
        self.transmission: Transmission | None = None  # packet currently being serialized
        self._arrival_s: dict[int | str, float] = {}  # waiting packet_id -> arrival time

    @property
    def is_busy(self) -> bool:
        return self.transmission is not None

    def arrive(self, packet: Packet, now_s: float) -> ArrivalResult:
        """A packet reaches this output port at time now_s."""
        if not self.is_busy:
            return ArrivalResult("transmitting", self._start(packet, now_s, arrived_s=now_s))
        if enqueue_packet(self.queue, packet):
            self._arrival_s[packet.packet_id] = now_s
            return ArrivalResult("queued")
        return ArrivalResult("dropped")

    def complete_transmission(self, now_s: float) -> Transmission | None:
        """The current transmission finishes at now_s. Start the next waiting packet, if any."""
        if self.transmission is None:
            raise ValueError("no transmission in progress")
        if now_s < self.transmission.end_s and not math.isclose(now_s, self.transmission.end_s, abs_tol=1e-12):
            raise ValueError(f"transmission ends at {self.transmission.end_s}, not before {now_s}")
        self.transmission = None
        packet = dequeue_packet(self.queue)
        if packet is None:
            return None
        return self._start(packet, now_s, arrived_s=self._arrival_s.pop(packet.packet_id))

    def drain_waiting(self) -> list[Packet]:
        """Remove and return every waiting packet in service order. The transmission in progress is untouched."""
        drained: list[Packet] = []
        while (packet := dequeue_packet(self.queue)) is not None:
            drained.append(packet)
        self._arrival_s.clear()
        return drained

    def _start(self, packet: Packet, start_s: float, arrived_s: float) -> Transmission:
        end_s = start_s + transmission_delay_s(packet.size_bytes, self.link.bandwidth_bps)
        packet.status = "in_transit"
        self.transmission = Transmission(
            packet=packet,
            start_s=start_s,
            end_s=end_s,
            reaches_next_node_s=end_s + self.link.propagation_delay_s,
            queueing_delay_s=start_s - arrived_s,
        )
        return self.transmission
