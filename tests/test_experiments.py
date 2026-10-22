import pytest

from campusnet.config import ExperimentConfig, validate_config
from campusnet.experiments import (
    BOTTLENECK_BANDWIDTH_BPS,
    DEFAULT_DURATION_S,
    DEFAULT_SEED,
    PRESETS,
    ExperimentResult,
    base_config,
    run_preset,
    run_preset_b,
    standard_flows,
)
from campusnet.metrics import calculate_metrics

EXPECTED = {  # preset -> (number of runs, the one config field that may differ between runs)
    "A": (2, ["flows"]),
    "B": (2, ["queue_policy"]),
    "C": (4, ["bandwidth_overrides"]),
    "D": (3, ["failure_events"]),
    "E": (4, ["queue_capacity_packets"]),
}


@pytest.fixture(scope="module")
def results() -> dict[str, ExperimentResult]:
    return {key: run_preset(key) for key in PRESETS}


def offered_bps(intensity: float) -> float:
    return sum(f.packet_size_bytes * 8 / f.packet_interval_s for f in standard_flows(intensity, 1.0))


def test_workload_design_overloads_the_bottleneck_only_at_high_intensity() -> None:
    assert offered_bps(1.0) > BOTTLENECK_BANDWIDTH_BPS > offered_bps(0.4)


def test_base_config_is_valid_and_reproducible() -> None:
    assert validate_config(base_config()) == []
    assert base_config() == base_config()
    with pytest.raises(ValueError):
        standard_flows(0, 1.0)


def test_only_presets_a_to_e_exist() -> None:
    assert sorted(PRESETS) == ["A", "B", "C", "D", "E"]
    with pytest.raises(ValueError):
        run_preset("F")


# --- structure and recorded configuration ---


@pytest.mark.parametrize("key", sorted(EXPECTED))
def test_result_structure_records_each_runs_configuration(results: dict[str, ExperimentResult], key: str) -> None:
    experiment = results[key]
    assert experiment.experiment_id == key and experiment.title and experiment.varied and experiment.held_constant
    assert len(experiment.runs) == EXPECTED[key][0]
    assert len({run.label for run in experiment.runs}) == len(experiment.runs)
    for run in experiment.runs:
        assert isinstance(run.config, ExperimentConfig)
        assert run.result.config is run.config  # the result carries the config that produced it
        assert (run.config.seed, run.config.duration_s) == (DEFAULT_SEED, DEFAULT_DURATION_S)
        assert run.config.topology == "default" and run.config.flows
        assert run.metrics == calculate_metrics(run.result)


@pytest.mark.parametrize("key", sorted(EXPECTED))
def test_tables_have_one_row_per_run_and_class(results: dict[str, ExperimentResult], key: str) -> None:
    experiment = results[key]
    rows, class_rows = experiment.rows(), experiment.class_rows()
    assert [row["run"] for row in rows] == [run.label for run in experiment.runs]
    assert len(class_rows) == 3 * len(experiment.runs)
    for row, run in zip(rows, experiment.runs):
        overall = run.metrics.overall
        assert (row["generated"], row["delivered"], row["dropped"], row["unresolved"]) == (
            overall.generated, overall.delivered, overall.dropped, overall.unresolved,
        )
        assert row["generated"] == row["delivered"] + row["dropped"] + row["unresolved"]
        assert all(setting in row for setting in run.settings)
    for run in experiment.runs:  # class rows add up to the run's row
        mine = [r for r in class_rows if r["run"] == run.label]
        assert sum(r["generated"] for r in mine) == run.metrics.overall.generated  # type: ignore[misc]


@pytest.mark.parametrize("key", sorted(EXPECTED))
def test_each_preset_changes_exactly_one_main_variable(results: dict[str, ExperimentResult], key: str) -> None:
    assert results[key].comparison.differing_fields == EXPECTED[key][1]


def test_fair_comparison_means_identical_workload_and_seed(results: dict[str, ExperimentResult]) -> None:
    for key in "BCDE":
        configs = [run.config for run in results[key].runs]
        assert all(c.flows == configs[0].flows and c.seed == configs[0].seed for c in configs)
    assert results["B"].runs[0].result.generated == results["B"].runs[1].result.generated
    # A varies the load itself, so the workloads differ there by design
    assert results["A"].runs[0].config.flows != results["A"].runs[1].config.flows


# --- repeatability ---


@pytest.mark.parametrize("key", sorted(EXPECTED))
def test_presets_are_repeatable(key: str) -> None:
    first, second = run_preset(key, duration_s=1.0), run_preset(key, duration_s=1.0)
    assert first.rows() == second.rows()
    assert first.class_rows() == second.class_rows()
    assert first.routes == second.routes
    for a, b in zip(first.runs, second.runs):
        assert a.result.records == b.result.records


def test_preset_function_and_run_preset_agree() -> None:
    assert run_preset("B", 1.0).rows() == run_preset_b(1.0).rows()


# --- per-preset content ---


def test_preset_a_loads_and_bottleneck_drops(results: dict[str, ExperimentResult]) -> None:
    low, high = results["A"].runs
    assert (low.settings["intensity"], high.settings["intensity"]) == (0.4, 1.0)
    assert high.metrics.overall.generated > low.metrics.overall.generated
    assert low.metrics.links[("r1-r2", "r1")].queue_drops == 0
    assert high.metrics.links[("r1-r2", "r1")].queue_drops > 0
    assert high.metrics.links[("r1-r2", "r1")].max_queue_length > low.metrics.links[("r1-r2", "r1")].max_queue_length


def test_preset_b_policies_are_applied_and_produce_different_measurements(results: dict[str, ExperimentResult]) -> None:
    fifo, priority = results["B"].runs
    assert (fifo.config.queue_policy, priority.config.queue_policy) == ("fifo", "strict_priority")
    assert fifo.metrics.by_class["realtime"].avg_delay_s != priority.metrics.by_class["realtime"].avg_delay_s
    assert fifo.metrics.by_class["bulk"].mean_queueing_delay_s != priority.metrics.by_class["bulk"].mean_queueing_delay_s


def test_preset_c_applies_each_bottleneck_bandwidth(results: dict[str, ExperimentResult]) -> None:
    runs = results["C"].runs
    assert [r.settings["bottleneck_mbps"] for r in runs] == [5, 10, 20, 40]
    for run in runs:
        topology = run.result.topology
        assert topology.links["r1-r2"].bandwidth_bps == run.settings["bottleneck_mbps"] * 1e6  # type: ignore[operator]
        assert topology.links["sw1-r1"].bandwidth_bps == 100e6  # nothing else changed


def test_preset_e_applies_each_queue_capacity_to_every_link(results: dict[str, ExperimentResult]) -> None:
    runs = results["E"].runs
    assert [r.settings["queue_capacity_packets"] for r in runs] == [5, 20, 50, 200]
    for run in runs:
        capacities = {link.queue_capacity_packets for link in run.result.topology.links.values()}
        assert capacities == {run.settings["queue_capacity_packets"]}
        assert run.metrics.links[("r1-r2", "r1")].max_queue_length <= run.settings["queue_capacity_packets"]  # type: ignore[operator]


# --- preset D: routing behavior vs packet outcomes ---


def routes_for(experiment: ExperimentResult, failed_link: str) -> dict[str, dict[str, object]]:
    return {str(r["flow"]): r for r in experiment.routes if r["failed_link"] == failed_link}


def test_preset_d_redundant_failure_reroutes_every_flow(results: dict[str, ExperimentResult]) -> None:
    routes = routes_for(results["D"], "r1-r2")
    assert len(routes) == 4
    assert all(r["route_changed"] and r["reachable_after"] for r in routes.values())
    assert routes["download-1"]["path_before"] == ["stu3", "sw2", "r1", "r2", "files"]
    assert routes["download-1"]["path_after"] == ["stu3", "sw2", "r2", "files"]
    assert routes["lecture"]["path_after"] == ["classroom", "sw2", "r2", "portal"]


def test_preset_d_redundant_failure_packet_outcomes(results: dict[str, ExperimentResult]) -> None:
    baseline, redundant, _ = results["D"].runs
    fail_at = 1.0
    assert baseline.metrics.overall.drop_reasons == {}
    assert "no_route" not in redundant.metrics.overall.drop_reasons  # an alternative path always existed
    later = [p for p in redundant.result.packets if p.created_at_s >= fail_at]
    assert later and all("r1" not in p.path and p.path[-2] == "r2" for p in later)  # routed around the failed link
    assert any(p.status == "delivered" for p in later)
    # only packets caught in the failed link's queue are lost
    assert set(redundant.metrics.overall.drop_reasons) <= {"link_down"}


def test_preset_d_bridge_failure_disconnects_only_the_portal(results: dict[str, ExperimentResult]) -> None:
    routes = routes_for(results["D"], "r2-portal")
    for flow_id in ("lecture", "web-1"):
        assert routes[flow_id]["path_after"] is None and not routes[flow_id]["reachable_after"]
    for flow_id in ("download-1", "download-2"):
        assert not routes[flow_id]["route_changed"] and routes[flow_id]["reachable_after"]


def test_preset_d_bridge_failure_packet_outcomes(results: dict[str, ExperimentResult]) -> None:
    _, _, bridge = results["D"].runs
    fail_at = 1.0
    to_portal_after = [p for p in bridge.result.packets if p.destination_id == "portal" and p.created_at_s >= fail_at]
    assert to_portal_after
    assert all(p.status == "dropped" and p.drop_reason == "no_route" and p.path == [] for p in to_portal_after)
    to_files = [p for p in bridge.result.packets if p.destination_id == "files"]
    assert to_files and all(p.drop_reason is None for p in to_files)
    assert bridge.metrics.overall.drop_reasons["no_route"] == len(to_portal_after)
    assert bridge.metrics.by_class["bulk"].plr == 0.0


def test_preset_d_keeps_routing_behavior_separate_from_packet_outcomes(results: dict[str, ExperimentResult]) -> None:
    experiment = results["D"]
    assert experiment.routes and all("delivered" not in route and "dropped" not in route for route in experiment.routes)
    assert all("path_after" not in row and "route_changed" not in row for row in experiment.rows())
    assert {"drops_link_down", "drops_no_route"} <= set(experiment.rows()[0])
