import pytest

from campusnet.models import Link
from campusnet.queues import DROP_REASON_QUEUE_FULL, OutputPort, PacketQueue, dequeue_packet, enqueue_packet
from helpers import make_packet


def _drain(queue: PacketQueue) -> list[int | str]:
    order = []
    while (packet := dequeue_packet(queue)) is not None:
        order.append(packet.packet_id)
    return order


def test_fifo_dequeues_in_arrival_order_regardless_of_class() -> None:
    queue = PacketQueue("fifo", 10)
    for packet_id, traffic_class in [(1, "bulk"), (2, "realtime"), (3, "interactive"), (4, "bulk")]:
        assert enqueue_packet(queue, make_packet(packet_id, traffic_class))
    assert _drain(queue) == [1, 2, 3, 4]


def test_strict_priority_selects_highest_class_first() -> None:
    queue = PacketQueue("strict_priority", 10)
    for packet_id, traffic_class in [(1, "bulk"), (2, "interactive"), (3, "realtime")]:
        enqueue_packet(queue, make_packet(packet_id, traffic_class))
    assert _drain(queue) == [3, 2, 1]


def test_strict_priority_keeps_fifo_order_within_each_class() -> None:
    queue = PacketQueue("strict_priority", 10)
    arrivals = [
        (1, "bulk"), (2, "realtime"), (3, "interactive"), (4, "realtime"),
        (5, "bulk"), (6, "interactive"), (7, "realtime"),
    ]
    for packet_id, traffic_class in arrivals:
        enqueue_packet(queue, make_packet(packet_id, traffic_class))
    assert _drain(queue) == [2, 4, 7, 3, 6, 1, 5]


def test_strict_priority_serves_a_late_high_priority_packet_before_older_low_priority() -> None:
    queue = PacketQueue("strict_priority", 10)
    enqueue_packet(queue, make_packet(1, "bulk"))
    enqueue_packet(queue, make_packet(2, "bulk"))
    assert dequeue_packet(queue).packet_id == 1  # type: ignore[union-attr]
    enqueue_packet(queue, make_packet(3, "realtime"))
    assert _drain(queue) == [3, 2]


def test_dequeue_from_empty_queue_returns_none() -> None:
    assert dequeue_packet(PacketQueue("fifo", 5)) is None
    assert dequeue_packet(PacketQueue("strict_priority", 5)) is None


def test_full_queue_drops_arriving_packet_and_records_reason() -> None:
    queue = PacketQueue("fifo", 2)
    assert enqueue_packet(queue, make_packet(1))
    assert enqueue_packet(queue, make_packet(2))
    arriving = make_packet(3)
    assert enqueue_packet(queue, arriving) is False
    assert arriving.status == "dropped"
    assert arriving.drop_reason == DROP_REASON_QUEUE_FULL == "queue_full"
    assert queue.drop_count == 1
    assert len(queue) == 2
    assert _drain(queue) == [1, 2]  # queued packets are untouched; the arrival was the one dropped


def test_drop_tail_applies_to_strict_priority_too() -> None:
    queue = PacketQueue("strict_priority", 1)
    assert enqueue_packet(queue, make_packet(1, "bulk"))
    urgent = make_packet(2, "realtime")
    assert enqueue_packet(queue, urgent) is False  # arriving packet is dropped, not the queued bulk one
    assert urgent.drop_reason == "queue_full"
    assert _drain(queue) == [1]


def test_queued_packet_is_marked_queued_and_space_frees_after_dequeue() -> None:
    queue = PacketQueue("fifo", 1)
    first = make_packet(1)
    assert enqueue_packet(queue, first) and first.status == "queued"
    assert not enqueue_packet(queue, make_packet(2))
    dequeue_packet(queue)
    assert enqueue_packet(queue, make_packet(3))
    assert queue.drop_count == 1


def test_zero_capacity_queue_accepts_nothing() -> None:
    queue = PacketQueue("fifo", 0)
    assert not enqueue_packet(queue, make_packet(1))
    assert len(queue) == 0


@pytest.mark.parametrize(("policy", "capacity"), [("lifo", 5), ("fifo", -1), ("fifo", 2.5), ("fifo", True)])
def test_invalid_queue_arguments_raise(policy: str, capacity: object) -> None:
    with pytest.raises(ValueError):
        PacketQueue(policy, capacity)  # type: ignore[arg-type]


def test_unknown_traffic_class_is_rejected_by_priority_queue_without_changing_it() -> None:
    queue = PacketQueue("strict_priority", 5)
    with pytest.raises(ValueError):
        enqueue_packet(queue, make_packet(1, "video"))
    assert len(queue) == 0


# --- OutputPort: transmission server + waiting queue (1 Mbps, 1000 B -> 0.008 s per packet) ---


def _port(policy: str = "fifo", capacity: int = 2) -> OutputPort:
    return OutputPort(Link("l", "a", "b", 1e6, 0.0, 1, capacity), policy)  # type: ignore[arg-type]


def test_capacity_counts_waiting_packets_only_not_the_one_transmitting() -> None:
    port = _port(capacity=2)
    outcomes = [port.arrive(make_packet(i), 0.0).outcome for i in range(1, 5)]
    # p1 transmitting, p2 and p3 waiting (capacity 2), p4 dropped
    assert outcomes == ["transmitting", "queued", "queued", "dropped"]
    assert len(port.queue) == 2 and port.queue.drop_count == 1


def test_overflow_packet_is_marked_dropped_with_reason() -> None:
    port = _port(capacity=1)
    port.arrive(make_packet(1), 0.0)
    port.arrive(make_packet(2), 0.0)
    overflow = make_packet(3)
    assert port.arrive(overflow, 0.0).outcome == "dropped"
    assert overflow.status == "dropped" and overflow.drop_reason == "queue_full"


def test_zero_capacity_port_accepts_a_packet_only_when_the_server_is_idle() -> None:
    port = _port(capacity=0)
    assert port.arrive(make_packet(1), 0.0).outcome == "transmitting"
    assert port.arrive(make_packet(2), 0.001).outcome == "dropped"
    assert port.complete_transmission(0.008) is None
    assert port.arrive(make_packet(3), 0.009).outcome == "transmitting"


def test_waiting_packet_leaves_queue_exactly_when_its_transmission_starts() -> None:
    port = _port()
    port.arrive(make_packet(1), 0.0)
    waiting = make_packet(2)
    port.arrive(waiting, 0.001)
    assert len(port.queue) == 1 and waiting.status == "queued"
    started = port.complete_transmission(0.008)
    assert started is not None and started.packet is waiting
    assert len(port.queue) == 0 and waiting.status == "in_transit"


def test_strict_priority_does_not_preempt_a_packet_already_transmitting() -> None:
    port = _port("strict_priority", capacity=5)
    bulk = make_packet(1, "bulk")
    first = port.arrive(bulk, 0.0).transmission
    assert first is not None
    assert port.arrive(make_packet(2, "realtime"), 0.001).outcome == "queued"
    assert port.transmission is first  # the bulk packet keeps the server
    assert first.end_s == pytest.approx(0.008)  # unchanged by the realtime arrival
    nxt = port.complete_transmission(0.008)
    assert nxt is not None and nxt.packet.packet_id == 2


def test_priority_port_serves_waiting_packets_by_class_then_arrival() -> None:
    port = _port("strict_priority", capacity=5)
    port.arrive(make_packet(1, "bulk"), 0.0)  # transmitting
    for packet_id, traffic_class in [(2, "bulk"), (3, "interactive"), (4, "realtime"), (5, "realtime")]:
        port.arrive(make_packet(packet_id, traffic_class), 0.001)
    served, now = [], 0.008
    while (tx := port.complete_transmission(now)) is not None:
        served.append(tx.packet.packet_id)
        now = tx.end_s
    assert served == [4, 5, 3, 2]


def test_fifo_port_serves_in_arrival_order_even_if_a_realtime_packet_arrives_later() -> None:
    port = _port("fifo", capacity=5)
    port.arrive(make_packet(1, "bulk"), 0.0)
    port.arrive(make_packet(2, "bulk"), 0.001)
    port.arrive(make_packet(3, "realtime"), 0.002)
    first = port.complete_transmission(0.008)
    assert first is not None and first.packet.packet_id == 2


def test_priority_reduces_realtime_queueing_delay_and_increases_bulk_delay_vs_fifo() -> None:
    def realtime_and_bulk_delay(policy: str) -> tuple[float, float]:
        port = _port(policy, capacity=10)
        port.arrive(make_packet(1, "bulk"), 0.0)
        port.arrive(make_packet(2, "bulk"), 0.001)
        port.arrive(make_packet(3, "realtime"), 0.002)
        delays: dict[int | str, float] = {}
        now = 0.008
        while (tx := port.complete_transmission(now)) is not None:
            delays[tx.packet.packet_id] = tx.queueing_delay_s
            now = tx.end_s
        return delays[3], delays[2]

    fifo_realtime, fifo_bulk = realtime_and_bulk_delay("fifo")
    prio_realtime, prio_bulk = realtime_and_bulk_delay("strict_priority")
    # FIFO: bulk#2 starts 0.008 (waited 0.007), realtime starts 0.016 (waited 0.014)
    assert (fifo_bulk, fifo_realtime) == (pytest.approx(0.007), pytest.approx(0.014))
    # Priority: realtime starts 0.008 (waited 0.006), bulk#2 starts 0.016 (waited 0.015)
    assert (prio_realtime, prio_bulk) == (pytest.approx(0.006), pytest.approx(0.015))


def test_completing_with_no_transmission_or_too_early_raises() -> None:
    port = _port()
    with pytest.raises(ValueError):
        port.complete_transmission(0.0)
    port.arrive(make_packet(1), 0.0)
    with pytest.raises(ValueError):
        port.complete_transmission(0.004)  # transmission ends at 0.008


def test_drain_waiting_returns_service_order_and_leaves_the_transmission_alone() -> None:
    port = _port("strict_priority", capacity=5)
    transmitting = port.arrive(make_packet(1, "bulk"), 0.0).transmission
    for packet_id, traffic_class in [(2, "bulk"), (3, "realtime"), (4, "interactive")]:
        port.arrive(make_packet(packet_id, traffic_class), 0.001)
    assert [p.packet_id for p in port.drain_waiting()] == [3, 4, 2]
    assert len(port.queue) == 0 and port.transmission is transmitting
    assert port.complete_transmission(0.008) is None  # nothing left to start


def test_port_goes_idle_when_nothing_is_waiting() -> None:
    port = _port()
    port.arrive(make_packet(1), 0.0)
    assert port.is_busy
    assert port.complete_transmission(0.008) is None
    assert not port.is_busy
