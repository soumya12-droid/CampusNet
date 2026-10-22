"""Plain-language statements and inspector details, generated from measured results.

Every number comes from a SimulationResult, its Metrics, or the replay of its event log. Statements
describe what was measured in one simulated workload. They do not say which setting is better, and
conclusions are only worded when the data supports them (for example "dropped to zero" is only
written when the measured count is zero).
"""

from campusnet.experiments import BOTTLENECK, BOTTLENECK_LINK_ID, ExperimentResult, ExperimentRun
from campusnet.metrics import Metrics
from campusnet.presentation import POLICY_LABELS, TRAFFIC_CLASS_LABELS, describe_failures, fmt_mbps, fmt_ms, fmt_pct, offered_load_bps
from campusnet.replay import ReplayIndex, Snapshot
from campusnet.simulator import SimulationResult
from campusnet.topology import Topology

CLASSES = ("realtime", "interactive", "bulk")
CAUTION = "These are measurements of one simulated workload, not general rules."


def _bottleneck_mbps(run: ExperimentRun) -> float:
    return run.result.topology.links[BOTTLENECK_LINK_ID].bandwidth_bps / 1e6


def _trend(values: list[float]) -> str:
    if all(b >= a for a, b in zip(values, values[1:])) and values[0] < values[-1]:
        return "rose"
    if all(b <= a for a, b in zip(values, values[1:])) and values[0] > values[-1]:
        return "fell"
    if values[0] == values[-1] and len(set(values)) == 1:
        return "stayed the same"
    return "did not change in one direction"


def _arrow(a: str, b: str) -> str:
    return f"{a} → {b}"


def run_observations(run: ExperimentRun) -> list[str]:
    """What one run measured: outcomes, the bottleneck, drops by reason, class delays and any failure."""
    o, m = run.metrics.overall, run.metrics
    link = m.links[BOTTLENECK]
    cap = run.config.queue_capacity_packets
    lines = [
        f"{o.generated:,} packets were generated: {o.delivered:,} delivered ({fmt_pct(o.pdr)}), {o.dropped:,} dropped, "
        f"and {o.unresolved:,} still in the network when the run ended (not counted as lost).",
        f"The offered load was {offered_load_bps(run.config) / 1e6:.1f} Mbps against a {_bottleneck_mbps(run):g} Mbps r1-r2 link. "
        f"That link was transmitting {fmt_pct(link.utilization)} of the time and its queue peaked at {link.max_queue_length} of "
        f"{cap} packets (average {link.avg_queue_length or 0:.1f}).",
    ]
    if o.drop_reasons:
        lines.append("Drops by reason: " + ", ".join(f"{reason} {count:,}" for reason, count in o.drop_reasons.items()) + ".")
    else:
        lines.append("No packets were dropped.")
    delays = ", ".join(f"{TRAFFIC_CLASS_LABELS[c].lower()} {fmt_ms(m.by_class[c].avg_delay_s)}" for c in CLASSES)
    lines.append(f"Average end-to-end delay by class: {delays}; policy: {POLICY_LABELS[run.config.queue_policy]}.")
    if run.config.failure_events:
        for row in ReplayIndex(run.result).failure_summary():
            lines.append(failure_sentence(row))
    return lines


def failure_sentence(row: dict) -> str:
    """One failure's effect on packets, using the engine's failure rules."""
    parts = [
        f"{row['in_transit_at_failure']} mid-transmission or propagating on the failed link when it went down "
        f"({row['in_transit_delivered']} delivered, {row['in_transit_dropped']} dropped, {row['in_transit_unresolved']} unresolved)",
        f"{row['flushed']} flushed from its waiting queue",
        f"{row['next_hop_down']} dropped later on reaching a node whose next link was down",
    ]
    if row["reached_failed_router"]:
        parts.append(f"{row['reached_failed_router']} dropped on reaching the failed router")
    parts.append(f"{row['no_route']} created afterwards with no route")
    return f"{row['kind'].capitalize()} {row['target']} failed at {row['time_s']:g} s: " + "; ".join(parts) + "."


# --- presets ---


def preset_observations(experiment: ExperimentResult) -> list[str]:
    builders = {"A": _observe_a, "B": _observe_b, "C": _observe_c, "D": _observe_d, "E": _observe_e}
    return builders[experiment.experiment_id](experiment) + [CAUTION]


def _observe_a(e: ExperimentResult) -> list[str]:
    low, high = e.runs
    ll, hl = low.metrics.links[BOTTLENECK], high.metrics.links[BOTTLENECK]
    cap = high.config.queue_capacity_packets
    lines = [
        f"Offered load was {offered_load_bps(low.config) / 1e6:.1f} Mbps at intensity {low.settings['intensity']:g} and "
        f"{offered_load_bps(high.config) / 1e6:.1f} Mbps at {high.settings['intensity']:g}, against a {_bottleneck_mbps(high):g} Mbps bottleneck.",
        f"Bottleneck queue: peak {_arrow(str(ll.max_queue_length), str(hl.max_queue_length))} of {cap} packets, "
        f"average {_arrow(f'{ll.avg_queue_length:.1f}', f'{hl.avg_queue_length:.1f}')}.",
        f"Queue-full drops at the bottleneck {_arrow(str(ll.queue_drops), str(hl.queue_drops))}; delivered share "
        f"{_arrow(fmt_pct(low.metrics.overall.pdr), fmt_pct(high.metrics.overall.pdr))}; average delay "
        f"{_arrow(fmt_ms(low.metrics.overall.avg_delay_s), fmt_ms(high.metrics.overall.avg_delay_s))}.",
        f"Delivered throughput {_arrow(fmt_mbps(low.metrics.overall.throughput_bps), fmt_mbps(high.metrics.overall.throughput_bps))}; "
        f"bottleneck utilization {_arrow(fmt_pct(ll.utilization), fmt_pct(hl.utilization))}.",
    ]
    if hl.max_queue_length >= cap and hl.queue_drops > 0 and ll.queue_drops == 0:
        lines.append("What it shows: at the higher load the bottleneck queue filled to its limit and drop-tail discarded arriving "
                     "packets, so the extra demand appears as loss and queueing delay rather than as extra throughput.")
    elif hl.queue_drops == 0 and ll.queue_drops == 0:
        lines.append("What it shows: neither load overflowed the bottleneck queue in this run.")
    else:
        lines.append(f"What it shows: queue-full drops were {ll.queue_drops} at the lower load and {hl.queue_drops} at the higher load.")
    return lines


def _observe_b(e: ExperimentResult) -> list[str]:
    fifo, prio = e.runs
    lines = []
    lower, higher = [], []
    for c in CLASSES:
        f, p = fifo.metrics.by_class[c], prio.metrics.by_class[c]
        lines.append(
            f"{TRAFFIC_CLASS_LABELS[c]}: average delay {_arrow(fmt_ms(f.avg_delay_s), fmt_ms(p.avg_delay_s))}; delivered "
            f"{_arrow(fmt_pct(f.pdr), fmt_pct(p.pdr))}; mean queueing delay per hop "
            f"{_arrow(fmt_ms(f.mean_queueing_delay_s), fmt_ms(p.mean_queueing_delay_s))}."
        )
        if f.avg_delay_s is not None and p.avg_delay_s is not None and abs(f.avg_delay_s - p.avg_delay_s) > 5e-5:
            (lower if p.avg_delay_s < f.avg_delay_s else higher).append(TRAFFIC_CLASS_LABELS[c].lower())
    fb, pb = fifo.metrics.by_class["bulk"], prio.metrics.by_class["bulk"]
    lines.append(f"Bulk packets still waiting at the end {_arrow(str(fb.still_waiting), str(pb.still_waiting))}; longest wait "
                 f"{_arrow(fmt_ms(fb.max_wait_s), fmt_ms(pb.max_wait_s))}. (Project-defined indicators, a snapshot at the end of the run.)")
    lines.append(f"Overall delivered share {_arrow(fmt_pct(fifo.metrics.overall.pdr), fmt_pct(prio.metrics.overall.pdr))}; "
                 f"overall drops {_arrow(str(fifo.metrics.overall.dropped), str(prio.metrics.overall.dropped))}.")
    change = []
    if lower:
        change.append("lower for " + " and ".join(lower))
    if higher:
        change.append("higher for " + " and ".join(higher))
    lines.append("What it shows: under strict priority the average delay was " + (" but ".join(change) if change else "about the same for every class")
                 + ". Strict priority serves a lower class only when no higher class is waiting, so what each class gains or loses depends on the load mix.")
    return lines


def _observe_c(e: ExperimentResult) -> list[str]:
    lines = []
    for run in e.runs:
        o, link = run.metrics.overall, run.metrics.links[BOTTLENECK]
        lines.append(f"{_bottleneck_mbps(run):g} Mbps: delivered {fmt_pct(o.pdr)}, throughput {fmt_mbps(o.throughput_bps)}, queue-full drops "
                     f"{link.queue_drops:,}, average delay {fmt_ms(o.avg_delay_s)}, bottleneck utilization {fmt_pct(link.utilization)}.")
    offered = offered_load_bps(e.runs[0].config) / 1e6
    clear = [r for r in e.runs if r.metrics.links[BOTTLENECK].queue_drops == 0]
    if clear:
        first = clear[0]
        lines.append(f"What it shows: with an offered load of {offered:.1f} Mbps, queue-full drops reached zero at {_bottleneck_mbps(first):g} Mbps; "
                     f"there the bottleneck was busy {fmt_pct(first.metrics.links[BOTTLENECK].utilization)} of the time, and capacity beyond that "
                     "lowers utilization without adding throughput.")
    else:
        lines.append(f"What it shows: queue-full drops occurred at every capacity tested (offered load {offered:.1f} Mbps).")
    return lines


def _observe_d(e: ExperimentResult) -> list[str]:
    baseline, redundant, bridge = e.runs
    lines = [f"Baseline (no failure): delivered {fmt_pct(baseline.metrics.overall.pdr)}, {baseline.metrics.overall.dropped} dropped."]
    for run, link_id in ((redundant, "r1-r2"), (bridge, "r2-portal")):
        routes = [r for r in e.routes if r["failed_link"] == link_id]
        changed = sum(1 for r in routes if r["route_changed"])
        unreachable = [r["flow"] for r in routes if not r["reachable_after"]]
        lines.append(f"Routing after {link_id} fails: {changed} of {len(routes)} flows change path, "
                     + (f"{len(unreachable)} have no route ({', '.join(unreachable)})." if unreachable else "and every flow still has a route."))
        lines += [failure_sentence(row) for row in ReplayIndex(run.result).failure_summary()]
        o = run.metrics.overall
        lines.append(f"Packets: delivered {fmt_pct(o.pdr)} ({o.delivered:,} of {o.generated:,}), dropped {o.dropped:,}"
                     + (f" ({', '.join(f'{k} {v}' for k, v in o.drop_reasons.items())})." if o.drop_reasons else "."))
    lines.append("What it shows: the redundant link's failure changes routes but loses only the packets caught at the failed link; the bridge "
                 "link's failure leaves its server unreachable, so packets for it are dropped at creation (no_route). Routing results and packet "
                 "outcomes are different things: a route can exist while packets already on the old path are still lost.")
    return lines


def _observe_e(e: ExperimentResult) -> list[str]:
    lines = []
    caps, drops, delays = [], [], []
    for run in e.runs:
        o, link = run.metrics.overall, run.metrics.links[BOTTLENECK]
        caps.append(run.config.queue_capacity_packets)
        drops.append(link.queue_drops)
        delays.append(o.avg_delay_s or 0.0)
        lines.append(f"Capacity {caps[-1]}: queue-full drops {link.queue_drops:,}, delivered {fmt_pct(o.pdr)}, average delay {fmt_ms(o.avg_delay_s)}, "
                     f"peak queue {link.max_queue_length}, {o.unresolved:,} packets still in the network at the end.")
    lines.append(f"What it shows: as the queue grew from {caps[0]} to {caps[-1]} packets, queue-full drops {_trend(drops)} and average delay {_trend(delays)}. "
                 "A larger buffer trades drops for waiting time; it does not add link capacity.")
    return lines


# --- inspector details ---


def node_details(topology: Topology, node_id: str, snapshot: Snapshot | None = None) -> list[tuple[str, str]]:
    node = topology.nodes[node_id]
    failed = snapshot is not None and node_id in snapshot.failed_routers
    attached = []
    for link in topology.links_of(node_id):
        other = link.target if node_id == link.source else link.source
        down = snapshot is not None and link.link_id in snapshot.failed_links
        attached.append(f"{link.link_id} to {other} ({link.bandwidth_bps / 1e6:g} Mbps{', failed' if down else ''})")
    rows = [("Device", f"{node.name} ({node.node_id})"), ("Type", node.node_type.replace("_", " ")),
            ("IPv4", node.ip_address or "none"), ("Subnet", node.subnet or "none")]
    if node.node_type == "router":
        rows.append(("State", "failed" if failed else "up"))
    rows.append(("Links", "; ".join(attached)))
    return rows


def link_details(
    topology: Topology, link_id: str, snapshot: Snapshot | None = None, metrics: Metrics | None = None
) -> list[tuple[str, str]]:
    link = topology.links[link_id]
    failed = snapshot is not None and link_id in snapshot.failed_links
    rows = [("Link", f"{link.link_id} ({link.source} ↔ {link.target})"),
            ("Bandwidth", f"{link.bandwidth_bps / 1e6:g} Mbps"),
            ("Propagation delay", f"{link.propagation_delay_s * 1000:g} ms"),
            ("Routing cost", f"{link.routing_cost:g}"),
            ("Queue capacity", f"{link.queue_capacity_packets} packets per direction")]
    if snapshot is not None:
        rows.append(("State at this moment", "failed" if failed else "up"))
    for sender in (link.source, link.target):
        other = link.target if sender == link.source else link.source
        parts = []
        if snapshot is not None:
            port = snapshot.ports.get((link_id, sender))
            parts.append(f"now: {len(port.waiting) if port else 0}/{link.queue_capacity_packets} waiting"
                         + (", transmitting" if port and port.transmitting is not None else "")
                         + (f", {len(port.propagating)} propagating" if port and port.propagating else ""))
        if metrics is not None:
            m = metrics.links[(link_id, sender)]
            parts.append(f"whole run: utilization {fmt_pct(m.utilization)}, queue peak {m.max_queue_length}, queue-full drops {m.queue_drops}")
        if parts:
            rows.append((f"{sender} → {other}", "; ".join(parts)))
    return rows


def packet_details(replay: ReplayIndex, packet_id) -> list[tuple[str, str]]:
    p = replay.packets[packet_id]
    waits = 0.0
    queued_at: dict = {}
    for i in replay.journey(packet_id):
        r = replay.records[i]
        if r.event_type == "queued":
            queued_at[r.location] = r.timestamp_s
        elif r.event_type == "transmission_start" and r.location in queued_at:
            waits += r.timestamp_s - queued_at.pop(r.location)
    rows = [("Packet", f"{p.packet_id} ({TRAFFIC_CLASS_LABELS[p.traffic_class].lower()}, {p.size_bytes} B)"),
            ("From → to", f"{p.source_id} → {p.destination_id}"),
            ("Path (fixed at creation)", " → ".join(p.path) if p.path else "no route"),
            ("Created", f"{p.created_at_s:.4f} s"),
            ("Outcome", p.status.replace("_", " ") + (f" ({p.drop_reason})" if p.drop_reason else "")),
            ("Time waiting in queues", f"{waits * 1000:.2f} ms")]
    if p.delivered_at_s is not None:
        rows.append(("End-to-end delay", f"{(p.delivered_at_s - p.created_at_s) * 1000:.2f} ms"))
    return rows


def run_label(result: SimulationResult) -> str:
    c = result.config
    return f"{POLICY_LABELS[c.queue_policy]}, {c.duration_s:g} s, failure: {describe_failures(c)}"
