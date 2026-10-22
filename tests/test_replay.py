import pytest

from campusnet.config import ExperimentConfig, FailureEvent
from campusnet.experiments import base_config
from campusnet.models import Flow
from campusnet.replay import DROP_KINDS, NAV_FILTERS, ReplayIndex
from campusnet.simulator import run_simulation
from helpers import build_topology

SLOW = 8000.0  # 100 B packets serialize in exactly 0.1 s


def flow(flow_id: str, src: str = "a", dst: str = "b", start: float = 0.0, count: int = 1, interval: float = 1.0) -> Flow:
    return Flow(flow_id, src, dst, "bulk", "udp_like", 100, start, start + (count - 0.5) * interval, interval)


def run(flows, topology, duration: float = 1.0, capacity: int = 10, **extra):
    config = ExperimentConfig(flows=flows, duration_s=duration, queue_capacity_packets=capacity, **extra)
    result = run_simulation(config, topology)
    return result, ReplayIndex(result)


def find(replay: ReplayIndex, event: str, packet=None, location=None, nth: int = 0) -> int:
    hits = [i for i, r in enumerate(replay.records)
            if r.event_type == event and (packet is None or r.packet_id == packet) and (location is None or r.location == location)]
    return hits[nth]


def two_hop():
    """a-b (tx 0.1 s, prop 0.05 s) then b-c (tx 0.05 s); a burst of three packets a -> c at t=0."""
    topology = build_topology([("a", "b", SLOW, 0.05, 1), ("b", "c", 2 * SLOW, 0.0, 1)])
    return run([flow(f"f{i}", "a", "c") for i in (1, 2, 3)], topology)


# --- snapshots, by hand ---


def test_snapshot_before_any_event_is_empty() -> None:
    _, replay = two_hop()
    snap = replay.snapshot(-1)
    assert (snap.created, snap.delivered, snap.dropped, snap.in_network) == (0, 0, 0, 0)
    assert snap.ports == {} and snap.failed_links == set() and snap.time_s == 0.0


def test_snapshot_after_the_burst_is_queued_behind_the_transmitting_packet() -> None:
    _, replay = two_hop()
    snap = replay.snapshot(find(replay, "queued", packet=3))
    port = snap.ports[("a-b", "a")]
    assert (port.transmitting, port.waiting, port.capacity) == (1, [2, 3], 10)
    assert snap.queue_length(("a-b", "a")) == 2 and snap.queue_length(("b-c", "b")) == 0
    assert (snap.created, snap.in_network) == (3, 3)


def test_packet_leaves_the_queue_exactly_when_its_transmission_starts() -> None:
    _, replay = two_hop()
    done = replay.snapshot(find(replay, "transmitted", packet=1))  # packet 1 finished; packet 2 not yet started
    assert done.ports[("a-b", "a")].propagating == [(1, 0.0)] and done.ports[("a-b", "a")].waiting == [2, 3]
    started = replay.snapshot(find(replay, "transmission_start", packet=2, location="a-b"))
    port = started.ports[("a-b", "a")]
    assert (port.transmitting, port.waiting) == (2, [3]) and port.propagating == [(1, 0.0)]


def test_progress_along_the_link_follows_the_requested_time() -> None:
    _, replay = two_hop()
    index = find(replay, "transmission_start", packet=2, location="a-b")  # t = 0.1; packet 1 propagates until 0.15
    for time_s, expected in [(0.1, 0.0), (0.125, 0.5), (0.1499, 0.998)]:
        (_, fraction), = replay.snapshot(index, time_s).ports[("a-b", "a")].propagating
        assert fraction == pytest.approx(expected, abs=1e-6)
    port = replay.snapshot(index, 0.15).ports[("a-b", "a")]
    assert port.transmitting == 2  # packet 2 is 50% serialized at 0.15 (0.1 to 0.2)
    state = replay.packet_state(2, index, 0.15)
    assert state.kind == "transmitting" and state.fraction == pytest.approx(0.5)


def test_arrival_at_the_next_node_coincides_with_the_next_record() -> None:
    _, replay = two_hop()
    state = replay.packet_state(1, find(replay, "transmitted", packet=1, location="a-b"))
    arrival = replay.records[find(replay, "transmission_start", packet=1, location="b-c")].timestamp_s
    assert state.kind == "propagating" and state.until_s == arrival == pytest.approx(0.15)
    after = replay.snapshot(find(replay, "transmission_start", packet=1, location="b-c"))
    assert after.ports[("b-c", "b")].transmitting == 1
    on_first_link = after.ports.get(("a-b", "a"))
    assert on_first_link is None or 1 not in [p for p, _ in on_first_link.propagating]  # it has left the first link


def test_packet_state_sequence_for_a_queued_packet() -> None:
    _, replay = two_hop()
    kinds = []
    for i in replay.journey(2):
        kinds.append(replay.packet_state(2, i).kind)
    assert kinds == ["at_source", "waiting", "transmitting", "propagating", "transmitting", "propagating", "delivered"]
    assert replay.packet_state(2, -1).kind == "not_created"


def test_final_snapshot_agrees_with_the_result() -> None:
    result, replay = two_hop()
    snap = replay.snapshot(len(replay) - 1)
    assert (snap.created, snap.delivered, snap.dropped, snap.in_network) == (
        result.generated, result.delivered, result.dropped, result.unresolved)
    assert replay.snapshot(10**9).index == len(replay) - 1  # clamped


def test_unfinished_transmission_and_propagation_at_the_end() -> None:
    topology = build_topology([("a", "b", SLOW, 0.5, 1)])
    result, replay = run([flow("f1"), flow("f2")], topology, duration=0.15)  # f1 propagating, f2 transmitting
    snap = replay.snapshot(len(replay) - 1, 0.15)
    port = snap.ports[("a-b", "a")]
    assert port.transmitting == 2 and [p for p, _ in port.propagating] == [1]
    assert port.propagating[0][1] == pytest.approx(0.05 / 0.5)
    assert snap.in_network == result.unresolved == 2


# --- consistency with the engine's own records ---


@pytest.fixture(scope="module")
def congested():
    config = base_config(duration_s=1.0)
    config.queue_policy = "strict_priority"
    config.failure_events = [FailureEvent(0.5, "link", "r1-r2")]
    result = run_simulation(config)
    return result, ReplayIndex(result)


def test_reconstructed_queues_match_every_recorded_queue_length(congested) -> None:
    result, replay = congested
    checked = 0
    for i, record in enumerate(replay.records):
        if record.queue_length is None or record.sender is None:
            continue
        if record.event_type == "dropped" and replay.drop_kind(record) in ("flushed_link", "flushed_router"):
            continue  # the engine logs these after emptying the queue, so its length is not mid-flush
        assert replay.snapshot(i).queue_length((record.location, record.sender)) == record.queue_length, (i, record)
        checked += 1
    assert checked > 5000  # a congested run: thousands of queue observations


def test_reconstructed_counts_and_locations_match_the_engine(congested) -> None:
    result, replay = congested
    for i in range(0, len(replay), 97):
        if replay.records[i].event_type == "created":
            continue  # a new packet is between creation and its first queue for an instant
        snap = replay.snapshot(i)
        located = sum(len(p.waiting) + (p.transmitting is not None) + len(p.propagating) for p in snap.ports.values())
        assert located == snap.in_network, i
        assert snap.created == sum(1 for r in replay.records[: i + 1] if r.event_type == "created")
        assert snap.delivered == sum(1 for r in replay.records[: i + 1] if r.event_type == "delivered")
        assert snap.dropped == sum(1 for r in replay.records[: i + 1] if r.event_type == "dropped")
    final = replay.snapshot(len(replay) - 1)
    assert (final.delivered, final.dropped, final.in_network) == (result.delivered, result.dropped, result.unresolved)


def test_queues_never_exceed_capacity_and_a_port_serves_one_packet_at_a_time(congested) -> None:
    _, replay = congested
    for i in range(0, len(replay), 53):
        for key, port in replay.snapshot(i).ports.items():
            assert len(port.waiting) <= port.capacity
            assert isinstance(port.transmitting, (int, type(None)))


def test_snapshot_is_fast_enough_for_interactive_scrubbing(congested) -> None:
    import time

    _, replay = congested
    start = time.perf_counter()
    for i in range(0, len(replay), len(replay) // 40):
        replay.snapshot(i)
    assert (time.perf_counter() - start) / 40 < 0.1  # well under 100 ms per position (typically a few ms)


# --- failures ---


def test_failed_links_and_routers_appear_only_after_their_failure_record() -> None:
    topology = build_topology([("a", "r", SLOW, 0.0, 1), ("r", "c", SLOW, 0.0, 1)], routers=("r",))
    result, replay = run([flow("f", "a", "c")], topology, failure_events=[FailureEvent(0.3, "router", "r")])
    idx = find(replay, "router_failed")
    before, after = replay.snapshot(idx - 1), replay.snapshot(idx)
    assert before.failed_routers == set() and before.failed_links == set()
    assert after.failed_routers == {"r"} and after.failed_links == {"a-r", "r-c"}  # every attached link goes down


def test_link_failure_is_recorded_in_the_snapshot() -> None:
    topology = build_topology([("a", "b", SLOW, 0.0, 1)])
    _, replay = run([flow("f")], topology, failure_events=[FailureEvent(0.5, "link", "a-b")])
    assert replay.snapshot(find(replay, "link_failed")).failed_links == {"a-b"}
    assert replay.snapshot(find(replay, "link_failed") - 1).failed_links == set()


def redundant_failure():
    """a-b direct (tx 0.1 s) with a longer backup a-c-b; three packets at t=0, a late one at 0.2; a-b fails at 0.05."""
    topology = build_topology([("a", "b", SLOW, 0.05, 1), ("a", "c", SLOW, 0.0, 5), ("c", "b", SLOW, 0.0, 5)])
    flows = [flow(f"f{i}") for i in (1, 2, 3)] + [flow("late", start=0.2)]
    return run(flows, topology, failure_events=[FailureEvent(0.05, "link", "a-b")])


def test_failure_summary_separates_in_transit_flushed_and_later_drops() -> None:
    _, replay = redundant_failure()
    (row,) = replay.failure_summary()
    assert (row["kind"], row["target"], row["time_s"]) == ("link", "a-b", 0.05)
    assert row["in_transit_at_failure"] == 1 and row["in_transit_delivered"] == 1  # packet 1 finishes its hop
    assert row["flushed"] == 2 and row["next_hop_down"] == 0 and row["no_route"] == 0


def test_next_hop_down_is_distinguished_from_a_flushed_packet() -> None:
    topology = build_topology([("a", "b", SLOW, 0.05, 1), ("b", "c", SLOW, 0.0, 1)])
    _, replay = run([flow("f", "a", "c")], topology, failure_events=[FailureEvent(0.12, "link", "b-c")])
    (row,) = replay.failure_summary()
    assert row["in_transit_at_failure"] == 0  # the packet is on a-b, not on the failed link
    assert (row["flushed"], row["next_hop_down"], row["reached_failed_router"], row["no_route"]) == (0, 1, 0, 0)
    drop = find(replay, "dropped")
    assert replay.drop_kind(replay.records[drop]) == "next_hop_down"
    assert "fixed when it is created" in replay.explain(drop) and "0.1200 s" in replay.explain(drop)


def test_reaching_a_failed_router_is_its_own_case() -> None:
    topology = build_topology([("a", "r", SLOW, 0.05, 1), ("r", "c", SLOW, 0.0, 1)], routers=("r",))
    _, replay = run([flow("f", "a", "c")], topology, failure_events=[FailureEvent(0.12, "router", "r")])
    (row,) = replay.failure_summary()
    assert row["in_transit_at_failure"] == 1 and row["in_transit_dropped"] == 1  # propagating on a link attached to r
    assert row["reached_failed_router"] == 1 and row["flushed"] == 0
    assert "router r" in replay.explain(find(replay, "dropped"))


def test_flushed_router_queue_and_no_route_cases() -> None:
    topology = build_topology([("a", "r", 10 * SLOW, 0.0, 1), ("r", "c", SLOW, 0.0, 1)], routers=("r",))
    flows = [flow(f"f{i}", "a", "c") for i in (1, 2, 3)] + [flow("late", "a", "c", start=0.5)]
    _, replay = run(flows, topology, failure_events=[FailureEvent(0.05, "router", "r")])
    kinds = [replay.drop_kind(r) for r in replay.records if r.event_type == "dropped"]
    assert kinds.count("flushed_router") == 2 and kinds.count("no_route") == 1
    (row,) = replay.failure_summary()
    assert row["flushed"] == 2 and row["no_route"] == 1


def test_queue_full_explanation_names_the_queue() -> None:
    topology = build_topology([("a", "b", SLOW, 0.0, 1)])
    _, replay = run([flow(f"f{i}") for i in range(3)], topology, capacity=1)
    text = replay.explain(find(replay, "dropped"))
    assert "queue was full (1 packets)" in text and "a → b" in text and "drop-tail" in text


def test_every_drop_kind_is_known() -> None:
    _, replay = redundant_failure()
    assert {replay.drop_kind(r) for r in replay.records if r.event_type == "dropped"} <= set(DROP_KINDS)


# --- navigation ---


def test_step_through_filtered_events() -> None:
    _, replay = redundant_failure()
    drops = replay.indices("Drops")
    assert len(drops) == 2 and replay.first("Drops") == drops[0]
    assert replay.step(-1, "Drops", +1) == drops[0] and replay.step(drops[0], "Drops", +1) == drops[1]
    assert replay.step(drops[1], "Drops", +1) is None and replay.step(drops[0], "Drops", -1) is None
    assert replay.step(drops[1], "Drops", -1) == drops[0]
    assert replay.first("Failures") == find(replay, "link_failed")


def test_step_through_any_event_stops_at_both_ends() -> None:
    _, replay = two_hop()
    assert replay.step(-1, "Any event", +1) == 0 and replay.step(0, "Any event", -1) is None
    assert replay.step(len(replay) - 1, "Any event", +1) is None
    assert replay.step(5, "Any event", -1) == 4


def test_every_navigation_filter_returns_real_events() -> None:
    _, replay = redundant_failure()
    for name, kinds in NAV_FILTERS.items():
        found = replay.indices(name)
        assert found == sorted(found)
        assert all(kinds is None or replay.records[i].event_type in kinds for i in found)


def test_index_at_time() -> None:
    _, replay = two_hop()
    assert replay.index_at_time(-1.0) == -1
    last_at_zero = max(i for i, r in enumerate(replay.records) if r.timestamp_s == 0.0)
    assert replay.index_at_time(0.0) == last_at_zero
    assert replay.index_at_time(10**6) == len(replay) - 1


# --- routes ---


def test_flow_routes_come_from_the_paths_packets_actually_took() -> None:
    result, replay = redundant_failure()
    routes = replay.flow_routes()
    # Packets carry no flow id, so flows with the same source, destination and class share one group.
    assert set(routes) == {"f1", "f2", "f3", "late"} and routes["f1"] == routes["late"]
    direct, backup = routes["f1"]
    assert direct.path == ("a", "b") and backup.path == ("a", "c", "b")
    assert (direct.packets, direct.delivered, direct.dropped) == (3, 1, 2)  # two were flushed from the failed link
    assert (backup.packets, backup.delivered) == (1, 1)


def test_one_flow_that_changes_route_lists_both_paths_in_time_order() -> None:
    topology = build_topology([("a", "b", SLOW, 0.0, 1), ("a", "c", SLOW, 0.0, 1), ("c", "b", SLOW, 0.0, 1)])
    result, replay = run([flow("f", count=2, interval=0.5)], topology, failure_events=[FailureEvent(0.5, "link", "a-b")])
    uses = replay.flow_routes()["f"]
    assert [u.path for u in uses] == [("a", "b"), ("a", "c", "b")]
    assert uses[0].first_created_s < uses[1].first_created_s
    failure = find(replay, "link_failed")
    assert replay.route_at("f", failure - 1) == ("a", "b") and replay.route_at("f", len(replay) - 1) == ("a", "c", "b")
    assert replay.route_at("f", -1) is None
    with pytest.raises(ValueError):
        replay.route_at("nope", 0)


def test_packets_without_a_route_form_their_own_group() -> None:
    topology = build_topology([("a", "b", SLOW, 0.0, 1), ("c", "d", SLOW, 0.0, 1)])
    _, replay = run([flow("f", "a", "d")], topology)
    (use,) = replay.flow_routes()["f"]
    assert use.path == () and use.dropped == 1


def test_a_run_with_no_events_can_still_be_replayed() -> None:
    topology = build_topology([("a", "b", SLOW, 0.0, 1)])
    result, replay = run([], topology)
    assert len(replay) == 0 and replay.index_at_time(5.0) == -1
    snap = replay.snapshot(0)  # clamped to "before any event"
    assert (snap.index, snap.created, snap.in_network, snap.ports) == (-1, 0, 0, {})
    for name in NAV_FILTERS:
        assert replay.first(name) is None and replay.step(-1, name, +1) is None and replay.step(-1, name, -1) is None
    assert replay.failure_summary() == [] and replay.flow_routes() == {}


def test_a_flow_with_no_packets_has_no_routes() -> None:
    topology = build_topology([("a", "b", SLOW, 0.0, 1)])
    _, replay = run([flow("late", start=5.0)], topology, duration=1.0)  # starts after the run ends
    assert len(replay) == 0 and replay.flow_routes() == {"late": []}
    assert replay.route_at("late", -1) is None


def test_explaining_every_event_kind_mentions_the_packet_and_place() -> None:
    _, replay = redundant_failure()
    seen = set()
    for i, record in enumerate(replay.records):
        text = replay.explain(i)
        assert text and (record.packet_id is None or f"Packet {record.packet_id}" in text)
        if record.location:
            assert record.location in text or record.event_type in ("delivered", "dropped", "created")
        seen.add(record.event_type)
    assert {"created", "queued", "transmission_start", "transmitted", "delivered", "dropped", "link_failed"} <= seen
