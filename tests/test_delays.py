import pytest

from campusnet.models import Link
from campusnet.queues import OutputPort
from campusnet.timing import link_delay_s, transmission_delay_s
from helpers import make_packet


def _link(bandwidth_bps: float = 1e6, propagation_delay_s: float = 0.010, capacity: int = 10) -> Link:
    return Link("l", "a", "b", bandwidth_bps, propagation_delay_s, 1, capacity)


def test_transmission_delay_matches_formula_by_hand() -> None:
    # 1500 B = 12000 bits; 12000 / 10e6 = 0.0012 s
    assert transmission_delay_s(1500, 10e6) == pytest.approx(0.0012)
    # 1000 B = 8000 bits; 8000 / 1e6 = 0.008 s
    assert transmission_delay_s(1000, 1e6) == pytest.approx(0.008)
    # 125 B = 1000 bits; 1000 / 1000 = 1 s
    assert transmission_delay_s(125, 1000) == pytest.approx(1.0)


def test_transmission_delay_uses_bits_not_bytes() -> None:
    assert transmission_delay_s(1, 8) == pytest.approx(1.0)


def test_slower_link_takes_longer_for_the_same_packet() -> None:
    assert transmission_delay_s(1500, 1e6) > transmission_delay_s(1500, 10e6) > transmission_delay_s(1500, 100e6)
    assert transmission_delay_s(1500, 1e6) == pytest.approx(10 * transmission_delay_s(1500, 10e6))


def test_link_delay_is_transmission_plus_propagation() -> None:
    # 0.008 s transmission + 0.010 s propagation
    assert link_delay_s(1000, _link()) == pytest.approx(0.018)


def test_zero_propagation_delay_gives_pure_transmission_delay() -> None:
    assert link_delay_s(1000, _link(propagation_delay_s=0.0)) == pytest.approx(0.008)


@pytest.mark.parametrize(("size", "bandwidth"), [(0, 1e6), (-1, 1e6), (1000, 0), (1000, -5)])
def test_invalid_delay_inputs_raise(size: int, bandwidth: float) -> None:
    with pytest.raises(ValueError):
        transmission_delay_s(size, bandwidth)


def test_idle_port_starts_transmission_immediately_with_no_queueing_delay() -> None:
    port = OutputPort(_link(), "fifo")
    result = port.arrive(make_packet(1), now_s=5.0)
    assert result.outcome == "transmitting"
    tx = result.transmission
    assert tx is not None
    assert tx.start_s == 5.0
    assert tx.end_s == pytest.approx(5.008)
    assert tx.reaches_next_node_s == pytest.approx(5.018)  # propagation added once
    assert tx.queueing_delay_s == 0.0
    assert len(port.queue) == 0  # never occupied the waiting queue


def test_propagation_delay_is_added_once_per_traversed_link() -> None:
    # Two links in series, no contention: end-to-end = (tx1 + prop1) + (tx2 + prop2).
    first = OutputPort(_link(bandwidth_bps=1e6, propagation_delay_s=0.010), "fifo")
    second = OutputPort(_link(bandwidth_bps=2e6, propagation_delay_s=0.003), "fifo")
    packet = make_packet(1, size_bytes=1000)
    hop1 = first.arrive(packet, 0.0).transmission
    assert hop1 is not None and hop1.reaches_next_node_s == pytest.approx(0.018)
    hop2 = second.arrive(packet, hop1.reaches_next_node_s).transmission
    assert hop2 is not None
    # hop2: tx = 8000 / 2e6 = 0.004, prop = 0.003 -> 0.018 + 0.007 = 0.025
    assert hop2.reaches_next_node_s == pytest.approx(0.025)


def test_queueing_delay_increases_when_packets_wait_for_a_busy_link() -> None:
    # tx = 0.008 s each. p1 at t=0 starts at once; p2 at 0.002; p3 at 0.004.
    port = OutputPort(_link(), "fifo")
    first = port.arrive(make_packet(1), 0.0).transmission
    assert first is not None and first.end_s == pytest.approx(0.008)
    assert port.arrive(make_packet(2), 0.002).outcome == "queued"
    assert port.arrive(make_packet(3), 0.004).outcome == "queued"

    second = port.complete_transmission(0.008)
    assert second is not None and second.packet.packet_id == 2
    assert second.start_s == pytest.approx(0.008)
    assert second.queueing_delay_s == pytest.approx(0.006)  # 0.008 - 0.002
    assert second.reaches_next_node_s == pytest.approx(0.016 + 0.010)

    third = port.complete_transmission(second.end_s)
    assert third is not None and third.packet.packet_id == 3
    assert third.start_s == pytest.approx(0.016)
    assert third.queueing_delay_s == pytest.approx(0.012)  # 0.016 - 0.004
    assert third.reaches_next_node_s == pytest.approx(0.024 + 0.010)

    assert 0.0 < second.queueing_delay_s < third.queueing_delay_s


def test_same_packet_takes_longer_on_a_slower_link_end_to_end() -> None:
    slow = OutputPort(_link(bandwidth_bps=1e6, propagation_delay_s=0.0), "fifo")
    fast = OutputPort(_link(bandwidth_bps=10e6, propagation_delay_s=0.0), "fifo")
    slow_tx = slow.arrive(make_packet(1), 0.0).transmission
    fast_tx = fast.arrive(make_packet(1), 0.0).transmission
    assert slow_tx is not None and fast_tx is not None
    assert slow_tx.reaches_next_node_s > fast_tx.reaches_next_node_s
