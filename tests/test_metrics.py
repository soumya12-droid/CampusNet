import pytest

from campusnet.config import ExperimentConfig, FailureEvent
from campusnet.experiments import base_config
from campusnet.metrics import GroupMetrics, calculate_metrics, compare_results, throughput_over_time
from campusnet.models import Flow
from campusnet.simulator import SimulationResult, run_simulation
from campusnet.timing import transmission_delay_s
from helpers import build_topology

SLOW = 8000.0  # 100 B packet -> exactly 0.1 s per packet
FORWARD = ("a-b", "a")
BACKWARD = ("a-b", "b")


def flow(flow_id: str, traffic_class: str = "interactive", src: str = "a", dst: str = "b", start: float = 0.0) -> Flow:
    """A single 100-byte packet at `start`."""
    return Flow(flow_id, src, dst, traffic_class, "udp_like", 100, start, start + 0.5, 1.0)  # type: ignore[arg-type]


def run(flows: list[Flow], duration: float = 1.0, policy: str = "fifo", capacity: int = 10, **extra: object) -> SimulationResult:
    config = ExperimentConfig(
        flows=flows, duration_s=duration, queue_policy=policy, queue_capacity_packets=capacity, **extra  # type: ignore[arg-type]
    )
    return run_simulation(config, build_topology([("a", "b", SLOW, 0.0, 1)]))


# --- hand-calculated example: three packets, delivered at 0.1, 0.2, 0.3 ---


def burst3(policy: str = "fifo") -> SimulationResult:
    return run([flow("f1", "bulk"), flow("f2", "bulk"), flow("f3", "realtime")], policy=policy)


def test_counts_ratios_delay_and_throughput_by_hand() -> None:
    overall = calculate_metrics(burst3()).overall
    assert (overall.generated, overall.delivered, overall.dropped, overall.unresolved) == (3, 3, 0, 0)
    assert overall.pdr == 1.0 and overall.plr == 0.0 and overall.unresolved_ratio == 0.0
    assert overall.avg_delay_s == pytest.approx((0.1 + 0.2 + 0.3) / 3)
    assert overall.throughput_bps == pytest.approx(3 * 800 / 1.0)  # 2400 bps over T = 1 s
    assert overall.drop_reasons == {}


def test_per_class_metrics_by_hand() -> None:
    by_class = calculate_metrics(burst3()).by_class
    assert set(by_class) == {"realtime", "interactive", "bulk"}
    assert by_class["realtime"].avg_delay_s == pytest.approx(0.3)
    assert (by_class["bulk"].generated, by_class["bulk"].avg_delay_s) == (2, pytest.approx(0.15))
    assert by_class["bulk"].throughput_bps == pytest.approx(2 * 800)
    quiet = by_class["interactive"]  # no interactive traffic: no data, not zeros pretending to be data
    assert (quiet.generated, quiet.pdr, quiet.plr, quiet.avg_delay_s, quiet.mean_queueing_delay_s) == (0, None, None, None, None)
    assert quiet.throughput_bps == 0.0 and quiet.max_wait_s == 0.0


def test_link_utilization_and_queue_statistics_by_hand() -> None:
    link = calculate_metrics(burst3()).links[FORWARD]
    assert link.busy_time_s == pytest.approx(0.3)
    assert link.utilization == pytest.approx(0.3)  # 0.3 s busy / 1.0 s
    # waiting packets: 2 during [0, 0.1), 1 during [0.1, 0.2), 0 afterwards -> (0.2 + 0.1) / 1.0
    assert link.avg_queue_length == pytest.approx(0.3)
    assert link.max_queue_length == 2 and link.queue_drops == 0


def test_the_unused_direction_of_a_link_is_idle_not_missing() -> None:
    link = calculate_metrics(burst3()).links[BACKWARD]
    assert (link.busy_time_s, link.utilization, link.avg_queue_length, link.max_queue_length) == (0.0, 0.0, 0.0, 0)


def test_queueing_delay_by_class_under_each_policy() -> None:
    fifo = calculate_metrics(burst3("fifo")).by_class
    # FIFO order p1(bulk) 0, p2(bulk) 0.1, p3(realtime) 0.2
    assert fifo["realtime"].mean_queueing_delay_s == pytest.approx(0.2)
    assert fifo["bulk"].mean_queueing_delay_s == pytest.approx(0.05)
    priority = calculate_metrics(burst3("strict_priority")).by_class
    # p1(bulk) 0, then realtime 0.1, then p2(bulk) 0.2
    assert priority["realtime"].mean_queueing_delay_s == pytest.approx(0.1)
    assert priority["bulk"].mean_queueing_delay_s == pytest.approx(0.1)
    assert priority["bulk"].max_wait_s == pytest.approx(0.2) and fifo["bulk"].max_wait_s == pytest.approx(0.1)


# --- unresolved packets, drops, empty runs ---


def test_unresolved_packets_are_not_counted_as_dropped() -> None:
    # Five packets at t=0, T=0.25: delivered at 0.1 and 0.2; #3 transmitting (0.2-0.25+); #4, #5 waiting.
    result = run([flow(f"f{i}") for i in range(5)], duration=0.25)
    metrics = calculate_metrics(result)
    overall = metrics.overall
    assert (overall.generated, overall.delivered, overall.dropped, overall.unresolved) == (5, 2, 0, 3)
    assert overall.pdr == pytest.approx(0.4) and overall.plr == 0.0 and overall.unresolved_ratio == pytest.approx(0.6)
    assert overall.pdr + overall.plr + overall.unresolved_ratio == pytest.approx(1.0)
    assert overall.avg_delay_s == pytest.approx(0.15)  # delivered packets only
    assert overall.throughput_bps == pytest.approx(2 * 800 / 0.25)
    assert overall.drop_reasons == {}


def test_in_progress_work_at_the_end_is_counted_up_to_the_end_time() -> None:
    metrics = calculate_metrics(run([flow(f"f{i}") for i in range(5)], duration=0.25))
    link = metrics.links[FORWARD]
    assert link.busy_time_s == pytest.approx(0.25) and link.utilization == pytest.approx(1.0)  # 0.1 + 0.1 + 0.05
    # waiting packets: 4 during [0, 0.1), 3 during [0.1, 0.2), 2 during [0.2, 0.25]
    assert link.avg_queue_length == pytest.approx((4 * 0.1 + 3 * 0.1 + 2 * 0.05) / 0.25)
    assert link.max_queue_length == 4
    overall = metrics.overall
    assert overall.still_waiting == 2 and overall.transmissions_started == 3
    assert overall.mean_queueing_delay_s == pytest.approx((0 + 0.1 + 0.2) / 3)  # completed waits only
    assert overall.max_wait_s == pytest.approx(0.25)  # the two packets still waiting have waited 0.25 s


def test_queue_overflow_drops_are_counted_by_reason_and_link() -> None:
    result = run([flow(f"f{i}") for i in range(4)], capacity=1)
    metrics = calculate_metrics(result)
    overall = metrics.overall
    assert (overall.delivered, overall.dropped, overall.unresolved) == (2, 2, 0)
    assert overall.pdr == 0.5 and overall.plr == 0.5
    assert overall.drop_reasons == {"queue_full": 2}
    link = metrics.links[FORWARD]
    assert link.queue_drops == 2 and link.max_queue_length == 1
    assert link.avg_queue_length == pytest.approx(0.1)  # 1 waiting packet for 0.1 s of the 1.0 s


def test_flush_drops_end_a_wait_without_counting_it_and_empty_the_queue_series() -> None:
    result = run([flow(f"f{i}") for i in range(3)], failure_events=[FailureEvent(0.05, "link", "a-b")])
    metrics = calculate_metrics(result)
    assert metrics.overall.drop_reasons == {"link_down": 2}
    assert metrics.overall.transmissions_started == 1 and metrics.overall.still_waiting == 0
    link = metrics.links[FORWARD]
    assert link.max_queue_length == 2
    assert link.avg_queue_length == pytest.approx(2 * 0.05 / 1.0)  # 2 waiting until the flush at 0.05
    assert link.queue_drops == 0  # flush drops are not queue-full drops


def test_empty_run_returns_none_instead_of_dividing_by_zero() -> None:
    metrics = calculate_metrics(run([]))
    for group in (metrics.overall, *metrics.by_class.values()):
        assert (group.generated, group.delivered, group.dropped, group.unresolved) == (0, 0, 0, 0)
        assert group.pdr is None and group.plr is None and group.unresolved_ratio is None
        assert group.avg_delay_s is None and group.mean_queueing_delay_s is None
        assert group.throughput_bps == 0.0 and group.still_waiting == 0 and group.max_wait_s == 0.0
    assert all(link.utilization == 0.0 and link.avg_queue_length == 0.0 for link in metrics.links.values())


def test_all_packets_dropped_gives_zero_pdr_and_no_average_delay() -> None:
    topology = build_topology([("a", "b", SLOW, 0.0, 1), ("c", "d", SLOW, 0.0, 1)])
    config = ExperimentConfig(flows=[flow("f", dst="d")], duration_s=1.0)
    overall = calculate_metrics(run_simulation(config, topology)).overall
    assert (overall.pdr, overall.plr, overall.avg_delay_s, overall.throughput_bps) == (0.0, 1.0, None, 0.0)
    assert overall.drop_reasons == {"no_route": 1}


def test_zero_length_measurement_interval_gives_none() -> None:
    topology = build_topology([("a", "b", SLOW, 0.0, 1)])
    metrics = calculate_metrics(SimulationResult(ExperimentConfig(), topology, [], [], 0.0))
    assert metrics.overall.throughput_bps is None
    link = metrics.links[FORWARD]
    assert link.utilization is None and link.avg_queue_length is None


# --- consistency between the simulation and its metrics ---


@pytest.fixture(scope="module")
def congested() -> SimulationResult:
    config = base_config(duration_s=1.0)
    config.queue_policy = "strict_priority"
    config.failure_events = [FailureEvent(0.5, "link", "r1-r2")]
    return run_simulation(config)


def test_metric_counts_match_simulation_counts(congested: SimulationResult) -> None:
    metrics = calculate_metrics(congested)
    overall = metrics.overall
    assert (overall.generated, overall.delivered, overall.dropped, overall.unresolved) == (
        congested.generated, congested.delivered, congested.dropped, congested.unresolved,
    )
    assert overall.generated == sum(1 for r in congested.records if r.event_type == "created")
    assert overall.delivered == sum(1 for r in congested.records if r.event_type == "delivered")
    assert overall.dropped == sum(1 for r in congested.records if r.event_type == "dropped")
    assert sum(overall.drop_reasons.values()) == overall.dropped
    assert overall.pdr + overall.plr + overall.unresolved_ratio == pytest.approx(1.0)


def test_classes_add_up_to_the_total(congested: SimulationResult) -> None:
    metrics = calculate_metrics(congested)
    classes = list(metrics.by_class.values())
    for name in ("generated", "delivered", "dropped", "unresolved", "transmissions_started", "still_waiting"):
        assert sum(getattr(c, name) for c in classes) == getattr(metrics.overall, name)
    assert sum(c.throughput_bps or 0 for c in classes) == pytest.approx(metrics.overall.throughput_bps)
    for reason, count in metrics.overall.drop_reasons.items():
        assert sum(c.drop_reasons.get(reason, 0) for c in classes) == count


def test_link_metrics_are_valid_and_agree_with_the_event_log(congested: SimulationResult) -> None:
    metrics = calculate_metrics(congested)
    assert metrics.overall.drop_reasons.get("queue_full", 0) == sum(l.queue_drops for l in metrics.links.values())
    assert any(l.queue_drops > 0 for l in metrics.links.values())
    for key, link in metrics.links.items():
        assert 0.0 <= (link.utilization or 0.0) <= 1.0
        assert link.avg_queue_length is not None and 0.0 <= link.avg_queue_length <= link.max_queue_length
        assert link.max_queue_length <= congested.config.queue_capacity_packets
        # independent check of busy time: serialization time of every packet whose transmission finished,
        # plus at most one packet still being serialized when the run ended
        bandwidth = congested.topology.links[key[0]].bandwidth_bps
        sizes = {p.packet_id: p.size_bytes for p in congested.packets}
        finished = sum(
            transmission_delay_s(sizes[r.packet_id], bandwidth)  # type: ignore[index]
            for r in congested.records
            if r.event_type == "transmitted" and (r.location, r.sender) == key
        )
        assert finished - 1e-9 <= link.busy_time_s <= finished + transmission_delay_s(1500, bandwidth) + 1e-9


# --- throughput over time and comparisons ---


def test_throughput_over_time_by_hand() -> None:
    # deliveries at 0.1, 0.2, 0.3 s (800 bits each), T = 1 s, bins of 0.25 s
    series = throughput_over_time(burst3(), 0.25)
    assert [start for start, _ in series] == [0.0, 0.25, 0.5, 0.75]
    assert [bps for _, bps in series] == pytest.approx([2 * 800 / 0.25, 800 / 0.25, 0.0, 0.0])


def test_throughput_over_time_covers_exactly_the_delivered_bits(congested: SimulationResult) -> None:
    delivered_bits = sum(p.size_bytes * 8 for p in congested.packets if p.status == "delivered")
    assert delivered_bits > 0
    for interval in (0.1, 0.3, 1.0, 2.5):
        series = throughput_over_time(congested, interval)
        bits = sum(bps * (min(start + interval, congested.duration_s) - start) for start, bps in series)
        assert bits == pytest.approx(delivered_bits)


def test_delivery_exactly_at_the_end_lands_in_the_last_bin() -> None:
    series = throughput_over_time(run([flow("f1"), flow("f2")], duration=0.2), 0.1)  # deliveries at 0.1 and 0.2
    assert sum(bps * 0.1 for _, bps in series) == pytest.approx(1600)


def test_throughput_over_time_rejects_bad_interval() -> None:
    with pytest.raises(ValueError):
        throughput_over_time(burst3(), 0)


def test_compare_results_reports_the_single_differing_field() -> None:
    comparison = compare_results([burst3("fifo"), burst3("strict_priority")])
    assert comparison.differing_fields == ["queue_policy"]
    assert len(comparison.metrics) == 2
    assert comparison.metrics[0].overall.generated == comparison.metrics[1].overall.generated


def test_compare_identical_configs_and_empty_input() -> None:
    assert compare_results([burst3(), burst3()]).differing_fields == []
    assert compare_results([]).metrics == [] and compare_results([]).differing_fields == []


def test_group_metrics_is_immutable() -> None:
    group = calculate_metrics(burst3()).overall
    assert isinstance(group, GroupMetrics)
    with pytest.raises(AttributeError):
        group.generated = 99  # type: ignore[misc]
