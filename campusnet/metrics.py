"""Single source of truth for metric definitions (specification section 9).

All metrics are computed from a finished SimulationResult (its packets and event log).
Nothing here feeds back into the simulation.

Definitions. T is the measurement interval: the configured simulation duration in seconds,
used for every metric and every comparison. A metric that has no data returns None,
never a made-up number or a division by zero.

Packet counts (per group: all packets, or one traffic class)
  generated   packets created during the run. Each application packet is counted once;
              the simulator has no retransmission, so there are no extra copies to exclude.
  delivered   packets that reached their destination at or before T.
  dropped     packets explicitly dropped by the simulator (see drop_reasons).
  unresolved  generated - delivered - dropped: still queued or in transit when the run
              ended. They are NOT dropped and never counted as lost.

Ratios (unitless, 0..1; None when generated == 0)
  pdr              = delivered / generated
  plr              = dropped / generated          (explicit drops only)
  unresolved_ratio = unresolved / generated
  so pdr + plr + unresolved_ratio == 1.

Delay and throughput
  avg_delay_s     = sum(delivered_at - created_at) / delivered, seconds, delivered packets
                    only; None if nothing was delivered. End-to-end delay includes queueing,
                    transmission and propagation.
  throughput_bps  = (sum of size_bytes * 8 over delivered packets) / T, bits per second.
                    Packet size is the total simulated packet size, so this is throughput of
                    all simulated bits, not of payload only.

Queueing and starvation (per group)
  mean_queueing_delay_s  mean over all transmissions that started of (transmission start -
                         arrival at that output port), seconds, per hop. A packet that finds
                         the link idle contributes a wait of 0. Waits that ended in a drop
                         (failure flush) are excluded. None if no transmission started.
  still_waiting          packets that were sitting in an output queue when the run ended.
  max_wait_s             longest queueing wait seen, counting waits still in progress at T
                         as (T - arrival). 0.0 if nothing ever waited.

  How to read the starvation indicators. These three (mean_queueing_delay_s, still_waiting,
  max_wait_s) are project-defined indicators of how long, and how many, packets of a class
  wait for service; the spec suggests low-priority packets still waiting or their delay. They
  are not a standardized fairness or starvation metric, and no fairness index is computed.
  - still_waiting is a snapshot at the end of the run, not an average over time. It is also
    non-zero under FIFO whenever a queue is occupied at T, so interpret it per traffic class
    and compare it between runs; do not read one value in isolation.
  - Waits interrupted by a failure are excluded from mean_queueing_delay_s and max_wait_s: a
    packet flushed from a failed link's waiting queue (link_down / router_down) never started
    transmitting, so its wait is not a completed wait, and it is no longer waiting at T. Only
    completed waits and waits still in progress at T are reported.
  - Queues are finite with drop-tail, so a starved class tends to show up as queue-full drops
    and waits bounded by the queue size, not as unbounded delay.

Per directed link (identified by link_id and sending node)
  busy_time_s    total time the transmission server was serializing a packet; a transmission
                 still in progress at T counts until T.
  utilization    = busy_time_s / T. Validated to not exceed 1 (beyond float rounding).
  avg_queue_length   time-weighted average number of waiting packets over [0, T]; the queue
                     is empty at time 0 and the last recorded length is held until T. The
                     packet being transmitted is not counted.
  max_queue_length   largest recorded number of waiting packets.
  queue_drops        packets dropped on arrival because the queue was full.
"""

import math
from collections import Counter, defaultdict
from dataclasses import dataclass, fields

from campusnet.models import TRAFFIC_CLASSES, Packet
from campusnet.simulator import SimulationResult

_EPSILON = 1e-9


@dataclass(frozen=True)
class GroupMetrics:
    """Metrics for all packets (name "all") or for one traffic class."""

    name: str
    generated: int
    delivered: int
    dropped: int
    unresolved: int
    pdr: float | None
    plr: float | None
    unresolved_ratio: float | None
    avg_delay_s: float | None
    throughput_bps: float | None
    drop_reasons: dict[str, int]
    transmissions_started: int
    mean_queueing_delay_s: float | None
    still_waiting: int
    max_wait_s: float


@dataclass(frozen=True)
class LinkMetrics:
    link_id: str
    sender: str
    busy_time_s: float
    utilization: float | None
    avg_queue_length: float | None
    max_queue_length: int
    queue_drops: int


@dataclass(frozen=True)
class Metrics:
    duration_s: float
    overall: GroupMetrics
    by_class: dict[str, GroupMetrics]
    links: dict[tuple[str, str], LinkMetrics]  # keyed by (link_id, sending node)


@dataclass(frozen=True)
class ComparisonResult:
    metrics: list[Metrics]  # in the order of the results passed in
    differing_fields: list[str]  # ExperimentConfig fields that are not identical across the results


def _ratio(numerator: float, denominator: float) -> float | None:
    return numerator / denominator if denominator > 0 else None


def _group(
    name: str,
    packets: list[Packet],
    waits: list[float],
    ongoing_waits: list[float],
    duration_s: float,
) -> GroupMetrics:
    delivered = [p for p in packets if p.status == "delivered"]
    dropped = [p for p in packets if p.status == "dropped"]
    generated = len(packets)
    delays = [p.delivered_at_s - p.created_at_s for p in delivered if p.delivered_at_s is not None]
    return GroupMetrics(
        name=name,
        generated=generated,
        delivered=len(delivered),
        dropped=len(dropped),
        unresolved=generated - len(delivered) - len(dropped),
        pdr=_ratio(len(delivered), generated),
        plr=_ratio(len(dropped), generated),
        unresolved_ratio=_ratio(generated - len(delivered) - len(dropped), generated),
        avg_delay_s=_ratio(sum(delays), len(delays)),
        throughput_bps=_ratio(sum(p.size_bytes * 8 for p in delivered), duration_s),
        drop_reasons=dict(sorted(Counter(p.drop_reason for p in dropped if p.drop_reason).items())),
        transmissions_started=len(waits),
        mean_queueing_delay_s=_ratio(sum(waits), len(waits)),
        still_waiting=len(ongoing_waits),
        max_wait_s=max(waits + ongoing_waits, default=0.0),
    )


def calculate_metrics(result: SimulationResult) -> Metrics:
    duration = result.duration_s
    class_of = {p.packet_id: p.traffic_class for p in result.packets}

    waits: list[tuple[str, float]] = []  # (traffic class, completed queueing wait)
    queued_at: dict[tuple[object, str | None], float] = {}
    transmitting: dict[tuple[object, str | None], tuple[float, str | None]] = {}  # -> (start, sender)
    busy: defaultdict[tuple[str, str], float] = defaultdict(float)
    queue_series: defaultdict[tuple[str, str], list[tuple[float, int]]] = defaultdict(list)
    queue_drops: Counter[tuple[str, str]] = Counter()

    for record in result.records:
        key = (record.packet_id, record.location)
        if record.event_type == "queued":
            queued_at[key] = record.timestamp_s
        elif record.event_type == "transmission_start":
            waits.append((class_of[record.packet_id], record.timestamp_s - queued_at.pop(key, record.timestamp_s)))
            transmitting[key] = (record.timestamp_s, record.sender)
        elif record.event_type == "transmitted":
            start, sender = transmitting.pop(key)
            busy[(record.location, sender)] += record.timestamp_s - start  # type: ignore[index]
        elif record.event_type == "dropped":
            queued_at.pop(key, None)  # a wait that ends in a drop is not a completed wait
            if record.drop_reason == "queue_full":
                queue_drops[(record.location, record.sender)] += 1  # type: ignore[index]
        if record.queue_length is not None and record.sender is not None and record.location is not None:
            queue_series[(record.location, record.sender)].append((record.timestamp_s, record.queue_length))

    for (_, link_id), (start, sender) in transmitting.items():  # still serializing when the run ended
        busy[(link_id, sender)] += duration - start  # type: ignore[index]
    ongoing = [(class_of[packet_id], duration - since) for (packet_id, _), since in queued_at.items()]

    overall = _group("all", result.packets, [w for _, w in waits], [w for _, w in ongoing], duration)
    by_class = {
        traffic_class: _group(
            traffic_class,
            [p for p in result.packets if p.traffic_class == traffic_class],
            [w for c, w in waits if c == traffic_class],
            [w for c, w in ongoing if c == traffic_class],
            duration,
        )
        for traffic_class in TRAFFIC_CLASSES
    }

    links: dict[tuple[str, str], LinkMetrics] = {}
    for link in result.topology.links.values():
        for sender in (link.source, link.target):
            key = (link.link_id, sender)
            samples = queue_series.get(key, [])
            utilization = _ratio(busy[key], duration)
            if utilization is not None:
                if utilization > 1 + _EPSILON:
                    raise ValueError(f"link {key} utilization {utilization} exceeds 100%")
                utilization = min(utilization, 1.0)
            links[key] = LinkMetrics(
                link_id=link.link_id,
                sender=sender,
                busy_time_s=busy[key],
                utilization=utilization,
                avg_queue_length=_time_weighted_average(samples, duration),
                max_queue_length=max((length for _, length in samples), default=0),
                queue_drops=queue_drops[key],
            )
    return Metrics(duration, overall, by_class, links)


def _time_weighted_average(samples: list[tuple[float, int]], duration_s: float) -> float | None:
    """Average of a piecewise-constant queue length over [0, duration]; starts at 0, last value is held."""
    if duration_s <= 0:
        return None
    area, previous_time, length = 0.0, 0.0, 0
    for time_s, new_length in samples:
        area += length * (time_s - previous_time)
        previous_time, length = time_s, new_length
    area += length * (duration_s - previous_time)
    return area / duration_s


def throughput_over_time(result: SimulationResult, interval_s: float) -> list[tuple[float, float]]:
    """Delivered bits per second in consecutive bins of interval_s: a list of (bin_start_s, bps).

    The last bin is shortened to end at the run duration, and a packet delivered exactly at the
    end belongs to the last bin. The bins together cover exactly the delivered packets.
    """
    if interval_s <= 0:
        raise ValueError("interval_s must be > 0")
    duration = result.duration_s
    bin_count = max(1, math.ceil(duration / interval_s - _EPSILON))
    bits = [0.0] * bin_count
    for packet in result.packets:
        if packet.status == "delivered" and packet.delivered_at_s is not None:
            bits[min(int(packet.delivered_at_s // interval_s), bin_count - 1)] += packet.size_bytes * 8
    series = []
    for index, total in enumerate(bits):
        start = index * interval_s
        series.append((start, total / (min(start + interval_s, duration) - start)))
    return series


def compare_results(results: list[SimulationResult]) -> ComparisonResult:
    """Metrics for each result plus which configuration fields differ between them.

    A fair comparison changes one main variable: differing_fields then lists exactly that field.
    """
    differing = [
        field.name
        for field in fields(results[0].config)
        if any(getattr(r.config, field.name) != getattr(results[0].config, field.name) for r in results[1:])
    ] if results else []
    return ComparisonResult([calculate_metrics(r) for r in results], differing)
