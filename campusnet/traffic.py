"""Deterministic traffic generation from configured flows (specification section 5.4)."""

from dataclasses import replace

from campusnet.config import ExperimentConfig, validate_flow
from campusnet.models import Flow


def generate_flows(config: ExperimentConfig) -> list[Flow]:
    """Return copies of the configured flows, in configuration order. Raises ValueError if any is invalid."""
    problems = [problem for flow in config.flows for problem in validate_flow(flow)]
    if problems:
        raise ValueError("; ".join(problems))
    return [replace(flow) for flow in config.flows]


def packet_times(flow: Flow, duration_s: float) -> list[float]:
    """Creation times of a flow's packets: start + k * interval for k = 0, 1, ...

    The window is half-open: a packet is created only at times strictly before both
    end_time_s and duration_s. Times are computed as start + k * interval (not by
    repeated addition) so they do not drift.
    """
    limit = min(flow.end_time_s, duration_s)
    times: list[float] = []
    k = 0
    while (t := flow.start_time_s + k * flow.packet_interval_s) < limit:
        times.append(t)
        k += 1
    return times
