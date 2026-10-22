"""Experiment configuration defaults and validation (specification section 6.3)."""

import math
from dataclasses import dataclass, field
from typing import Literal, get_args

from campusnet.models import TRAFFIC_CLASSES, TRANSPORT_MODES, Flow

QueuePolicy = Literal["fifo", "strict_priority"]
FailureKind = Literal["link", "router"]

QUEUE_POLICIES: tuple[str, ...] = get_args(QueuePolicy)
FAILURE_KINDS: tuple[str, ...] = get_args(FailureKind)

DEFAULT_QUEUE_CAPACITY_PACKETS = 50
DEFAULT_DURATION_S = 10.0
DEFAULT_SEED = 1
DEFAULT_RETRY_LIMIT = 3


@dataclass(frozen=True)
class FailureEvent:
    time_s: float
    kind: FailureKind
    target_id: str  # link_id or router node_id


@dataclass
class ExperimentConfig:
    topology: str = "default"
    duration_s: float = DEFAULT_DURATION_S
    seed: int = DEFAULT_SEED
    flows: list[Flow] = field(default_factory=list)
    queue_policy: QueuePolicy = "fifo"
    queue_capacity_packets: int = DEFAULT_QUEUE_CAPACITY_PACKETS
    bandwidth_overrides: dict[str, float] = field(default_factory=dict)  # link_id -> bps
    failure_events: list[FailureEvent] = field(default_factory=list)
    retry_limit: int = DEFAULT_RETRY_LIMIT  # only used once a retransmission model exists


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _is_count(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def validate_flow(flow: Flow) -> list[str]:
    problems: list[str] = []
    name = f"flow {flow.flow_id}"
    if flow.traffic_class not in TRAFFIC_CLASSES:
        problems.append(f"{name}: invalid traffic_class {flow.traffic_class!r}")
    if flow.transport_mode not in TRANSPORT_MODES:
        problems.append(f"{name}: invalid transport_mode {flow.transport_mode!r}")
    if not _is_count(flow.packet_size_bytes) or flow.packet_size_bytes <= 0:
        problems.append(f"{name}: packet_size_bytes must be a positive integer")
    if not _is_number(flow.start_time_s) or flow.start_time_s < 0:
        problems.append(f"{name}: start_time_s must be >= 0")
    elif not _is_number(flow.end_time_s) or flow.end_time_s < flow.start_time_s:
        problems.append(f"{name}: end_time_s must be >= start_time_s")
    if not _is_number(flow.packet_interval_s) or flow.packet_interval_s <= 0:
        problems.append(f"{name}: packet_interval_s must be > 0")
    return problems


def validate_config(config: ExperimentConfig) -> list[str]:
    """Return a list of problems; an empty list means the config is valid.

    References to topology IDs (flow endpoints, failure targets, bandwidth
    override keys) are not checked here because the config holds no topology.
    """
    problems: list[str] = []
    if not _is_number(config.duration_s) or config.duration_s <= 0:
        problems.append("duration_s must be > 0")
    if not _is_count(config.seed):
        problems.append("seed must be an integer")
    if config.queue_policy not in QUEUE_POLICIES:
        problems.append(f"invalid queue_policy {config.queue_policy!r}")
    if not _is_count(config.queue_capacity_packets) or config.queue_capacity_packets < 0:
        problems.append("queue_capacity_packets must be an integer >= 0")
    if not _is_count(config.retry_limit) or config.retry_limit < 0:
        problems.append("retry_limit must be an integer >= 0")
    for link_id, bps in config.bandwidth_overrides.items():
        if not _is_number(bps) or bps <= 0:
            problems.append(f"bandwidth override for {link_id}: must be > 0 bps")
    for event in config.failure_events:
        if event.kind not in FAILURE_KINDS:
            problems.append(f"failure event on {event.target_id}: invalid kind {event.kind!r}")
        if not _is_number(event.time_s) or not 0 <= event.time_s <= config.duration_s:
            problems.append(f"failure event on {event.target_id}: time_s must be within [0, duration_s]")
    for flow in config.flows:
        problems.extend(validate_flow(flow))
    return problems
