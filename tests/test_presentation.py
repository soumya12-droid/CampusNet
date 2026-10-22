import io

import pandas as pd
import pytest

from campusnet.config import ExperimentConfig, FailureEvent
from campusnet.experiments import PRESETS, base_config, run_preset
from campusnet.metrics import calculate_metrics
from campusnet.models import Flow
from campusnet.presentation import (
    NA,
    PRESET_INFO,
    class_delivery_view,
    class_queueing_view,
    class_table,
    config_summary,
    config_table,
    describe_failures,
    event_log_frame,
    experiment_export,
    filter_event_log,
    fmt_mbps,
    fmt_ms,
    fmt_pct,
    link_metrics_table,
    offered_load_bps,
    outcomes_table,
    performance_table,
    queue_length_series,
    route_summary,
    routes_table,
    run_class_table,
    to_csv_bytes,
    topology_tables,
    workload_table,
)
from campusnet.simulator import run_simulation
from campusnet.topology import build_default_topology, disable_link
from helpers import build_topology

SLOW = 8000.0


@pytest.fixture(scope="module")
def presets() -> dict:
    return {key: run_preset(key, duration_s=1.0) for key in PRESETS}


def burst3():
    flows = [
        Flow(f"f{i}", "a", "b", cls, "udp_like", 100, 0.0, 0.5, 1.0)  # type: ignore[arg-type]
        for i, cls in enumerate(["bulk", "bulk", "realtime"], start=1)
    ]
    config = ExperimentConfig(flows=flows, duration_s=1.0, queue_capacity_packets=10)
    return run_simulation(config, build_topology([("a", "b", SLOW, 0.0, 1)]))


# --- preset descriptions ---


def test_every_preset_has_a_description_matching_the_experiment(presets: dict) -> None:
    assert sorted(PRESET_INFO) == sorted(PRESETS) == ["A", "B", "C", "D", "E"]
    for key, info in PRESET_INFO.items():
        assert info.title == presets[key].title
        assert info.varies == presets[key].varied
        assert info.summary and info.fixed and info.how_to_read


def test_descriptions_state_what_stays_fixed() -> None:
    for key in ("A", "B", "C", "D", "E"):
        fixed = " ".join(PRESET_INFO[key].fixed).lower()
        assert "topology" in fixed
    assert "FIFO" in " ".join(PRESET_INFO["A"].fixed) and "FIFO" not in " ".join(PRESET_INFO["B"].fixed)
    assert "queue capacity" not in " ".join(PRESET_INFO["E"].fixed).lower()  # E varies it


# --- formatting ---


def test_formatters_show_na_for_missing_values() -> None:
    assert fmt_pct(None) == fmt_ms(None) == fmt_mbps(None) == NA
    assert fmt_pct(0.5) == "50.0%" and fmt_ms(0.0125) == "12.50 ms" and fmt_mbps(2.5e6) == "2.50 Mbps"
    assert fmt_pct(0.0) == "0.0%"  # zero is data, not "no data"


# --- configuration and topology tables ---


def test_offered_load_and_workload_table_match_the_flows() -> None:
    config = base_config()
    table = workload_table(config)
    assert len(table) == 4 and list(table["Class"]) == ["Realtime", "Interactive", "Bulk", "Bulk"]
    assert table["Rate (Mbps)"].sum() == pytest.approx(offered_load_bps(config) / 1e6)
    assert 11.8 < offered_load_bps(config) / 1e6 < 12.0


def test_describe_failures() -> None:
    assert describe_failures(ExperimentConfig()) == "none"
    config = ExperimentConfig(failure_events=[FailureEvent(1.0, "link", "r1-r2"), FailureEvent(1.5, "router", "r2")])
    assert describe_failures(config) == "link r1-r2 at 1 s; router r2 at 1.5 s"


def test_config_table_shows_each_runs_configuration(presets: dict) -> None:
    for key, experiment in presets.items():
        table = config_table(experiment.runs)
        assert list(table["Run"]) == [run.label for run in experiment.runs]
        assert (table["Seed"] == 42).all() and (table["Duration (s)"] == 1.0).all()
    c = config_table(presets["C"].runs)
    assert list(c["Bottleneck r1-r2 (Mbps)"]) == [5, 10, 20, 40]
    e = config_table(presets["E"].runs)
    assert list(e["Queue capacity (packets)"]) == [5, 20, 50, 200]
    b = config_table(presets["B"].runs)
    assert list(b["Queue policy"]) == ["FIFO", "Strict priority"]
    d = config_table(presets["D"].runs)
    assert d["Failure"].iloc[0] == "none" and "r1-r2" in d["Failure"].iloc[1] and "r2-portal" in d["Failure"].iloc[2]


def test_config_summary_names_every_setting(presets: dict) -> None:
    run = presets["D"].runs[1]
    text = config_summary(run)
    for part in ("FIFO", "queue 50 packets", "bottleneck 10 Mbps", "offered load", "link r1-r2 at 0.5 s", "1 s", "seed 42"):
        assert part in text
    assert "strict priority" in config_summary(presets["B"].runs[1]).lower()


def test_topology_tables_and_failed_status() -> None:
    topology = build_default_topology()
    nodes, links = topology_tables(topology)
    assert len(nodes) == 11 and len(links) == 12
    assert set(nodes["Type"]) == {"student device", "switch", "router", "server"}
    assert (links["Status"] == "up").all()
    disable_link(topology, "r1-r2")
    _, links = topology_tables(topology)
    assert list(links.loc[links["Status"] == "failed", "Link"]) == ["r1-r2"]


# --- result tables agree with the metrics ---


def test_outcomes_and_performance_tables_agree_with_metrics(presets: dict) -> None:
    for experiment in presets.values():
        outcomes, performance = outcomes_table(experiment), performance_table(experiment)
        assert len(outcomes) == len(performance) == len(experiment.runs)
        for i, run in enumerate(experiment.runs):
            overall = run.metrics.overall
            assert outcomes.loc[i, "Generated"] == overall.generated
            assert outcomes.loc[i, "Generated"] == outcomes.loc[i, ["Delivered", "Dropped", "Unresolved"]].sum()
            assert outcomes.loc[i, "PDR (%)"] == pytest.approx(overall.pdr * 100)
            assert outcomes.loc[i, "PLR (%)"] == pytest.approx(overall.plr * 100)
            assert performance.loc[i, "Avg delay (ms)"] == pytest.approx(overall.avg_delay_s * 1000)
            assert performance.loc[i, "Throughput (Mbps)"] == pytest.approx(overall.throughput_bps / 1e6)
            link = run.metrics.links[("r1-r2", "r1")]
            assert performance.loc[i, "Bottleneck util. (%)"] == pytest.approx(link.utilization * 100)
            assert performance.loc[i, "Bottleneck drops (full)"] == link.queue_drops


def test_outcomes_table_has_one_drop_column_per_reason(presets: dict) -> None:
    table = outcomes_table(presets["D"])
    assert {"Dropped: link_down", "Dropped: no_route"} <= set(table.columns)
    assert table["Dropped: no_route"].iloc[0] == 0  # baseline
    assert table["Dropped: no_route"].iloc[2] > 0  # bridge failure


def test_class_tables(presets: dict) -> None:
    table = class_table(presets["B"])
    assert len(table) == 6 and set(table["Class"]) == {"Realtime", "Interactive", "Bulk"}
    run = presets["B"].runs[0]
    single = run_class_table(run.metrics)
    assert list(single["Generated"]) == [run.metrics.by_class[c].generated for c in ("realtime", "interactive", "bulk")]
    assert {"Still waiting at end (packets)", "Longest wait (ms)", "Mean queueing delay per hop (ms)"} <= set(single.columns)


def test_class_views_split_the_table_without_losing_columns(presets: dict) -> None:
    full = class_table(presets["B"])
    delivery, queueing = class_delivery_view(full), class_queueing_view(full)
    assert len(delivery) == len(queueing) == len(full)
    assert set(delivery.columns) | set(queueing.columns) == set(full.columns)
    assert {"Run", "Class"} <= set(delivery.columns) & set(queueing.columns)
    assert max(len(delivery.columns), len(queueing.columns)) <= 10  # narrow enough to read without scrolling
    single = run_class_table(presets["B"].runs[0].metrics)
    assert "Run" in class_delivery_view(single).columns


def test_empty_classes_keep_missing_values_missing() -> None:
    config = ExperimentConfig(flows=[], duration_s=1.0)
    metrics = calculate_metrics(run_simulation(config, build_topology([("a", "b", SLOW, 0.0, 1)])))
    table = run_class_table(metrics)
    assert table["PDR (%)"].isna().all() and table["Avg delay (ms)"].isna().all()
    assert (table["Generated"] == 0).all()


def test_link_metrics_table_hides_idle_directions_by_default() -> None:
    result = burst3()
    metrics = calculate_metrics(result)
    active = link_metrics_table(metrics, result.topology)
    assert list(active["Direction"]) == ["a -> b"]
    assert active["Utilization (%)"].iloc[0] == pytest.approx(30.0)
    assert len(link_metrics_table(metrics, result.topology, only_active=False)) == 2


def test_route_summary_by_hand() -> None:
    topology = build_default_topology()
    # stu1-sw1 (cost 1, 0.1 ms), sw1-r1 (1, 0.5 ms), r1-r2 (1, 2 ms, 10 Mbps), r2-portal (1, 0.1 ms)
    summary = route_summary(topology, ["stu1", "sw1", "r1", "r2", "portal"])
    assert summary == {"hops": 4, "cost": 4, "slowest_link_mbps": 10.0, "propagation_ms": pytest.approx(2.7)}
    assert route_summary(topology, ["stu1"])["hops"] == 0


def test_routes_table_and_experiment_export(presets: dict) -> None:
    d = presets["D"]
    table = routes_table(d)
    assert len(table) == len(d.routes) == 8
    unreachable = table[(table["Flow"] == "lecture") & table["Scenario"].str.contains("bridge")]
    assert unreachable["Path after"].iloc[0] == "unreachable" and not unreachable["Reachable after"].iloc[0]
    assert "→" in table["Path before"].iloc[0]
    export = experiment_export(d)
    assert len(export) == len(d.runs) and {"Generated", "PDR (%)", "Avg delay (ms)", "Dropped: no_route"} <= set(export.columns)
    assert export.columns.is_unique


# --- event log ---


def test_event_log_frame_matches_the_records() -> None:
    result = burst3()
    frame = event_log_frame(result)
    assert len(frame) == len(result.records)
    assert frame["Time (s)"].is_monotonic_increasing
    assert set(frame.loc[frame["Packet"] == 3, "Class"]) == {"realtime"}
    assert list(frame.columns)[:3] == ["Time (s)", "Event", "Packet"]


def test_event_log_keeps_whole_number_columns_as_integers() -> None:
    config = ExperimentConfig(
        flows=[Flow("f", "a", "b", "bulk", "udp_like", 100, 0.0, 0.5, 1.0)], duration_s=1.0,
        failure_events=[FailureEvent(0.5, "link", "a-b")],
    )
    frame = event_log_frame(run_simulation(config, build_topology([("a", "b", SLOW, 0.0, 1)])))
    assert str(frame["Packet"].dtype) == "Int64" and str(frame["Queue (pkts)"].dtype) == "Int64"
    assert frame.loc[frame["Event"] == "link_failed", "Packet"].isna().all()  # no packet, shown as missing
    assert filter_event_log(frame, packet_id=1)["Packet"].eq(1).all()  # filtering by packet ignores the gaps


def test_event_log_frame_for_an_empty_run_has_columns_and_no_rows() -> None:
    config = ExperimentConfig(flows=[], duration_s=1.0)
    frame = event_log_frame(run_simulation(config, build_topology([("a", "b", SLOW, 0.0, 1)])))
    assert frame.empty and "Event" in frame.columns


@pytest.fixture()
def log() -> pd.DataFrame:
    config = ExperimentConfig(
        flows=[Flow(f"f{i}", "a", "b", "bulk" if i < 3 else "realtime", "udp_like", 100, 0.0, 0.5, 1.0) for i in range(1, 5)],
        duration_s=1.0, queue_capacity_packets=1, failure_events=[FailureEvent(0.15, "link", "a-b")],
    )
    return event_log_frame(run_simulation(config, build_topology([("a", "b", SLOW, 0.0, 1)])))


def test_filters_combine_and_empty_filters_mean_no_restriction(log: pd.DataFrame) -> None:
    assert len(filter_event_log(log)) == len(log)
    assert set(filter_event_log(log, events=["dropped"])["Event"]) == {"dropped"}
    dropped = filter_event_log(log, events=["dropped"], drop_reasons=["queue_full"])
    assert len(dropped) > 0 and set(dropped["Drop reason"]) == {"queue_full"}
    assert set(filter_event_log(log, classes=["realtime"])["Class"]) == {"realtime"}
    assert set(filter_event_log(log, packet_id=1)["Packet"]) == {1}
    assert set(filter_event_log(log, location="A-B")["Location"]) == {"a-b"}  # case-insensitive substring
    window = filter_event_log(log, time_range=(0.1, 0.2))
    assert window["Time (s)"].between(0.1, 0.2).all() and 0 < len(window) < len(log)
    assert filter_event_log(log, events=["delivered"], drop_reasons=["queue_full"]).empty


def test_failure_events_have_no_packet(log: pd.DataFrame) -> None:
    failure = filter_event_log(log, events=["link_failed"])
    assert len(failure) == 1 and failure["Packet"].isna().all() and failure["Location"].iloc[0] == "a-b"


def test_location_filter_treats_text_literally(log: pd.DataFrame) -> None:
    assert filter_event_log(log, location="a-b.*").empty  # not interpreted as a regular expression


def test_csv_export_round_trips_the_displayed_table(log: pd.DataFrame) -> None:
    shown = filter_event_log(log, events=["dropped", "link_failed"])
    parsed = pd.read_csv(io.BytesIO(to_csv_bytes(shown)))
    assert len(parsed) == len(shown) and list(parsed.columns) == list(shown.columns)
    assert parsed["Time (s)"].tolist() == pytest.approx(shown["Time (s)"].tolist())
    assert parsed["Event"].tolist() == shown["Event"].tolist()


# --- chart series ---


def test_queue_length_series_by_hand_and_consistent_with_metrics() -> None:
    result = burst3()
    series = queue_length_series(result, "a-b", "a")
    assert series[0] == (0.0, 0) and series[-1] == (1.0, 0)
    assert max(length for _, length in series) == 2
    times = [t for t, _ in series]
    assert times == sorted(times)
    area = sum(series[i][1] * (series[i + 1][0] - series[i][0]) for i in range(len(series) - 1))
    expected = calculate_metrics(result).links[("a-b", "a")].avg_queue_length * result.duration_s
    assert area == pytest.approx(expected)  # the plotted series and the reported average agree
    assert queue_length_series(result, "a-b", "b") == [(0.0, 0), (1.0, 0)]  # idle direction
