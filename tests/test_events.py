import pytest

from campusnet.events import EventKind, EventQueue


def _drain(queue: EventQueue) -> list[tuple[float, EventKind, object]]:
    out = []
    while len(queue):
        event = queue.pop()
        out.append((event.time_s, event.kind, event.payload))
    return out


def test_events_pop_in_time_order() -> None:
    queue = EventQueue()
    for time_s in (3.0, 1.0, 2.0):
        queue.push(time_s, EventKind.GENERATE, time_s)
    assert [t for t, _, _ in _drain(queue)] == [1.0, 2.0, 3.0]


def test_same_time_events_are_ordered_by_kind() -> None:
    queue = EventQueue()
    # pushed in the reverse of the required order
    queue.push(1.0, EventKind.GENERATE, "generate")
    queue.push(1.0, EventKind.NODE_ARRIVE, "arrive")
    queue.push(1.0, EventKind.TX_COMPLETE, "tx_complete")
    queue.push(1.0, EventKind.FAILURE, "failure")
    assert [p for _, _, p in _drain(queue)] == ["failure", "tx_complete", "arrive", "generate"]


def test_same_time_same_kind_events_keep_scheduling_order() -> None:
    queue = EventQueue()
    for label in "abcde":
        queue.push(2.0, EventKind.NODE_ARRIVE, label)
    assert [p for _, _, p in _drain(queue)] == list("abcde")


def test_earlier_time_beats_kind_priority() -> None:
    queue = EventQueue()
    queue.push(2.0, EventKind.FAILURE, "late failure")
    queue.push(1.0, EventKind.GENERATE, "early generate")
    assert [p for _, _, p in _drain(queue)] == ["early generate", "late failure"]


def test_clock_advances_only_when_events_are_popped() -> None:
    queue = EventQueue()
    queue.push(5.0, EventKind.GENERATE)
    assert queue.now_s == 0.0 and queue.peek_time_s() == 5.0
    queue.pop()
    assert queue.now_s == 5.0


def test_cannot_schedule_into_the_past_but_can_schedule_at_now() -> None:
    queue = EventQueue()
    queue.push(5.0, EventKind.GENERATE)
    queue.pop()
    queue.push(5.0, EventKind.NODE_ARRIVE)  # same instant is allowed
    with pytest.raises(ValueError):
        queue.push(4.999, EventKind.NODE_ARRIVE)


@pytest.mark.parametrize("bad", [float("nan"), float("inf")])
def test_non_finite_times_are_rejected(bad: float) -> None:
    with pytest.raises(ValueError):
        EventQueue().push(bad, EventKind.GENERATE)


def test_empty_queue_behaviour() -> None:
    queue = EventQueue()
    assert queue.peek_time_s() is None and len(queue) == 0
    with pytest.raises(IndexError):
        queue.pop()
