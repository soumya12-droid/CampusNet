import pytest

from campusnet.config import ExperimentConfig
from campusnet.models import Flow
from campusnet.traffic import generate_flows, packet_times


def _flow(**overrides: object) -> Flow:
    values: dict[str, object] = dict(
        flow_id="f", source_id="a", destination_id="b", traffic_class="bulk", transport_mode="udp_like",
        packet_size_bytes=100, start_time_s=0.0, end_time_s=1.0, packet_interval_s=0.25,
    )
    values.update(overrides)
    return Flow(**values)  # type: ignore[arg-type]


def test_packet_times_are_start_plus_k_times_interval() -> None:
    assert packet_times(_flow(), duration_s=10.0) == [0.0, 0.25, 0.5, 0.75]


def test_end_time_is_exclusive() -> None:
    assert packet_times(_flow(end_time_s=0.5), duration_s=10.0) == [0.0, 0.25]


def test_duration_cuts_the_schedule_and_is_exclusive() -> None:
    assert packet_times(_flow(), duration_s=0.5) == [0.0, 0.25]


def test_start_offset_is_respected() -> None:
    assert packet_times(_flow(start_time_s=0.5, end_time_s=1.2), duration_s=10.0) == [0.5, 0.75, 1.0]


def test_flow_starting_after_the_duration_has_no_packets() -> None:
    assert packet_times(_flow(start_time_s=2.0, end_time_s=3.0), duration_s=1.0) == []


def test_times_do_not_drift_over_many_packets() -> None:
    times = packet_times(_flow(end_time_s=1000.0, packet_interval_s=0.1), duration_s=1000.0)
    assert len(times) == 10000
    assert times[-1] == pytest.approx(999.9, abs=1e-9)


def test_packet_times_are_repeatable() -> None:
    assert packet_times(_flow(packet_interval_s=0.1), 5.0) == packet_times(_flow(packet_interval_s=0.1), 5.0)


def test_generate_flows_returns_copies_in_order() -> None:
    config = ExperimentConfig(flows=[_flow(flow_id="x"), _flow(flow_id="y")])
    flows = generate_flows(config)
    assert [f.flow_id for f in flows] == ["x", "y"]
    flows[0].packet_size_bytes = 999
    assert config.flows[0].packet_size_bytes == 100


def test_generate_flows_rejects_invalid_flows() -> None:
    with pytest.raises(ValueError):
        generate_flows(ExperimentConfig(flows=[_flow(packet_interval_s=0)]))
