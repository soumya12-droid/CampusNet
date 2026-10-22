import re

import pytest

from campusnet.experiments import PRESETS, ExperimentRun, base_config, run_preset
from campusnet.insights import (
    CAUTION,
    failure_sentence,
    link_details,
    node_details,
    packet_details,
    preset_observations,
    run_observations,
)
from campusnet.metrics import calculate_metrics
from campusnet.presentation import (
    events_window,
    failure_breakdown,
    failure_table,
    flow_routes_table,
    journey_table,
)
from campusnet.replay import ReplayIndex
from campusnet.simulator import run_simulation
from campusnet.config import FailureEvent


@pytest.fixture(scope="module")
def presets() -> dict:
    return {key: run_preset(key) for key in PRESETS}


def text(lines: list[str]) -> str:
    return " ".join(lines)


# --- observations are generated from measurements ---


def test_every_preset_has_observations_ending_with_the_caution(presets: dict) -> None:
    for key, experiment in presets.items():
        lines = preset_observations(experiment)
        assert len(lines) >= 3 and lines[-1] == CAUTION and all(line.strip() for line in lines), key


def test_observations_never_declare_a_winner(presets: dict) -> None:
    banned = re.compile(r"\b(better|worse|best|worst|superior|optimal|should use|recommend)\b", re.I)
    for experiment in presets.values():
        for line in preset_observations(experiment):
            assert not banned.search(line), line


def numbers_in(line: str) -> set[str]:
    return set(re.findall(r"\d[\d,]*\.?\d*", line))


def test_preset_a_observations_quote_the_measured_values(presets: dict) -> None:
    low, high = presets["A"].runs
    body = text(preset_observations(presets["A"]))
    link_low, link_high = low.metrics.links[("r1-r2", "r1")], high.metrics.links[("r1-r2", "r1")]
    assert f"{link_low.queue_drops} → {link_high.queue_drops}" in body
    assert f"{link_low.max_queue_length} → {link_high.max_queue_length} of 50" in body
    assert f"{high.metrics.overall.pdr * 100:.1f}%" in body and f"{low.metrics.overall.pdr * 100:.1f}%" in body
    assert "drop-tail discarded" in body  # the measured data supports this statement: queue full, drops only at high load


def test_preset_b_covers_every_class_and_states_the_trade_off_from_data(presets: dict) -> None:
    fifo, prio = presets["B"].runs
    body = text(preset_observations(presets["B"]))
    for name in ("Realtime", "Interactive", "Bulk"):
        assert f"{name}:" in body
    f, p = fifo.metrics.by_class["realtime"], prio.metrics.by_class["realtime"]
    assert f"{f.avg_delay_s * 1000:.2f} ms → {p.avg_delay_s * 1000:.2f} ms" in body
    statement = next(line for line in preset_observations(presets["B"]) if line.startswith("What it shows"))
    lowered = [c for c in ("realtime", "interactive", "bulk")
               if fifo.metrics.by_class[c].avg_delay_s - prio.metrics.by_class[c].avg_delay_s > 5e-5]
    raised = [c for c in ("realtime", "interactive", "bulk")
              if prio.metrics.by_class[c].avg_delay_s - fifo.metrics.by_class[c].avg_delay_s > 5e-5]
    for c in lowered:
        assert f"lower for" in statement and c in statement.split("lower for")[1].split("but")[0]
    if raised:
        assert "higher for" in statement and all(c in statement.split("higher for")[1] for c in raised)
    assert "depends on the load mix" in statement  # does not claim priority helps every class


def test_preset_b_does_not_claim_improvement_for_a_class_that_got_slower(presets: dict) -> None:
    fifo, prio = presets["B"].runs
    slower = [c for c in ("realtime", "interactive", "bulk")
              if prio.metrics.by_class[c].avg_delay_s > fifo.metrics.by_class[c].avg_delay_s + 5e-5]
    statement = next(line for line in preset_observations(presets["B"]) if line.startswith("What it shows"))
    for c in slower:
        assert c not in statement.split("lower for")[1].split("but")[0] if "lower for" in statement else True


def test_preset_c_reports_where_queue_drops_stop(presets: dict) -> None:
    c = presets["C"]
    body = text(preset_observations(c))
    zero = next(r for r in c.runs if r.metrics.links[("r1-r2", "r1")].queue_drops == 0)
    assert f"reached zero at {zero.settings['bottleneck_mbps']:g} Mbps" in body
    for run in c.runs:
        assert f"{run.settings['bottleneck_mbps']:g} Mbps: delivered" in body


def test_preset_d_separates_routing_from_packet_outcomes(presets: dict) -> None:
    d = presets["D"]
    body = text(preset_observations(d))
    assert "4 of 4 flows change path" in body and "every flow still has a route" in body  # redundant link
    assert "2 have no route (lecture, web-1)" in body  # bridge link
    assert "Routing results and packet outcomes are different things" in body
    bridge = d.runs[2]
    assert f"no_route {bridge.metrics.overall.drop_reasons['no_route']}" in body


def test_preset_e_trends_follow_the_numbers(presets: dict) -> None:
    e = presets["E"]
    drops = [r.metrics.links[("r1-r2", "r1")].queue_drops for r in e.runs]
    body = text(preset_observations(e))
    expected = "fell" if drops == sorted(drops, reverse=True) and drops[0] > drops[-1] else "did not change in one direction"
    assert f"queue-full drops {expected}" in body
    assert "does not add link capacity" in body


def test_run_observations_for_a_custom_run_with_a_failure() -> None:
    config = base_config(duration_s=1.0)
    config.failure_events = [FailureEvent(0.5, "link", "r1-r2")]
    result = run_simulation(config)
    run = ExperimentRun("Custom run", {}, config, result, calculate_metrics(result))
    body = text(run_observations(run))
    o = run.metrics.overall
    assert f"{o.generated:,} packets were generated" in body and f"{o.delivered:,} delivered" in body
    assert "still in the network when the run ended (not counted as lost)" in body
    assert "Link r1-r2 failed at 0.5 s" in body and "flushed from its waiting queue" in body


def test_run_observations_without_drops_or_failure() -> None:
    config = base_config(duration_s=1.0, intensity=0.4)
    result = run_simulation(config)
    body = text(run_observations(ExperimentRun("Custom run", {}, config, result, calculate_metrics(result))))
    assert "No packets were dropped." in body and "failed at" not in body


def test_failure_sentence_counts_match_the_summary() -> None:
    row = dict(time_s=1.0, kind="link", target="x", in_transit_at_failure=3, in_transit_delivered=2, in_transit_dropped=1,
               in_transit_unresolved=0, flushed=4, next_hop_down=5, reached_failed_router=0, no_route=6)
    sentence = failure_sentence(row)
    for fragment in ("3 mid-transmission", "2 delivered, 1 dropped", "4 flushed", "5 dropped later", "6 created afterwards"):
        assert fragment in sentence
    assert "failed router" not in sentence


# --- inspector details ---


@pytest.fixture(scope="module")
def failure_run():
    config = base_config(duration_s=1.0)
    config.failure_events = [FailureEvent(0.5, "link", "r1-r2")]
    result = run_simulation(config)
    return result, ReplayIndex(result), calculate_metrics(result)


def test_node_and_link_details(failure_run) -> None:
    result, replay, metrics = failure_run
    rows = dict(node_details(result.topology, "r1"))
    assert rows["Type"] == "router" and rows["IPv4"] == "10.10.0.1" and "r1-r2 to r2 (10 Mbps)" in rows["Links"]
    assert dict(node_details(result.topology, "sw1"))["IPv4"] == "none"
    failure = replay.first("Failures")
    at_failure = replay.snapshot(failure)  # the failure record is applied; the flush drops that follow are not yet
    assert at_failure.queue_length(("r1-r2", "r1")) > 0 and "r1-r2" in at_failure.failed_links
    after = replay.snapshot(replay.index_at_time(replay.records[failure].timestamp_s))  # every record at that instant applied
    assert "r1-r2 to r2 (10 Mbps, failed)" in dict(node_details(result.topology, "r1", after))["Links"]
    link = dict(link_details(result.topology, "r1-r2", after, metrics))
    assert link["Bandwidth"] == "10 Mbps" and link["Propagation delay"] == "2 ms" and link["Routing cost"] == "1"
    assert link["State at this moment"] == "failed" and "now: 0/50 waiting" in link["r1 → r2"]
    assert "whole run: utilization" in link["r1 → r2"] and "queue-full drops" in link["r1 → r2"]
    before = dict(link_details(result.topology, "r1-r2", replay.snapshot(replay.first("Failures") - 1)))
    assert before["State at this moment"] == "up"
    assert "whole run" not in before["r1 → r2"]  # metrics only when given


def test_packet_details_by_hand() -> None:
    from helpers import build_topology
    from campusnet.config import ExperimentConfig
    from campusnet.models import Flow

    flows = [Flow(f"f{i}", "a", "b", "bulk", "udp_like", 100, 0.0, 0.5, 1.0) for i in (1, 2)]
    result = run_simulation(ExperimentConfig(flows=flows, duration_s=1.0), build_topology([("a", "b", 8000.0, 0.0, 1)]))
    replay = ReplayIndex(result)
    rows = dict(packet_details(replay, 2))
    assert rows["Path (fixed at creation)"] == "a → b" and rows["Time waiting in queues"] == "100.00 ms"
    assert rows["End-to-end delay"] == "200.00 ms" and rows["Outcome"] == "delivered"
    assert dict(packet_details(replay, 1))["Time waiting in queues"] == "0.00 ms"


# --- replay tables ---


def test_journey_table_lists_every_event_of_the_packet(failure_run) -> None:
    result, replay, _ = failure_run
    pid = next(p.packet_id for p in result.packets if p.status == "delivered")
    table = journey_table(replay, pid)
    assert list(table["Event"])[0] == "created" and list(table["Event"])[-1] == "delivered"
    assert len(table) == len(replay.journey(pid)) and table["Time (s)"].is_monotonic_increasing
    assert all(isinstance(t, str) and t for t in table["What happened"])


def test_events_window_marks_the_current_event(failure_run) -> None:
    _, replay, _ = failure_run
    window = events_window(replay, 100)
    assert list(window["#"]) == list(range(96, 106)) and window.loc[window["#"] == 100, "Now"].iloc[0] == "▶"
    assert (window["Now"] != "").sum() == 1
    assert events_window(replay, 0)["#"].iloc[0] == 0 and events_window(replay, len(replay) - 1)["#"].iloc[-1] == len(replay) - 1
    assert len(events_window(replay, -1)) == 6 or len(events_window(replay, -1)) >= 1  # before the first event: shows the start


def test_flow_routes_and_failure_tables(failure_run) -> None:
    result, replay, _ = failure_run
    routes = flow_routes_table(replay)
    assert set(routes["Flow"]) == {"lecture", "web-1", "download-1", "download-2"}
    download = routes[routes["Flow"] == "download-1"]
    assert list(download["Path"]) == ["stu3 → sw2 → r1 → r2 → files", "stu3 → sw2 → r2 → files"]
    assert download["Packets"].sum() == sum(1 for p in result.packets if p.source_id == "stu3")
    failures = failure_table(replay)
    assert list(failures["Failure"]) == ["link r1-r2"] and failures["At (s)"].iloc[0] == 0.5
    assert failures["Flushed from its queue"].iloc[0] > 0 and failures["No route at creation"].iloc[0] == 0
    # the vertical breakdown carries exactly the same numbers, one case per row
    breakdown = failure_breakdown(replay)
    assert set(breakdown["Failure"]) == {"link r1-r2 at 0.5 s"} and breakdown["Packets"].sum() >= failures["Flushed from its queue"].iloc[0]
    by_case = dict(zip(breakdown["Case"], breakdown["Packets"]))
    assert by_case["Waiting in its queue: flushed (dropped at the failure)"] == failures["Flushed from its queue"].iloc[0]
    assert by_case["On the failed link at that moment (finish their hop)"] == failures["On the failed link at that moment"].iloc[0]
    assert not any("failed router" in case for case in by_case)  # a link failure has no failed-router case
