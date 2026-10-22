import time

import pytest

from campusnet.config import ExperimentConfig, FailureEvent
from campusnet.models import Flow
from campusnet.simulator import SimulationResult, run_simulation
from campusnet.topology import build_default_topology
from helpers import build_topology

# 100-byte packets on an 8000 bps link serialize in exactly 0.1 s (800 bits / 8000 bps).
SLOW = 8000.0


def flow(
    flow_id: str, src: str, dst: str, start: float = 0.0, count: int = 1, interval: float = 1.0,
    traffic_class: str = "interactive", size: int = 100,
) -> Flow:
    """A flow creating exactly `count` packets at start, start+interval, ..."""
    return Flow(flow_id, src, dst, traffic_class, "udp_like", size, start, start + (count - 0.5) * interval, interval)  # type: ignore[arg-type]


def config(flows: list[Flow], duration: float = 10.0, policy: str = "fifo", capacity: int = 10, **extra: object) -> ExperimentConfig:
    return ExperimentConfig(
        flows=flows, duration_s=duration, queue_policy=policy, queue_capacity_packets=capacity, **extra  # type: ignore[arg-type]
    )


def kinds(result: SimulationResult, packet_id: int) -> list[str]:
    return [r.event_type for r in result.records if r.packet_id == packet_id]


def assert_consistent(result: SimulationResult) -> None:
    """Internal consistency of counts, terminal states and the event log."""
    assert result.generated == result.delivered + result.dropped + result.unresolved
    assert [p.packet_id for p in result.packets] == list(range(1, result.generated + 1))
    times = [r.timestamp_s for r in result.records]
    assert times == sorted(times)
    assert sum(1 for r in result.records if r.event_type == "created") == result.generated
    for packet in result.packets:
        sequence = kinds(result, packet.packet_id)
        assert sequence[0] == "created"
        terminals = [k for k in sequence if k in ("delivered", "dropped")]
        if packet.status == "delivered":
            assert terminals == ["delivered"] and packet.drop_reason is None
            assert packet.delivered_at_s is not None and packet.delivered_at_s >= packet.created_at_s
        elif packet.status == "dropped":
            assert terminals == ["dropped"] and packet.drop_reason and packet.delivered_at_s is None
            drop = next(r for r in result.records if r.packet_id == packet.packet_id and r.event_type == "dropped")
            assert drop.drop_reason == packet.drop_reason
        else:
            assert terminals == [] and packet.status in ("queued", "in_transit", "created")
            assert packet.drop_reason is None and packet.delivered_at_s is None


# --- end to end, hand calculated ---


def test_single_packet_over_two_hops_hand_calculated() -> None:
    topology = build_topology([("a", "b", SLOW, 0.05, 1), ("b", "c", 2 * SLOW, 0.0, 1)])
    result = run_simulation(config([flow("f", "a", "c")]), topology)
    (packet,) = result.packets
    # hop 1: tx 0.1, prop 0.05 -> reaches b at 0.15; hop 2: tx 0.05, prop 0 -> reaches c at 0.20
    assert packet.path == ["a", "b", "c"]
    assert packet.status == "delivered"
    assert packet.delivered_at_s == pytest.approx(0.20)
    assert (result.generated, result.delivered, result.dropped, result.unresolved) == (1, 1, 0, 0)
    assert kinds(result, 1) == [
        "created", "transmission_start", "transmitted", "transmission_start", "transmitted", "delivered",
    ]
    assert_consistent(result)


def test_default_topology_end_to_end_hand_calculated() -> None:
    result = run_simulation(config([flow("f", "stu1", "portal", size=1500)], capacity=50))
    (packet,) = result.packets
    assert packet.path == ["stu1", "sw1", "r1", "r2", "portal"]
    # transmission: 3 x 12000/100e6 + 12000/10e6 = 0.00156; propagation: 0.0001+0.0005+0.002+0.0001 = 0.0027
    assert packet.delivered_at_s == pytest.approx(0.00426)
    assert_consistent(result)


def test_port_events_record_the_sending_direction() -> None:
    topology = build_topology([("a", "b", SLOW, 0.0, 1)])
    forward = flow("fwd", "a", "b")
    backward = flow("bwd", "b", "a")
    result = run_simulation(config([forward, backward]), topology)
    senders = {(r.packet_id, r.event_type): r.sender for r in result.records if r.location == "a-b"}
    assert senders[(1, "transmission_start")] == senders[(1, "transmitted")] == "a"
    assert senders[(2, "transmission_start")] == senders[(2, "transmitted")] == "b"
    # events that are not about an output port carry no sender
    assert all(r.sender is None for r in result.records if r.event_type in ("created", "delivered"))


def test_queue_full_and_flush_drops_record_the_sending_direction() -> None:
    topology = build_topology([("a", "b", SLOW, 0.0, 1)])
    flows = [flow(f"f{i}", "a", "b") for i in (1, 2, 3)]
    cfg = config(flows, capacity=1, failure_events=[FailureEvent(0.05, "link", "a-b")])
    drops = [r for r in run_simulation(cfg, topology).records if r.event_type == "dropped"]
    assert {(r.drop_reason, r.sender) for r in drops} == {("queue_full", "a"), ("link_down", "a")}


def test_packets_are_routed_through_queues_and_wait_for_the_busy_link() -> None:
    # Burst of 3 at t=0. Hop 1 (tx 0.1, prop 0.05): reach b at 0.15, 0.25, 0.35.
    # Hop 2 (tx 0.05): starts 0.15, 0.25, 0.35 -> delivered at 0.20, 0.30, 0.40.
    topology = build_topology([("a", "b", SLOW, 0.05, 1), ("b", "c", 2 * SLOW, 0.0, 1)])
    flows = [flow(f"f{i}", "a", "c") for i in (1, 2, 3)]
    result = run_simulation(config(flows), topology)
    assert [p.delivered_at_s for p in result.packets] == pytest.approx([0.20, 0.30, 0.40])
    assert kinds(result, 2) == [
        "created", "queued", "transmission_start", "transmitted", "transmission_start", "transmitted", "delivered",
    ]
    queued = [r for r in result.records if r.event_type == "queued"]
    assert [(r.packet_id, r.queue_length, r.location) for r in queued] == [(2, 1, "a-b"), (3, 2, "a-b")]
    assert_consistent(result)


def test_queue_overflow_drops_arriving_packets_with_reason() -> None:
    topology = build_topology([("a", "b", SLOW, 0.0, 1)])
    flows = [flow(f"f{i}", "a", "b") for i in (1, 2, 3, 4)]
    result = run_simulation(config(flows, capacity=1), topology)
    assert [p.status for p in result.packets] == ["delivered", "delivered", "dropped", "dropped"]
    assert [p.drop_reason for p in result.packets[2:]] == ["queue_full", "queue_full"]
    assert [p.delivered_at_s for p in result.packets[:2]] == pytest.approx([0.1, 0.2])
    drops = [r for r in result.records if r.event_type == "dropped"]
    assert all(r.location == "a-b" and r.queue_length == 1 and r.timestamp_s == 0.0 for r in drops)
    assert (result.generated, result.delivered, result.dropped, result.unresolved) == (4, 2, 2, 0)
    assert_consistent(result)


@pytest.mark.parametrize(
    ("policy", "expected_order"), [("fifo", [1, 2, 3]), ("strict_priority", [1, 3, 2])]
)
def test_scheduling_policy_through_the_engine(policy: str, expected_order: list[int]) -> None:
    topology = build_topology([("a", "b", SLOW, 0.0, 1)])
    flows = [
        flow("bulk1", "a", "b", traffic_class="bulk"),
        flow("bulk2", "a", "b", traffic_class="bulk"),
        flow("rt", "a", "b", traffic_class="realtime"),
    ]
    result = run_simulation(config(flows, policy=policy), topology)
    by_delivery = sorted(result.packets, key=lambda p: p.delivered_at_s or 0.0)
    # packet 1 already holds the server, so priority never preempts it
    assert [p.packet_id for p in by_delivery] == expected_order
    assert [p.delivered_at_s for p in by_delivery] == pytest.approx([0.1, 0.2, 0.3])


# --- same-time event ordering ---


def test_transmission_completion_is_processed_before_a_same_instant_arrival() -> None:
    # Capacity 0: a packet is accepted only if the server is idle. Packet 1 finishes at exactly
    # 0.1 and packet 2 is generated at exactly 0.1, so the server must already be free.
    topology = build_topology([("a", "b", SLOW, 0.0, 1)])
    result = run_simulation(config([flow("f1", "a", "b"), flow("f2", "a", "b", start=0.1)], capacity=0), topology)
    assert [p.status for p in result.packets] == ["delivered", "delivered"]
    assert result.packets[1].delivered_at_s == pytest.approx(0.2)


def test_arrival_while_the_server_is_still_busy_is_dropped_when_capacity_is_zero() -> None:
    topology = build_topology([("a", "b", SLOW, 0.0, 1)])
    result = run_simulation(config([flow("f1", "a", "b"), flow("f2", "a", "b", start=0.05)], capacity=0), topology)
    assert [p.status for p in result.packets] == ["delivered", "dropped"]
    assert result.packets[1].drop_reason == "queue_full"


def test_failure_is_processed_before_a_packet_generated_at_the_same_instant() -> None:
    topology = build_topology([("a", "b", SLOW, 0.0, 1), ("a", "c", SLOW, 0.0, 1), ("c", "b", SLOW, 0.0, 1)])
    flows = [flow("f", "a", "b", count=2, interval=0.5)]
    result = run_simulation(config(flows, failure_events=[FailureEvent(0.5, "link", "a-b")]), topology)
    first, second = result.packets
    assert first.path == ["a", "b"]  # created at 0.0, before the failure
    assert second.path == ["a", "c", "b"]  # created at 0.5, the instant of the failure
    assert second.status == "delivered"
    assert_consistent(result)


# --- failure handling ---


def test_packets_waiting_on_a_failed_link_are_dropped_and_the_transmitting_one_finishes() -> None:
    # a-b: tx 0.1, prop 0.05. Packets 2 and 3 wait; the link fails at 0.05 while packet 1 transmits.
    topology = build_topology([("a", "b", SLOW, 0.05, 1), ("a", "c", SLOW, 0.0, 5), ("c", "b", SLOW, 0.0, 5)])
    flows = [flow(f"f{i}", "a", "b") for i in (1, 2, 3)] + [flow("late", "a", "b", start=0.2)]
    result = run_simulation(config(flows, failure_events=[FailureEvent(0.05, "link", "a-b")]), topology)
    p1, p2, p3, late = result.packets
    assert p1.status == "delivered" and p1.delivered_at_s == pytest.approx(0.15)
    for waiting in (p2, p3):
        assert waiting.status == "dropped" and waiting.drop_reason == "link_down"
        assert waiting.path == ["a", "b"]  # the path was fixed at creation and not recomputed
    drops = [r for r in result.records if r.event_type == "dropped"]
    assert [(r.timestamp_s, r.location) for r in drops] == [(0.05, "a-b"), (0.05, "a-b")]
    # created after the failure -> routed on the updated topology
    assert late.path == ["a", "c", "b"] and late.delivered_at_s == pytest.approx(0.4)
    assert [r.event_type for r in result.records if r.packet_id is None] == ["link_failed"]
    assert_consistent(result)


def test_packet_reaching_a_node_whose_next_link_is_down_is_dropped() -> None:
    # The packet reaches b at 0.15; link b-c failed at 0.12.
    topology = build_topology([("a", "b", SLOW, 0.05, 1), ("b", "c", SLOW, 0.0, 1)])
    result = run_simulation(config([flow("f", "a", "c")], failure_events=[FailureEvent(0.12, "link", "b-c")]), topology)
    (packet,) = result.packets
    assert packet.status == "dropped" and packet.drop_reason == "link_down"
    drop = next(r for r in result.records if r.event_type == "dropped")
    assert drop.timestamp_s == pytest.approx(0.15) and drop.location == "b-c"
    assert_consistent(result)


def test_packet_reaching_a_failed_router_is_dropped() -> None:
    topology = build_topology([("a", "r", SLOW, 0.05, 1), ("r", "c", SLOW, 0.0, 1)], routers=("r",))
    result = run_simulation(config([flow("f", "a", "c")], failure_events=[FailureEvent(0.12, "router", "r")]), topology)
    (packet,) = result.packets
    assert packet.status == "dropped" and packet.drop_reason == "router_down"
    drop = next(r for r in result.records if r.event_type == "dropped")
    assert drop.location == "r" and drop.timestamp_s == pytest.approx(0.15)
    assert any(r.event_type == "router_failed" and r.location == "r" for r in result.records)
    assert_consistent(result)


def test_packets_waiting_at_a_failed_router_are_dropped_and_its_transmission_finishes() -> None:
    # a-r is fast (tx 0.01) so all three packets reach r by 0.03; r-c is slow (tx 0.1).
    # Packet 1 starts on r-c at 0.01; packets 2 and 3 wait. Router r fails at 0.05.
    topology = build_topology([("a", "r", 10 * SLOW, 0.0, 1), ("r", "c", SLOW, 0.0, 1)], routers=("r",))
    flows = [flow(f"f{i}", "a", "c") for i in (1, 2, 3)]
    result = run_simulation(config(flows, failure_events=[FailureEvent(0.05, "router", "r")]), topology)
    p1, p2, p3 = result.packets
    assert p1.status == "delivered" and p1.delivered_at_s == pytest.approx(0.11)
    assert [(p.status, p.drop_reason) for p in (p2, p3)] == [("dropped", "router_down")] * 2
    assert_consistent(result)


def test_no_route_at_creation_is_created_then_dropped() -> None:
    topology = build_topology([("a", "b", SLOW, 0.0, 1), ("c", "d", SLOW, 0.0, 1)])
    result = run_simulation(config([flow("f", "a", "d")]), topology)
    (packet,) = result.packets
    assert packet.status == "dropped" and packet.drop_reason == "no_route" and packet.path == []
    assert kinds(result, 1) == ["created", "dropped"]
    assert (result.generated, result.dropped) == (1, 1)


def test_destination_becomes_unreachable_after_a_failure() -> None:
    topology = build_topology([("a", "b", SLOW, 0.0, 1)])
    flows = [flow("f", "a", "b", count=2, interval=1.0)]
    result = run_simulation(config(flows, failure_events=[FailureEvent(0.5, "link", "a-b")]), topology)
    assert [(p.status, p.drop_reason) for p in result.packets] == [("delivered", None), ("dropped", "no_route")]


# --- end of run, counts, determinism ---


def test_packets_still_in_the_network_at_the_end_are_unresolved_not_dropped() -> None:
    topology = build_topology([("a", "b", SLOW, 0.0, 1)])
    flows = [flow(f"f{i}", "a", "b") for i in range(5)]
    result = run_simulation(config(flows, duration=0.25), topology)
    # deliveries at 0.1 and 0.2; packet 3 is transmitting (0.2-0.3); packets 4 and 5 wait
    assert (result.generated, result.delivered, result.dropped, result.unresolved) == (5, 2, 0, 3)
    assert [p.status for p in result.packets] == ["delivered", "delivered", "in_transit", "queued", "queued"]
    assert_consistent(result)


def test_event_exactly_at_the_end_time_is_processed() -> None:
    topology = build_topology([("a", "b", SLOW, 0.0, 1)])
    flows = [flow("f1", "a", "b"), flow("f2", "a", "b")]
    assert run_simulation(config(flows, duration=0.2), topology).delivered == 2
    assert run_simulation(config(flows, duration=0.19), topology).delivered == 1


def test_run_with_no_flows_is_empty_and_consistent() -> None:
    result = run_simulation(config([]), build_topology([("a", "b", SLOW, 0.0, 1)]))
    assert (result.generated, result.delivered, result.dropped, result.unresolved) == (0, 0, 0, 0)
    assert result.records == []


def test_flow_starting_at_or_after_the_end_creates_no_packets() -> None:
    topology = build_topology([("a", "b", SLOW, 0.0, 1)])
    flows = [flow("at_end", "a", "b", start=1.0), flow("after_end", "a", "b", start=2.0)]
    assert run_simulation(config(flows, duration=1.0), topology).generated == 0


def test_failure_exactly_at_the_end_time_is_processed_and_drops_waiting_packets() -> None:
    topology = build_topology([("a", "b", SLOW, 0.0, 1)])
    flows = [flow(f"f{i}", "a", "b") for i in (1, 2, 3)]
    # packet 1 finishes at 0.1; packet 2 is transmitting 0.1-0.2; packet 3 is still waiting at t=0.2
    # Failure and completion both fall at 0.2: the failure goes first, so packet 3 is dropped, not started.
    result = run_simulation(config(flows, duration=0.2, failure_events=[FailureEvent(0.2, "link", "a-b")]), topology)
    assert [p.status for p in result.packets] == ["delivered", "delivered", "dropped"]
    assert result.packets[2].drop_reason == "link_down"
    assert (result.generated, result.delivered, result.dropped, result.unresolved) == (3, 2, 1, 0)
    assert_consistent(result)


def test_transmission_still_in_progress_at_the_end_stays_in_transit_and_unresolved() -> None:
    topology = build_topology([("a", "b", SLOW, 0.5, 1)])  # delivery at 0.6, after the run ends
    result = run_simulation(config([flow("f", "a", "b")], duration=0.3), topology)
    (packet,) = result.packets
    assert packet.status == "in_transit" and packet.delivered_at_s is None
    assert kinds(result, 1) == ["created", "transmission_start", "transmitted"]  # propagating when the run ended
    assert (result.delivered, result.dropped, result.unresolved) == (0, 0, 1)


def test_failing_the_same_link_twice_is_harmless() -> None:
    topology = build_topology([("a", "b", SLOW, 0.0, 1)])
    failures = [FailureEvent(0.05, "link", "a-b"), FailureEvent(0.06, "link", "a-b")]
    flows = [flow(f"f{i}", "a", "b") for i in (1, 2)]
    result = run_simulation(config(flows, failure_events=failures), topology)
    assert [p.status for p in result.packets] == ["delivered", "dropped"]
    assert_consistent(result)


def test_router_failure_with_nothing_queued_changes_only_later_routing() -> None:
    topology = build_topology(
        [("a", "r1", SLOW, 0.0, 1), ("r1", "b", SLOW, 0.0, 1), ("a", "r2", SLOW, 0.0, 2), ("r2", "b", SLOW, 0.0, 2)],
        routers=("r1", "r2"),
    )
    flows = [flow("f", "a", "b", count=2, interval=1.0)]
    result = run_simulation(config(flows, failure_events=[FailureEvent(0.5, "router", "r1")]), topology)
    assert [p.path for p in result.packets] == [["a", "r1", "b"], ["a", "r2", "b"]]
    assert [p.status for p in result.packets] == ["delivered", "delivered"]
    assert_consistent(result)


def _congested_config() -> ExperimentConfig:
    flows = [
        Flow("rt", "stu1", "portal", "realtime", "udp_like", 1500, 0.0, 1.0, 0.002),
        Flow("web", "stu2", "portal", "interactive", "udp_like", 1500, 0.0, 1.0, 0.004),
        Flow("bulk", "stu3", "files", "bulk", "tcp_like", 1500, 0.0, 1.0, 0.002),
    ]
    return ExperimentConfig(
        duration_s=1.0, flows=flows, queue_policy="strict_priority", queue_capacity_packets=10,
        failure_events=[FailureEvent(0.5, "link", "r1-r2")],
    )


def test_congested_run_with_a_failure_has_consistent_counts_and_log() -> None:
    result = run_simulation(_congested_config())
    assert result.generated == 500 + 250 + 500
    assert_consistent(result)
    reasons = {p.drop_reason for p in result.packets if p.status == "dropped"}
    assert "queue_full" in reasons  # 12+ Mbps offered into a 10 Mbps core link
    assert reasons <= {"queue_full", "link_down", "no_route", "router_down"}
    after = [p for p in result.packets if p.created_at_s >= 0.5]
    assert after and all(p.path[1:3] in (["sw1", "r2"], ["sw2", "r2"]) for p in after if p.path)  # backup uplinks
    assert any(r.event_type == "link_failed" and r.timestamp_s == 0.5 for r in result.records)


def test_runs_are_deterministic() -> None:
    first, second = run_simulation(_congested_config()), run_simulation(_congested_config())
    assert first.records == second.records
    assert first.packets == second.packets


def test_simulation_never_sleeps(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(_: float) -> None:
        raise AssertionError("time.sleep must not be used to advance simulated time")

    monkeypatch.setattr(time, "sleep", forbidden)
    topology = build_topology([("a", "b", SLOW, 0.5, 1)])
    result = run_simulation(config([flow("f", "a", "b", count=500, interval=2.0)], duration=1000.0), topology)
    assert result.delivered == 500


# --- configuration handling ---


def test_bandwidth_override_and_queue_capacity_are_applied() -> None:
    topology = build_topology([("a", "b", SLOW, 0.0, 1)])
    flows = [flow(f"f{i}", "a", "b") for i in (1, 2, 3)]
    result = run_simulation(config(flows, capacity=1, bandwidth_overrides={"a-b": 2 * SLOW}), topology)
    # tx is now 0.05: packet 1 delivered at 0.05, packet 2 (queued) at 0.10, packet 3 dropped
    assert [p.delivered_at_s for p in result.packets[:2]] == pytest.approx([0.05, 0.10])
    assert result.packets[2].drop_reason == "queue_full"


def test_caller_topology_is_not_modified() -> None:
    topology = build_default_topology()
    cfg = config([flow("f", "stu1", "portal")], failure_events=[FailureEvent(0.0, "link", "r1-r2")])
    result = run_simulation(cfg, topology)
    assert topology.links["r1-r2"].is_up and not result.topology.links["r1-r2"].is_up


@pytest.mark.parametrize(
    "bad_config",
    [
        config([flow("f", "a", "zzz")]),
        config([flow("f", "a", "a")]),
        config([flow("f", "a", "b")], bandwidth_overrides={"nope": 1e6}),
        config([flow("f", "a", "b")], failure_events=[FailureEvent(1.0, "link", "nope")]),
        config([flow("f", "a", "b")], failure_events=[FailureEvent(1.0, "router", "a")]),
        config([flow("f", "a", "b")], duration=0),
        config([flow("f", "a", "b")], topology="unknown"),
    ],
)
def test_invalid_runs_are_rejected(bad_config: ExperimentConfig) -> None:
    topology = build_topology([("a", "b", SLOW, 0.0, 1)])
    with pytest.raises(ValueError):
        run_simulation(bad_config, topology if bad_config.topology == "default" else None)
