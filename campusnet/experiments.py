"""Preset experiments A-E (specification section 4, Module 6).

Rules every preset follows:
- Same topology (the default campus topology) and the same base workload in every run
  of a preset; exactly one main variable changes between runs.
- A fixed seed, recorded in each run's config. The engine is deterministic and uses no
  randomness yet, so the seed does not change results.
- Each run keeps its full ExperimentConfig and SimulationResult next to its metrics.
- Presets only measure. Nothing here states which setting is better; read the results.

Base workload (all flows start at 0 and run for the whole duration; all use udp_like because
no retransmission model exists). Every flow crosses the r1 -> r2 core link, the bottleneck.
At intensity 1.0 the offered load is about 11.9 Mbps against that 10 Mbps link:

    flow        class        route                size    interval    ~rate
    lecture     realtime     classroom -> portal  1200 B  5 ms        1.92 Mbps
    web-1       interactive  stu1 -> portal       500 B   4 ms        1.00 Mbps
    download-1  bulk         stu3 -> files        1500 B  2 ms        6.00 Mbps
    download-2  bulk         stu4 -> files        1500 B  4 ms        3.00 Mbps

"Intensity" divides every flow's packet interval, so 0.5 sends half as many packets.
"""

import copy
from dataclasses import dataclass, field, replace

from campusnet.config import ExperimentConfig, FailureEvent
from campusnet.metrics import ComparisonResult, Metrics, calculate_metrics, compare_results
from campusnet.models import Flow
from campusnet.routing import shortest_path
from campusnet.simulator import SimulationResult, run_simulation
from campusnet.topology import build_default_topology, disable_link

DEFAULT_DURATION_S = 2.0
DEFAULT_SEED = 42
BOTTLENECK = ("r1-r2", "r1")  # (link_id, sending node): the core link in the client -> server direction
BOTTLENECK_LINK_ID = "r1-r2"
BOTTLENECK_BANDWIDTH_BPS = 10e6
BASE_QUEUE_CAPACITY = 50
HIGH_INTENSITY = 1.0

# (flow_id, source, destination, traffic_class, size_bytes, interval_s at intensity 1.0)
_BASE_FLOWS = (
    ("lecture", "classroom", "portal", "realtime", 1200, 0.005),
    ("web-1", "stu1", "portal", "interactive", 500, 0.004),
    ("download-1", "stu3", "files", "bulk", 1500, 0.002),
    ("download-2", "stu4", "files", "bulk", 1500, 0.004),
)


def standard_flows(intensity: float, duration_s: float) -> list[Flow]:
    if intensity <= 0:
        raise ValueError("intensity must be > 0")
    return [
        Flow(flow_id, source, destination, traffic_class, "udp_like", size, 0.0, duration_s, interval / intensity)  # type: ignore[arg-type]
        for flow_id, source, destination, traffic_class, size, interval in _BASE_FLOWS
    ]


def base_config(
    duration_s: float = DEFAULT_DURATION_S, seed: int = DEFAULT_SEED, intensity: float = HIGH_INTENSITY
) -> ExperimentConfig:
    return ExperimentConfig(
        topology="default",
        duration_s=duration_s,
        seed=seed,
        flows=standard_flows(intensity, duration_s),
        queue_policy="fifo",
        queue_capacity_packets=BASE_QUEUE_CAPACITY,
    )


@dataclass
class ExperimentRun:
    label: str
    settings: dict[str, object]  # the value(s) of the variable this run uses, for tables
    config: ExperimentConfig
    result: SimulationResult
    metrics: Metrics


@dataclass
class ExperimentResult:
    experiment_id: str
    title: str
    varied: str  # the one main variable
    held_constant: str
    runs: list[ExperimentRun]
    comparison: ComparisonResult
    routes: list[dict[str, object]] = field(default_factory=list)  # routing behavior (preset D); not packet outcomes
    notes: list[str] = field(default_factory=list)

    def rows(self) -> list[dict[str, object]]:
        """One row per run: settings, overall packet outcomes, and bottleneck-link measurements."""
        reasons = sorted({reason for run in self.runs for reason in run.metrics.overall.drop_reasons})
        rows = []
        for run in self.runs:
            overall, bottleneck = run.metrics.overall, run.metrics.links[BOTTLENECK]
            row: dict[str, object] = {"run": run.label, **run.settings}
            row.update(
                generated=overall.generated, delivered=overall.delivered, dropped=overall.dropped,
                unresolved=overall.unresolved, pdr=overall.pdr, plr=overall.plr,
                unresolved_ratio=overall.unresolved_ratio,
                avg_delay_ms=None if overall.avg_delay_s is None else overall.avg_delay_s * 1000,
                throughput_bps=overall.throughput_bps,
                bottleneck_utilization=bottleneck.utilization,
                bottleneck_avg_queue=bottleneck.avg_queue_length,
                bottleneck_max_queue=bottleneck.max_queue_length,
                bottleneck_queue_drops=bottleneck.queue_drops,
            )
            row.update({f"drops_{reason}": overall.drop_reasons.get(reason, 0) for reason in reasons})
            rows.append(row)
        return rows

    def class_rows(self) -> list[dict[str, object]]:
        """One row per run and traffic class."""
        rows = []
        for run in self.runs:
            for name, group in run.metrics.by_class.items():
                rows.append({
                    "run": run.label, **run.settings, "traffic_class": name,
                    "generated": group.generated, "delivered": group.delivered, "dropped": group.dropped,
                    "unresolved": group.unresolved, "pdr": group.pdr, "plr": group.plr,
                    "avg_delay_ms": None if group.avg_delay_s is None else group.avg_delay_s * 1000,
                    "throughput_bps": group.throughput_bps,
                    "mean_queueing_delay_ms": (
                        None if group.mean_queueing_delay_s is None else group.mean_queueing_delay_s * 1000
                    ),
                    "still_waiting": group.still_waiting, "max_wait_ms": group.max_wait_s * 1000,
                })
        return rows


def _variant(base: ExperimentConfig, **changes: object) -> ExperimentConfig:
    return replace(copy.deepcopy(base), **changes)  # type: ignore[arg-type]


def _run(label: str, settings: dict[str, object], config: ExperimentConfig) -> ExperimentRun:
    result = run_simulation(config)
    return ExperimentRun(label, settings, config, result, calculate_metrics(result))


def _finish(
    experiment_id: str, title: str, varied: str, held_constant: str, runs: list[ExperimentRun], **extra: object
) -> ExperimentResult:
    comparison = compare_results([run.result for run in runs])
    return ExperimentResult(experiment_id, title, varied, held_constant, runs, comparison, **extra)  # type: ignore[arg-type]


def run_preset_a(duration_s: float = DEFAULT_DURATION_S, seed: int = DEFAULT_SEED) -> ExperimentResult:
    """A - Congestion demonstration: low vs high traffic load."""
    runs = []
    for label, intensity in (("low load", 0.4), ("high load", HIGH_INTENSITY)):
        config = base_config(duration_s, seed, intensity)
        runs.append(_run(label, {"intensity": intensity}, config))
    return _finish(
        "A", "Congestion demonstration", "traffic intensity",
        "topology, FIFO policy, queue capacity 50, bandwidths, seed", runs,
        notes=["Intensity 1.0 offers more than the 10 Mbps r1-r2 link can carry; 0.4 offers well under it."],
    )


def run_preset_b(duration_s: float = DEFAULT_DURATION_S, seed: int = DEFAULT_SEED) -> ExperimentResult:
    """B - QoS comparison: FIFO vs strict priority under the same workload and seed."""
    runs = []
    for policy in ("fifo", "strict_priority"):
        runs.append(_run(policy, {"queue_policy": policy}, _variant(base_config(duration_s, seed), queue_policy=policy)))
    return _finish(
        "B", "QoS comparison", "queue policy",
        "topology, workload (intensity 1.0), queue capacity 50, bandwidths, seed", runs,
        notes=["Compare real-time and bulk rows in class_rows(); strict priority can trade bulk service for realtime service."],
    )


def run_preset_c(duration_s: float = DEFAULT_DURATION_S, seed: int = DEFAULT_SEED) -> ExperimentResult:
    """C - Bandwidth upgrade: bottleneck (r1-r2) bandwidth varied."""
    runs = []
    for mbps in (5, 10, 20, 40):
        config = _variant(base_config(duration_s, seed), bandwidth_overrides={BOTTLENECK_LINK_ID: mbps * 1e6})
        runs.append(_run(f"{mbps} Mbps", {"bottleneck_mbps": mbps}, config))
    return _finish(
        "C", "Bandwidth upgrade", "bottleneck bandwidth (r1-r2)",
        "topology, workload (intensity 1.0), FIFO policy, queue capacity 50, seed", runs,
    )


def run_preset_d(duration_s: float = DEFAULT_DURATION_S, seed: int = DEFAULT_SEED) -> ExperimentResult:
    """D - Link failure: a redundant link and a bridge link, each against a no-failure baseline.

    `routes` shows what routing does to each flow's path (route recalculation); the runs' packet
    outcomes (delivered, dropped by reason) show what actually happened to packets.

    Packets already in the network keep the path chosen when they were created, and the engine
    can drop them for a failure in two different ways:
      * flushed: the packet was waiting in the output queue of a failed link and is dropped at
        the failure time (link_down; router_down for a router failure);
      * dropped later: the packet reaches a node whose next-hop link is down and is dropped there
        (link_down), or reaches a failed router (router_down).
    Packets created after the failure are routed on the updated topology; if no path remains they
    are dropped at creation (no_route).

    At this preset's low intensity the queues are empty when the failure happens, so no packet is
    flushed. The link_down drops in these runs are all of the second kind: a packet created just
    before the failure that reached the failed link afterwards. The flush rule is covered by the
    engine tests, not by this preset.
    """
    # Moderate intensity so drops come from the failure, not from congestion.
    base = base_config(duration_s, seed, intensity=0.4)
    fail_at = duration_s / 2
    cases = (
        ("redundant link r1-r2 fails", "r1-r2"),  # sw1-r2 / sw2-r2 backup uplinks exist
        ("bridge link r2-portal fails", "r2-portal"),  # the portal has no other connection
    )
    runs = [_run("baseline (no failure)", {"failure": "none"}, base)]
    routes: list[dict[str, object]] = []
    for label, link_id in cases:
        config = _variant(base, failure_events=[FailureEvent(fail_at, "link", link_id)])
        runs.append(_run(label, {"failure": f"{link_id} at {fail_at:g} s"}, config))
        intact, broken = build_default_topology(), build_default_topology()
        disable_link(broken, link_id)
        for flow in base.flows:
            before = shortest_path(intact, flow.source_id, flow.destination_id)
            after = shortest_path(broken, flow.source_id, flow.destination_id)
            routes.append({
                "scenario": label, "failed_link": link_id, "flow": flow.flow_id,
                "path_before": before, "path_after": after,
                "route_changed": before != after, "reachable_after": after is not None,
            })
    return _finish(
        "D", "Link failure", "failed link",
        "topology, workload (intensity 0.4), FIFO policy, queue capacity 50, failure time, seed", runs,
        routes=routes,
        notes=[
            "routes = routing behavior only. Packet outcomes are in rows(). no_route drops are packets "
            "created after the failure with no remaining path. link_down drops are packets that were "
            "already on a fixed path and reached the failed link, or were flushed from its waiting queue; "
            "at this intensity the queues are empty at the failure, so only the first kind occurs.",
        ],
    )


def run_preset_e(duration_s: float = DEFAULT_DURATION_S, seed: int = DEFAULT_SEED) -> ExperimentResult:
    """E - Queue capacity: waiting-room size on every link varied."""
    runs = []
    for capacity in (5, 20, 50, 200):
        config = _variant(base_config(duration_s, seed), queue_capacity_packets=capacity)
        runs.append(_run(f"capacity {capacity}", {"queue_capacity_packets": capacity}, config))
    return _finish(
        "E", "Queue capacity", "queue capacity (packets per link)",
        "topology, workload (intensity 1.0), FIFO policy, bandwidths, seed", runs,
    )


PRESETS = {"A": run_preset_a, "B": run_preset_b, "C": run_preset_c, "D": run_preset_d, "E": run_preset_e}


def run_preset(experiment_id: str, duration_s: float = DEFAULT_DURATION_S, seed: int = DEFAULT_SEED) -> ExperimentResult:
    if experiment_id not in PRESETS:
        raise ValueError(f"unknown preset {experiment_id!r}; choose from {sorted(PRESETS)}")
    return PRESETS[experiment_id](duration_s, seed)
