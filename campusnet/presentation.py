"""Data shaping for the dashboard: preset descriptions, tables, event-log filtering and CSV.

No simulation or metric logic lives here. Numbers come from the engine and metrics modules and
are only renamed, converted to display units, and arranged into tables. Tables are returned with
numeric values and units in the column names, so the same frame can be displayed and exported.
"""

from dataclasses import dataclass

import pandas as pd

from campusnet.config import ExperimentConfig
from campusnet.experiments import BOTTLENECK_LINK_ID, ExperimentResult
from campusnet.metrics import Metrics
from campusnet.simulator import SimulationResult
from campusnet.topology import Topology

NA = "N/A"


@dataclass(frozen=True)
class PresetInfo:
    title: str
    summary: str
    varies: str  # the one main variable (matches ExperimentResult.varied)
    fixed: list[str]
    how_to_read: str


PRESET_INFO: dict[str, PresetInfo] = {
    "A": PresetInfo(
        "Congestion demonstration",
        "Runs a lightly loaded and a heavily loaded network. At the heavy load the offered traffic "
        "(about 11.9 Mbps) exceeds the 10 Mbps r1-r2 link, which is the bottleneck.",
        "traffic intensity",
        ["Topology and link bandwidths", "FIFO queueing", "Queue capacity: 50 packets per link", "Random seed"],
        "Compare queue length, delay and queue-full drops at the bottleneck between the two runs.",
    ),
    "B": PresetInfo(
        "QoS comparison",
        "Runs the same congested workload once with FIFO and once with strict priority "
        "(realtime, then interactive, then bulk; FIFO within a class).",
        "queue policy",
        ["Topology and link bandwidths", "Workload (intensity 1.0)", "Queue capacity: 50 packets per link", "Random seed"],
        "Compare each traffic class between the two runs. Look at delay and loss, and at the "
        "starvation indicators (still waiting, longest wait) for bulk traffic.",
    ),
    "C": PresetInfo(
        "Bandwidth upgrade",
        "Repeats the congested workload with different capacities on the r1-r2 bottleneck link.",
        "bottleneck bandwidth (r1-r2)",
        ["Topology and other link bandwidths", "Workload (intensity 1.0)", "FIFO queueing", "Queue capacity: 50 packets per link", "Random seed"],
        "Compare throughput, delay, loss and bottleneck utilization across the four capacities.",
    ),
    "D": PresetInfo(
        "Link failure",
        "Fails one link halfway through the run. The redundant core link r1-r2 has backup uplinks, "
        "so traffic can be rerouted. The bridge link r2-portal is the portal's only connection, so "
        "the portal becomes unreachable. A no-failure baseline is included.",
        "failed link",
        ["Topology and link bandwidths", "Workload (intensity 0.4, below the bottleneck capacity)", "FIFO queueing", "Queue capacity: 50 packets per link", "Failure time: half the run", "Random seed"],
        "Routing results (paths before and after) are separate from packet outcomes (what happened to "
        "packets, by drop reason). The two failure runs differ from the baseline only by their failure. "
        "no_route: created after the failure with no path left. link_down: already on its path and reached "
        "the failed link, or was waiting in that link's queue (the queues are empty at this load, so the "
        "former).",
    ),
    "E": PresetInfo(
        "Queue capacity",
        "Repeats the congested workload with different queue sizes on every link.",
        "queue capacity (packets per link)",
        ["Topology and link bandwidths", "Workload (intensity 1.0)", "FIFO queueing", "Random seed"],
        "Compare queue-full drops against delay and packets still waiting at the end of the run.",
    ),
}

_SETTING_LABELS = {
    "intensity": "Traffic intensity (x reference)",
    "queue_policy": "Queue policy",
    "bottleneck_mbps": "Bottleneck (Mbps)",
    "queue_capacity_packets": "Queue capacity (packets)",
    "failure": "Failure",
}

TRAFFIC_CLASS_LABELS = {"realtime": "Realtime", "interactive": "Interactive", "bulk": "Bulk"}
POLICY_LABELS = {"fifo": "FIFO", "strict_priority": "Strict priority"}


# --- formatting helpers (None means "no data" and is shown as N/A) ---


def fmt_pct(ratio: float | None) -> str:
    return NA if ratio is None else f"{ratio * 100:.1f}%"


def fmt_ms(seconds: float | None) -> str:
    return NA if seconds is None else f"{seconds * 1000:.2f} ms"


def fmt_mbps(bps: float | None) -> str:
    return NA if bps is None else f"{bps / 1e6:.2f} Mbps"


# --- configuration ---


def offered_load_bps(config: ExperimentConfig) -> float:
    """Offered load of the configured flows while they are all active: sum of size_bits / interval."""
    return sum(f.packet_size_bytes * 8 / f.packet_interval_s for f in config.flows)


def describe_failures(config: ExperimentConfig) -> str:
    if not config.failure_events:
        return "none"
    return "; ".join(f"{e.kind} {e.target_id} at {e.time_s:g} s" for e in config.failure_events)


def config_table(runs: list) -> pd.DataFrame:
    """One row per run showing the configuration that produced it (accepts ExperimentRun objects)."""
    rows = []
    for run in runs:
        config, topology = run.config, run.result.topology
        bottleneck = topology.links.get(BOTTLENECK_LINK_ID)
        rows.append({
            "Run": run.label,
            "Queue policy": POLICY_LABELS[config.queue_policy],
            "Queue capacity (packets)": config.queue_capacity_packets,
            "Bottleneck r1-r2 (Mbps)": None if bottleneck is None else bottleneck.bandwidth_bps / 1e6,
            "Offered load (Mbps)": offered_load_bps(config) / 1e6,
            "Failure": describe_failures(config),
            "Duration (s)": config.duration_s,
            "Seed": config.seed,
        })
    return pd.DataFrame(rows)


def config_summary(run) -> str:
    """One-line description of the configuration of a run (an ExperimentRun)."""
    config = run.config
    bottleneck = run.result.topology.links[BOTTLENECK_LINK_ID].bandwidth_bps / 1e6
    return (
        f"{POLICY_LABELS[config.queue_policy]}, queue {config.queue_capacity_packets} packets per link, "
        f"bottleneck {bottleneck:g} Mbps, offered load {offered_load_bps(config) / 1e6:.1f} Mbps, "
        f"failure: {describe_failures(config)}, {config.duration_s:g} s, seed {config.seed}"
    )


def workload_table(config: ExperimentConfig) -> pd.DataFrame:
    return pd.DataFrame([
        {
            "Flow": f.flow_id,
            "Class": TRAFFIC_CLASS_LABELS[f.traffic_class],
            "Route": f"{f.source_id} → {f.destination_id}",
            "Packet size (bytes)": f.packet_size_bytes,
            "Interval (ms)": f.packet_interval_s * 1000,
            "Rate (Mbps)": f.packet_size_bytes * 8 / f.packet_interval_s / 1e6,
        }
        for f in config.flows
    ])


def topology_tables(topology: Topology) -> tuple[pd.DataFrame, pd.DataFrame]:
    nodes = pd.DataFrame([
        {"ID": n.node_id, "Name": n.name, "Type": n.node_type.replace("_", " "),
         "IPv4": n.ip_address or "", "Subnet": n.subnet or ""}
        for n in topology.nodes.values()
    ])
    links = pd.DataFrame([
        {"Link": l.link_id, "End A": l.source, "End B": l.target, "Bandwidth (Mbps)": l.bandwidth_bps / 1e6,
         "Propagation delay (ms)": l.propagation_delay_s * 1000, "Routing cost": l.routing_cost,
         "Queue capacity (packets)": l.queue_capacity_packets, "Status": "up" if l.is_up else "failed"}
        for l in topology.links.values()
    ])
    return nodes, links


# --- results ---


def _setting_columns(rows: list[dict]) -> list[str]:
    return [key for key in rows[0] if key in _SETTING_LABELS] if rows else []


def outcomes_table(experiment: ExperimentResult) -> pd.DataFrame:
    """Packet outcomes per run: counts, ratios and explicit drops by reason."""
    rows = experiment.rows()
    settings = _setting_columns(rows)
    reasons = [key for key in rows[0] if key.startswith("drops_")] if rows else []
    frame = pd.DataFrame([
        {
            "Run": row["run"],
            **{_SETTING_LABELS[k]: row[k] for k in settings},
            "Generated": row["generated"], "Delivered": row["delivered"], "Dropped": row["dropped"],
            "Unresolved": row["unresolved"],
            "PDR (%)": _pct(row["pdr"]), "PLR (%)": _pct(row["plr"]), "Unresolved (%)": _pct(row["unresolved_ratio"]),
            **{f"Dropped: {key[len('drops_'):]}": row[key] for key in reasons},
        }
        for row in rows
    ])
    return frame


def performance_table(experiment: ExperimentResult) -> pd.DataFrame:
    """Delay, throughput and r1->r2 bottleneck measurements per run."""
    return pd.DataFrame([
        {
            "Run": row["run"],
            "Avg delay (ms)": row["avg_delay_ms"],
            "Throughput (Mbps)": None if row["throughput_bps"] is None else row["throughput_bps"] / 1e6,
            "Bottleneck util. (%)": _pct(row["bottleneck_utilization"]),
            "Bottleneck avg queue (pkts)": row["bottleneck_avg_queue"],
            "Bottleneck max queue (pkts)": row["bottleneck_max_queue"],
            "Bottleneck drops (full)": row["bottleneck_queue_drops"],
        }
        for row in experiment.rows()
    ])


def experiment_export(experiment: ExperimentResult) -> pd.DataFrame:
    """Run-level summary for CSV export: the outcomes and performance tables joined on the run."""
    return outcomes_table(experiment).merge(performance_table(experiment), on="Run")


def routes_table(experiment: ExperimentResult) -> pd.DataFrame:
    """Routing behavior (preset D): each flow's path before and after the failure. Not packet outcomes."""
    return pd.DataFrame([
        {
            "Scenario": r["scenario"], "Flow": r["flow"],
            "Path before": " → ".join(r["path_before"]) if r["path_before"] else "unreachable",  # type: ignore[arg-type]
            "Path after": " → ".join(r["path_after"]) if r["path_after"] else "unreachable",  # type: ignore[arg-type]
            "Route changed": r["route_changed"], "Reachable after": r["reachable_after"],
        }
        for r in experiment.routes
    ])


def route_summary(topology: Topology, path: list[str]) -> dict[str, float]:
    """Hops, total routing cost, slowest link (Mbps) and summed propagation delay (ms) along a node path."""
    by_pair = {frozenset((l.source, l.target)): l for l in topology.links.values()}
    links = [by_pair[frozenset(pair)] for pair in zip(path, path[1:])]
    return {
        "hops": len(links),
        "cost": sum(l.routing_cost for l in links),
        "slowest_link_mbps": min((l.bandwidth_bps for l in links), default=0.0) / 1e6,
        "propagation_ms": sum(l.propagation_delay_s for l in links) * 1000,
    }


def class_table(experiment: ExperimentResult) -> pd.DataFrame:
    """Per-run, per-class results including the starvation indicators."""
    return _class_frame(experiment.class_rows())


def run_class_table(metrics: Metrics, run_label: str = "Custom run") -> pd.DataFrame:
    """Per-class results of one run, from its Metrics."""
    rows = []
    for name, g in metrics.by_class.items():
        rows.append({
            "run": run_label, "traffic_class": name, "generated": g.generated, "delivered": g.delivered,
            "dropped": g.dropped, "unresolved": g.unresolved, "pdr": g.pdr, "plr": g.plr,
            "avg_delay_ms": None if g.avg_delay_s is None else g.avg_delay_s * 1000,
            "throughput_bps": g.throughput_bps,
            "mean_queueing_delay_ms": None if g.mean_queueing_delay_s is None else g.mean_queueing_delay_s * 1000,
            "still_waiting": g.still_waiting, "max_wait_ms": g.max_wait_s * 1000,
        })
    return _class_frame(rows)


def class_delivery_view(frame: pd.DataFrame) -> pd.DataFrame:
    """Delivery columns of a class table (counts, ratios, delay, throughput); keeps the Run column if present."""
    keep = ["Run", "Class", "Generated", "Delivered", "Dropped", "Unresolved", "PDR (%)", "PLR (%)", "Avg delay (ms)", "Throughput (Mbps)"]
    return frame[[c for c in keep if c in frame.columns]]


def class_queueing_view(frame: pd.DataFrame) -> pd.DataFrame:
    """Queueing and starvation-indicator columns of a class table."""
    keep = ["Run", "Class", "Mean queueing delay per hop (ms)", "Still waiting at end (packets)", "Longest wait (ms)"]
    return frame[[c for c in keep if c in frame.columns]]


def _class_frame(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame([
        {
            "Run": r["run"], "Class": TRAFFIC_CLASS_LABELS[r["traffic_class"]],
            "Generated": r["generated"], "Delivered": r["delivered"], "Dropped": r["dropped"],
            "Unresolved": r["unresolved"], "PDR (%)": _pct(r["pdr"]), "PLR (%)": _pct(r["plr"]),
            "Avg delay (ms)": r["avg_delay_ms"],
            "Throughput (Mbps)": None if r["throughput_bps"] is None else r["throughput_bps"] / 1e6,
            "Mean queueing delay per hop (ms)": r["mean_queueing_delay_ms"],
            "Still waiting at end (packets)": r["still_waiting"], "Longest wait (ms)": r["max_wait_ms"],
        }
        for r in rows
    ])


def link_metrics_table(metrics: Metrics, topology: Topology, only_active: bool = True) -> pd.DataFrame:
    """Per directed link: utilization and queue statistics. Idle directions are omitted if only_active."""
    rows = []
    for (link_id, sender), m in metrics.links.items():
        link = topology.links[link_id]
        receiver = link.target if sender == link.source else link.source
        if only_active and m.busy_time_s == 0 and m.max_queue_length == 0:
            continue
        rows.append({
            "Link": link_id, "Direction": f"{sender} -> {receiver}", "Bandwidth (Mbps)": link.bandwidth_bps / 1e6,
            "Utilization (%)": _pct(m.utilization), "Avg queue (packets)": m.avg_queue_length,
            "Max queue (packets)": m.max_queue_length, "Queue-full drops": m.queue_drops,
        })
    return pd.DataFrame(rows)


def _pct(ratio: float | None) -> float | None:
    return None if ratio is None else ratio * 100


# --- event log ---

EVENT_DESCRIPTIONS = {
    "created": "packet created",
    "queued": "packet joined a waiting queue",
    "transmission_start": "packet started transmitting on a link",
    "transmitted": "packet finished transmitting on a link",
    "delivered": "packet reached its destination",
    "dropped": "packet dropped",
    "link_failed": "link failed",
    "router_failed": "router failed",
}


def event_log_frame(result: SimulationResult) -> pd.DataFrame:
    traffic_class = {p.packet_id: p.traffic_class for p in result.packets}
    frame = pd.DataFrame(
        [
            {
                "Time (s)": r.timestamp_s,
                "Event": r.event_type,
                "Packet": r.packet_id,
                "Class": traffic_class.get(r.packet_id, ""),
                "Location": r.location or "",
                "Sender": r.sender or "",
                "Queue (pkts)": r.queue_length,
                "Drop reason": r.drop_reason or "",
            }
            for r in result.records
        ],
        columns=["Time (s)", "Event", "Packet", "Class", "Location", "Sender", "Queue (pkts)", "Drop reason"],
    )
    # Whole-number columns with gaps (failure events have no packet) stay integers, not floats.
    frame["Packet"] = frame["Packet"].astype("Int64")
    frame["Queue (pkts)"] = frame["Queue (pkts)"].astype("Int64")
    return frame


def filter_event_log(
    frame: pd.DataFrame,
    events: list[str] | None = None,
    classes: list[str] | None = None,
    drop_reasons: list[str] | None = None,
    packet_id: int | None = None,
    location: str = "",
    time_range: tuple[float, float] | None = None,
) -> pd.DataFrame:
    """Apply the dashboard's event-log filters. An empty or None filter means no restriction."""
    mask = pd.Series(True, index=frame.index)
    if events:
        mask &= frame["Event"].isin(events)
    if classes:
        mask &= frame["Class"].isin(classes)
    if drop_reasons:
        mask &= frame["Drop reason"].isin(drop_reasons)
    if packet_id is not None:
        mask &= (frame["Packet"] == packet_id).fillna(False).astype(bool)
    if location.strip():
        mask &= frame["Location"].str.contains(location.strip(), case=False, regex=False)
    if time_range is not None:
        mask &= frame["Time (s)"].between(time_range[0], time_range[1])
    return frame[mask]


def to_csv_bytes(frame: pd.DataFrame) -> bytes:
    return frame.to_csv(index=False).encode("utf-8")


# --- replay tables ---


def _where(record) -> str:
    if not record.location:
        return ""
    return record.location if not record.sender else f"{record.location} ({record.sender})"


def events_window(replay, center: int, before: int = 4, after: int = 5) -> pd.DataFrame:
    """The recorded events around one event, for stepping through a run. `Now` marks the current one."""
    lo = max(0, center - before)
    hi = min(len(replay.records), max(center, 0) + after + 1)
    return pd.DataFrame([
        {
            "Now": "▶" if i == center else "",
            "#": i,
            "Time (s)": replay.records[i].timestamp_s,
            "Event": replay.records[i].event_type,
            "Packet": replay.records[i].packet_id,
            "Where": _where(replay.records[i]),
        }
        for i in range(lo, hi)
    ]).astype({"Packet": "Int64"}) if hi > lo else pd.DataFrame(columns=["Now", "#", "Time (s)", "Event", "Packet", "Where"])


def journey_table(replay, packet_id) -> pd.DataFrame:
    """Every recorded event of one packet, with a plain-language description."""
    rows = []
    for i in replay.journey(packet_id):
        r = replay.records[i]
        rows.append({"#": i, "Time (s)": r.timestamp_s, "Event": r.event_type, "Where": _where(r),
                     "Queue (pkts)": r.queue_length, "What happened": replay.explain(i)})
    return pd.DataFrame(rows, columns=["#", "Time (s)", "Event", "Where", "Queue (pkts)", "What happened"])


def flow_routes_table(replay) -> pd.DataFrame:
    """The paths each flow's packets actually took, with what happened to the packets on each path."""
    rows = []
    for flow_id, uses in replay.flow_routes().items():
        for use in uses:
            rows.append({
                "Flow": flow_id, "Path": " → ".join(use.path) if use.path else "no route", "Packets": use.packets,
                "First created (s)": use.first_created_s, "Last created (s)": use.last_created_s,
                "Delivered": use.delivered, "Dropped": use.dropped, "Unresolved": use.unresolved,
            })
    return pd.DataFrame(rows, columns=["Flow", "Path", "Packets", "First created (s)", "Last created (s)", "Delivered", "Dropped", "Unresolved"])


def failure_breakdown(replay) -> pd.DataFrame:
    """The same facts as failure_table, one case per row so they read top to bottom: Failure, Case, Packets."""
    cases = [
        ("on_the_link", "On the failed link at that moment (finish their hop)"),
        ("delivered", "...of those, later delivered"),
        ("dropped", "...of those, later dropped"),
        ("flushed", "Waiting in its queue: flushed (dropped at the failure)"),
        ("next_hop_down", "Dropped later on reaching a node whose next link was down"),
        ("reached_failed_router", "Dropped on reaching the failed router"),
        ("no_route", "Created afterwards with no route (dropped at creation)"),
    ]
    rows = []
    for row in replay.failure_summary():
        values = {"on_the_link": row["in_transit_at_failure"], "delivered": row["in_transit_delivered"],
                  "dropped": row["in_transit_dropped"], "flushed": row["flushed"], "next_hop_down": row["next_hop_down"],
                  "reached_failed_router": row["reached_failed_router"], "no_route": row["no_route"]}
        for key, label in cases:
            if key == "reached_failed_router" and row["kind"] == "link":
                continue  # only applies to a router failure
            rows.append({"Failure": f"{row['kind']} {row['target']} at {row['time_s']:g} s", "Case": label, "Packets": values[key]})
    return pd.DataFrame(rows, columns=["Failure", "Case", "Packets"])


def failure_table(replay) -> pd.DataFrame:
    """What happened to packets around each failure, separated by the engine's failure rules."""
    return pd.DataFrame([
        {
            "Failure": f"{row['kind']} {row['target']}", "At (s)": row["time_s"],
            "On the failed link at that moment": row["in_transit_at_failure"],
            "...then delivered": row["in_transit_delivered"], "...then dropped": row["in_transit_dropped"],
            "Flushed from its queue": row["flushed"], "Dropped at failed next hop": row["next_hop_down"],
            "Reached failed router": row["reached_failed_router"], "No route at creation": row["no_route"],
        }
        for row in replay.failure_summary()
    ])


# --- series for charts ---


def queue_length_series(result: SimulationResult, link_id: str, sender: str) -> list[tuple[float, int]]:
    """Step series (time_s, waiting packets) for one directed link, from time 0 to the end of the run."""
    points: list[tuple[float, int]] = [(0.0, 0)]
    for r in result.records:
        if r.location == link_id and r.sender == sender and r.queue_length is not None:
            points.append((r.timestamp_s, r.queue_length))
    points.append((result.duration_s, points[-1][1]))
    return points
