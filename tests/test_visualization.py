import itertools
import math

import pytest

from campusnet.experiments import run_preset
from campusnet.metrics import calculate_metrics, throughput_over_time
from campusnet.presentation import queue_length_series
from campusnet.routing import shortest_path
from campusnet.topology import build_default_topology, disable_link, disable_router
from campusnet.visualization import (
    DEFAULT_POSITIONS,
    FOCUS_REGIONS,
    LEFT_MARGIN,
    MAX_QUEUE_POINTS,
    QUEUE_BINS,
    delay_by_class_chart,
    empty_figure,
    outcomes_chart,
    peak_per_bin,
    queue_chart,
    throughput_chart,
    topology_figure,
)


def names(fig) -> list[str]:
    return [trace.name for trace in fig.data if trace.name]


def trace(fig, name: str):
    return next(t for t in fig.data if t.name == name)


# --- topology ---


def test_default_layout_places_every_node_and_keeps_labels_apart() -> None:
    topology = build_default_topology()
    assert set(DEFAULT_POSITIONS) == set(topology.nodes)
    points = list(DEFAULT_POSITIONS.values())
    assert len(set(points)) == len(points)
    for (ax, ay), (bx, by) in itertools.combinations(points, 2):
        assert math.hypot(ax - bx, ay - by) >= 1.1  # no two nodes (or their labels) crowd each other
    mids = [
        ((DEFAULT_POSITIONS[l.source][0] + DEFAULT_POSITIONS[l.target][0]) / 2,
         (DEFAULT_POSITIONS[l.source][1] + DEFAULT_POSITIONS[l.target][1]) / 2)
        for l in topology.links.values()
    ]
    for (ax, ay), (bx, by) in itertools.combinations(mids, 2):
        assert math.hypot(ax - bx, ay - by) >= 0.55, "link bandwidth labels would overlap"
    for mx, my in mids:
        for nx_, ny in points:
            assert math.hypot(mx - nx_, my - ny) >= 0.45, "a link label would sit on a node"


def test_topology_figure_distinguishes_node_types_by_shape_and_legend() -> None:
    fig = topology_figure(build_default_topology())
    assert {"Client", "Switch", "Router", "Server", "Link up"} <= set(names(fig))
    symbols = {n: trace(fig, n).marker.symbol for n in ("Client", "Switch", "Router", "Server")}
    assert len(set(symbols.values())) == 4  # shape, not just color, tells the types apart
    assert len(trace(fig, "Client").x) == 5 and len(trace(fig, "Router").x) == 2
    assert "Link failed" not in names(fig)


def test_every_link_is_labelled_with_its_bandwidth() -> None:
    topology = build_default_topology()
    fig = topology_figure(topology)
    labels = [a.text for a in fig.layout.annotations if "Mbps" in a.text]
    assert len(labels) == len(topology.links)
    assert labels.count("100 Mbps") == 9 and labels.count("10 Mbps") == 3  # 5 access + 2 uplink + 2 server; core + 2 backup


def test_every_node_has_a_label_that_stays_readable_over_route_lines() -> None:
    topology = build_default_topology()
    path = shortest_path(topology, "stu1", "portal")
    fig = topology_figure(topology, routes=[("Active route", path)])
    node_labels = {a.text: a for a in fig.layout.annotations if a.text in topology.nodes}
    assert set(node_labels) == set(topology.nodes)
    assert all(a.bgcolor for a in node_labels.values())  # opaque-ish backing: text never sits directly on a line
    assert node_labels["r1"].yanchor == "top" and node_labels["sw1"].yanchor == "bottom"  # routers below, switches above


def test_failed_link_is_drawn_dashed_in_red_and_labelled() -> None:
    topology = build_default_topology()
    disable_link(topology, "r1-r2")
    fig = topology_figure(topology)
    failed = trace(fig, "Link failed")
    assert failed.line.dash == "dash" and failed.line.color != trace(fig, "Link up").line.color
    assert sum(1 for x in failed.x if x is not None) == 2  # exactly one segment
    assert "10 Mbps (failed)" in [a.text for a in fig.layout.annotations]
    up_segments = sum(1 for x in trace(fig, "Link up").x if x is not None)
    assert up_segments == 2 * 11  # the other eleven links


def test_failed_router_gets_its_own_legend_entry() -> None:
    topology = build_default_topology()
    disable_router(topology, "r1")
    fig = topology_figure(topology)
    assert "Router (failed)" in names(fig) and len(trace(fig, "Router (failed)").x) == 1
    assert len(trace(fig, "Router").x) == 1
    assert "Link failed" in names(fig)  # its attached links are down


def test_route_is_highlighted_and_not_double_drawn() -> None:
    topology = build_default_topology()
    path = shortest_path(topology, "stu1", "portal")
    fig = topology_figure(topology, routes=[("Active route", path)])
    route = trace(fig, "Active route")
    assert route.line.width > trace(fig, "Link up").line.width
    assert sum(1 for x in route.x if x is not None) == 2 * (len(path) - 1)
    assert sum(1 for x in trace(fig, "Link up").x if x is not None) == 2 * (12 - (len(path) - 1))


def test_two_routes_before_and_after_a_failure_are_distinguishable() -> None:
    topology = build_default_topology()
    before = shortest_path(topology, "stu1", "portal")
    disable_link(topology, "r1-r2")
    after = shortest_path(topology, "stu1", "portal")
    fig = topology_figure(topology, routes=[("Route after failure", after), ("Route before failure", before)])
    first, second = trace(fig, "Route after failure"), trace(fig, "Route before failure")
    assert first.line.color != second.line.color and first.line.dash != second.line.dash
    assert first.line.width > second.line.width


def test_node_hover_includes_address() -> None:
    fig = topology_figure(build_default_topology())
    assert any("10.10.3.10" in text for text in trace(fig, "Server").hovertext)


def test_unknown_topologies_get_a_computed_layout() -> None:
    from helpers import make_topology

    fig = topology_figure(make_topology([("a", "b", 1), ("b", "c", 1)]))
    assert len(trace(fig, "Client").x) == 3


# --- replay overlays ---


@pytest.fixture(scope="module")
def busy_run():
    from campusnet.config import FailureEvent
    from campusnet.replay import ReplayIndex
    from campusnet.simulator import run_simulation
    from campusnet.experiments import base_config

    config = base_config(duration_s=1.0)
    config.failure_events = [FailureEvent(0.8, "link", "r1-r2")]
    result = run_simulation(config)
    return result, ReplayIndex(result)


def busy_index(replay, threshold: float) -> int:
    for i in range(0, len(replay), 25):
        for port in replay.snapshot(i).ports.values():
            if port.capacity and len(port.waiting) / port.capacity >= threshold:
                return i
    raise AssertionError("no such queue level in this run")


def test_near_full_queue_is_drawn_on_the_link_with_its_size(busy_run) -> None:
    result, replay = busy_run
    snap = replay.snapshot(busy_index(replay, 0.9))
    fig = topology_figure(result.topology, snapshot=snap, failed_links=snap.failed_links, failed_routers=snap.failed_routers)
    assert "Queue at least 90% full" in names(fig)
    badge = [a.text for a in fig.layout.annotations if "/" in a.text and a.text.split("/")[0].isdigit()]
    port = snap.ports[("r1-r2", "r1")]
    assert f"{len(port.waiting)}/{port.capacity}" in badge
    assert trace(fig, "Queue at least 90% full").line.color != trace(fig, "Link up").line.color


def test_packets_on_links_are_drawn_from_the_snapshot(busy_run) -> None:
    result, replay = busy_run
    snap = replay.snapshot(busy_index(replay, 0.5))
    expected = sum((p.transmitting is not None) + len(p.propagating) for p in snap.ports.values())
    fig = topology_figure(result.topology, snapshot=snap)
    assert len(trace(fig, "Packet on link").x) == expected and expected > 0
    assert all("Packet" in t for t in trace(fig, "Packet on link").hovertext)


def test_no_snapshot_means_no_queue_or_packet_overlay(busy_run) -> None:
    result, _ = busy_run
    fig = topology_figure(result.topology)
    assert "Packet on link" not in names(fig) and "Queue at least 90% full" not in names(fig)


def test_failure_state_can_be_overridden_to_show_a_moment_of_the_run() -> None:
    topology = build_default_topology()  # healthy topology object
    fig = topology_figure(topology, failed_links={"r1-r2"}, failed_routers=set())
    assert "Link failed" in names(fig) and "10 Mbps (failed)" in [a.text for a in fig.layout.annotations]
    fig = topology_figure(topology, failed_links={"r1-r2", "r1-sw1"}, failed_routers={"r1"})
    assert "Router (failed)" in names(fig)


def test_failure_appears_in_the_picture_only_after_the_failure_event(busy_run) -> None:
    result, replay = busy_run
    failure = replay.first("Failures")
    before, after = replay.snapshot(failure - 1), replay.snapshot(failure)
    fig_before = topology_figure(result.topology, snapshot=before, failed_links=before.failed_links)
    fig_after = topology_figure(result.topology, snapshot=after, failed_links=after.failed_links)
    assert "Link failed" not in names(fig_before) and "Link failed" in names(fig_after)


def test_focus_regions_crop_the_view_without_moving_nodes() -> None:
    topology = build_default_topology()
    whole = topology_figure(topology)
    assert tuple(whole.layout.xaxis.range) == (min(p[0] for p in DEFAULT_POSITIONS.values()) - LEFT_MARGIN, 9.0)
    ranges = {name: tuple(topology_figure(topology, focus=name).layout.xaxis.range) for name in FOCUS_REGIONS}
    assert ranges["Whole network"] == tuple(whole.layout.xaxis.range)
    for name, (lo, hi) in ranges.items():
        if name != "Whole network":
            assert lo >= tuple(whole.layout.xaxis.range)[0] and hi <= tuple(whole.layout.xaxis.range)[1] and hi - lo < 9.5
    core_lo, core_hi = ranges["Core routers"]
    assert core_lo < DEFAULT_POSITIONS["r1"][0] < DEFAULT_POSITIONS["r2"][0] < core_hi
    assert trace(topology_figure(topology, focus="Core routers"), "Router").x == trace(whole, "Router").x


def test_primary_route_shows_its_direction() -> None:
    topology = build_default_topology()
    path = shortest_path(topology, "stu1", "portal")
    fig = topology_figure(topology, routes=[("Active route", path)])
    arrows = [a for a in fig.layout.annotations if a.showarrow]
    assert len(arrows) == len(path) - 1
    first = arrows[0]  # stu1 -> sw1: the arrow points toward sw1, i.e. its head is closer to sw1 than its tail
    sw1 = DEFAULT_POSITIONS["sw1"]
    head, tail = (first.x, first.y), (first.ax, first.ay)
    assert math.dist(head, sw1) < math.dist(tail, sw1)
    assert not [a for a in topology_figure(topology).layout.annotations if a.showarrow]


def test_nodes_and_links_are_selectable_by_id() -> None:
    topology = build_default_topology()
    fig = topology_figure(topology)
    assert set(trace(fig, "Client").customdata) == {"stu1", "stu2", "stu3", "stu4", "classroom"}
    assert sorted(trace(fig, "links").customdata) == sorted(topology.links)
    assert trace(fig, "Router").unselected.marker.opacity == 1  # selecting one does not dim the others


def test_highlight_rings_and_followed_packet_markers(busy_run) -> None:
    from campusnet.replay import PacketState

    result, replay = busy_run
    topology = build_default_topology()
    fig = topology_figure(topology, highlights=[("link", "r1-r2", "Selected event"), ("node", "r1", "Inspected item")])
    assert {"Selected event", "Inspected item"} <= set(names(fig))
    assert trace(fig, "Selected event").x[0] == pytest.approx((DEFAULT_POSITIONS["r1"][0] + DEFAULT_POSITIONS["r2"][0]) / 2)
    assert tuple(trace(fig, "Inspected item").x) == DEFAULT_POSITIONS["r1"][:1]
    waiting = PacketState("waiting", "r1-r2", "r1", "r1", 0.0)
    dropped = PacketState("dropped", "r1-r2", "r1", "r1", 0.0)
    fig = topology_figure(topology, followed=(7, waiting))
    assert trace(fig, "Followed packet 7").marker.symbol == "star"
    fig = topology_figure(topology, followed=(7, dropped))
    assert trace(fig, "Dropped packet 7").marker.symbol == "x" and "Followed packet 7" not in names(fig)
    assert not [n for n in names(topology_figure(topology, followed=(7, PacketState("not_created")))) if "Followed" in n]


def test_followed_packet_position_by_hand() -> None:
    from campusnet.replay import PacketState
    from campusnet.visualization import PROPAGATION_END, PROPAGATION_START, QUEUE_BADGE_AT, SERIALIZING_AT, link_position

    topology = build_default_topology()
    pos = DEFAULT_POSITIONS
    r1, r2 = pos["r1"], pos["r2"]
    along = lambda f: (r1[0] + (r2[0] - r1[0]) * f, r1[1] + (r2[1] - r1[1]) * f)  # noqa: E731
    state = lambda kind, frac=None: PacketState(kind, "r1-r2", "r1", "r1", 0.0, None, frac)  # noqa: E731
    assert link_position(state("waiting"), pos, topology.links) == pytest.approx(along(QUEUE_BADGE_AT))
    assert link_position(state("transmitting", 0.3), pos, topology.links) == pytest.approx(along(SERIALIZING_AT))
    mid = PROPAGATION_START + (PROPAGATION_END - PROPAGATION_START) * 0.5
    assert link_position(state("propagating", 0.5), pos, topology.links) == pytest.approx(along(mid))
    reverse = PacketState("propagating", "r1-r2", "r2", None, 0.0, None, 0.0)  # r2 -> r1 starts near r2
    assert link_position(reverse, pos, topology.links) == pytest.approx((r2[0] + (r1[0] - r2[0]) * PROPAGATION_START, r2[1]))
    assert link_position(PacketState("not_created"), pos, topology.links) is None


# --- result charts ---


@pytest.fixture(scope="module")
def preset_b():
    return run_preset("B", duration_s=1.0)


def test_empty_figure_shows_message() -> None:
    fig = empty_figure("Nothing here")
    assert fig.layout.annotations[0].text == "Nothing here" and not fig.data


def test_throughput_chart_has_axis_titles_with_units_and_one_line_per_run(preset_b) -> None:
    series = {run.label: throughput_over_time(run.result, 0.1) for run in preset_b.runs}
    fig = throughput_chart(series, 0.1, 1.0)
    assert len(fig.data) == 2 and "Mbps" in fig.layout.yaxis.title.text and "(s)" in fig.layout.xaxis.title.text
    for run, line in zip(preset_b.runs, fig.data):
        assert max(line.y) * 1e6 == pytest.approx(max(bps for _, bps in series[run.label]))
    assert tuple(fig.layout.xaxis.range) == (0, 1.0)  # both runs share one time axis
    assert throughput_chart({}, 0.1, 1.0).layout.annotations


def test_queue_chart_plots_the_same_series_the_metrics_summarise(preset_b) -> None:
    series = {run.label: queue_length_series(run.result, "r1-r2", "r1") for run in preset_b.runs}
    fig = queue_chart(series, 1.0, "r1 to r2")
    assert [t.name for t in fig.data] == ["fifo", "strict_priority"]
    assert "packets" in fig.layout.yaxis.title.text.lower() and "(s)" in fig.layout.xaxis.title.text
    for run, line in zip(preset_b.runs, fig.data):
        assert max(line.y) == run.metrics.links[("r1-r2", "r1")].max_queue_length
    assert queue_chart({}, 1.0, "x").layout.annotations


def test_peak_per_bin_by_hand() -> None:
    series = [(0.0, 0), (0.1, 5), (0.15, 1), (0.5, 3), (1.0, 3)]
    assert peak_per_bin(series, 1.0, bins=2) == [(0.0, 5), (0.5, 3), (1.0, 3)]
    # the value in force at a bin's start counts even if nothing changes inside the bin
    assert peak_per_bin([(0.0, 0), (0.05, 4), (1.0, 4)], 1.0, bins=4) == [(0.0, 4), (0.25, 4), (0.5, 4), (0.75, 4), (1.0, 4)]


def test_busy_queue_series_is_reduced_without_losing_the_peak() -> None:
    result = run_preset("A", duration_s=1.0).runs[1].result  # high load: the queue oscillates constantly
    series = queue_length_series(result, "r1-r2", "r1")
    assert len(series) > MAX_QUEUE_POINTS
    reduced = peak_per_bin(series, result.duration_s)
    assert len(reduced) == QUEUE_BINS + 1
    assert max(n for _, n in reduced) == max(n for _, n in series)
    fig = queue_chart({"high load": series}, result.duration_s, "r1 to r2")
    assert len(fig.data[0].x) == QUEUE_BINS + 1 and "peak per" in fig.layout.yaxis.title.text
    small = queue_chart({"x": [(0.0, 0), (0.5, 2), (1.0, 2)]}, 1.0, "r1 to r2")
    assert len(small.data[0].x) == 3 and "peak" not in small.layout.yaxis.title.text  # short series stay exact


def test_delay_chart_groups_by_class_in_milliseconds(preset_b) -> None:
    fig = delay_by_class_chart({run.label: run.metrics for run in preset_b.runs})
    assert [b.name for b in fig.data] == ["fifo", "strict_priority"]
    assert list(fig.data[0].x) == ["Realtime", "Interactive", "Bulk"]
    assert "ms" in fig.layout.yaxis.title.text
    fifo = preset_b.runs[0].metrics.by_class["realtime"].avg_delay_s * 1000
    assert fig.data[0].y[0] == pytest.approx(fifo)


def test_delay_chart_handles_no_deliveries() -> None:
    from campusnet.config import ExperimentConfig
    from campusnet.simulator import run_simulation
    from helpers import build_topology

    metrics = calculate_metrics(run_simulation(ExperimentConfig(flows=[], duration_s=1.0), build_topology([("a", "b", 8000, 0, 1)])))
    assert delay_by_class_chart({"empty": metrics}).layout.annotations
    assert outcomes_chart({"empty": metrics}).layout.annotations


def test_outcomes_chart_shares_sum_to_one_hundred_percent_per_run(preset_b) -> None:
    fig = outcomes_chart({run.label: run.metrics for run in preset_b.runs})
    assert fig.layout.barmode == "stack" and tuple(fig.layout.xaxis.range) == (0, 100)
    for i, run in enumerate(preset_b.runs):
        assert sum(bar.x[i] for bar in fig.data) == pytest.approx(100.0)
    assert {bar.name for bar in fig.data} >= {"Delivered", "Unresolved at end", "Dropped: queue_full"}


def test_outcomes_chart_splits_drops_by_reason_for_preset_d() -> None:
    d = run_preset("D", duration_s=1.0)
    fig = outcomes_chart({run.label: run.metrics for run in d.runs})
    assert "Dropped: no_route" in [bar.name for bar in fig.data]
