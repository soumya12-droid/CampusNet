import pytest

from campusnet.config import ExperimentConfig, FailureEvent, validate_config, validate_flow
from campusnet.models import Flow, Link, Node, Packet


def _flow(**overrides: object) -> Flow:
    values: dict[str, object] = dict(
        flow_id="f1", source_id="stu1", destination_id="portal", traffic_class="interactive",
        transport_mode="udp_like", packet_size_bytes=1000, start_time_s=0.0, end_time_s=5.0, packet_interval_s=0.01,
    )
    values.update(overrides)
    return Flow(**values)  # type: ignore[arg-type]


def test_default_config_is_valid() -> None:
    assert validate_config(ExperimentConfig()) == []


def test_valid_config_with_flow_and_failure_is_valid() -> None:
    config = ExperimentConfig(
        flows=[_flow()], queue_policy="strict_priority", failure_events=[FailureEvent(5.0, "link", "r1-r2")],
        bandwidth_overrides={"r1-r2": 5e6},
    )
    assert validate_config(config) == []


def test_configs_do_not_share_mutable_defaults() -> None:
    first, second = ExperimentConfig(), ExperimentConfig()
    first.flows.append(_flow())
    first.bandwidth_overrides["x"] = 1.0
    assert second.flows == [] and second.bandwidth_overrides == {}


@pytest.mark.parametrize(
    ("overrides", "fragment"),
    [
        ({"duration_s": 0}, "duration_s"),
        ({"seed": 1.5}, "seed"),
        ({"queue_policy": "wfq"}, "queue_policy"),
        ({"queue_capacity_packets": -1}, "queue_capacity_packets"),
        ({"retry_limit": -1}, "retry_limit"),
        ({"bandwidth_overrides": {"l": 0}}, "bandwidth override"),
        ({"failure_events": [FailureEvent(99.0, "link", "l")]}, "time_s"),
        ({"failure_events": [FailureEvent(1.0, "switch", "l")]}, "kind"),  # type: ignore[arg-type]
    ],
)
def test_invalid_config_is_reported(overrides: dict[str, object], fragment: str) -> None:
    assert any(fragment in p for p in validate_config(ExperimentConfig(**overrides)))  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("overrides", "fragment"),
    [
        ({"traffic_class": "video"}, "traffic_class"),
        ({"transport_mode": "sctp"}, "transport_mode"),
        ({"packet_size_bytes": 0}, "packet_size_bytes"),
        ({"start_time_s": -1.0}, "start_time_s"),
        ({"end_time_s": -1.0}, "end_time_s"),
        ({"packet_interval_s": 0}, "packet_interval_s"),
    ],
)
def test_invalid_flow_is_reported(overrides: dict[str, object], fragment: str) -> None:
    assert any(fragment in p for p in validate_flow(_flow(**overrides)))


def test_model_defaults() -> None:
    assert Link("l", "a", "b", 1e6, 0.0, 1, 5).is_up
    packet = Packet(1, "a", "b", 100, "bulk", "udp_like", 0.0)
    assert packet.status == "created" and packet.path == [] and packet.drop_reason is None
    assert packet.delivered_at_s is None
    assert Node("a", "A", "switch").ip_address is None
