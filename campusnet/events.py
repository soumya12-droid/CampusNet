"""Event queue and virtual simulation clock (specification section 5.1).

Simulated time only advances when the next event is popped; nothing here sleeps.

Events at exactly the same time are processed in this order, then by the order
they were scheduled (first scheduled, first processed):
  1. FAILURE      - topology changes take effect before anything else at that instant
  2. TX_COMPLETE  - a finished transmission frees its server before new arrivals contend for it
  3. NODE_ARRIVE  - a packet reaches a node
  4. GENERATE     - a flow creates a new packet
Equal times mean exactly equal floating-point values.
"""

import heapq
import itertools
import math
from dataclasses import dataclass
from enum import IntEnum


class EventKind(IntEnum):
    FAILURE = 0
    TX_COMPLETE = 1
    NODE_ARRIVE = 2
    GENERATE = 3


@dataclass(frozen=True)
class Event:
    time_s: float
    kind: EventKind
    payload: object


class EventQueue:
    def __init__(self) -> None:
        self._heap: list[tuple[float, int, int, Event]] = []
        self._sequence = itertools.count()
        self._now_s = 0.0

    @property
    def now_s(self) -> float:
        """The virtual clock: the time of the most recently popped event (0.0 before any)."""
        return self._now_s

    def __len__(self) -> int:
        return len(self._heap)

    def push(self, time_s: float, kind: EventKind, payload: object = None) -> None:
        if not math.isfinite(time_s):
            raise ValueError(f"event time must be finite, got {time_s}")
        if time_s < self._now_s:
            raise ValueError(f"cannot schedule an event at {time_s}, before the current time {self._now_s}")
        event = Event(time_s, kind, payload)
        heapq.heappush(self._heap, (time_s, int(kind), next(self._sequence), event))

    def peek_time_s(self) -> float | None:
        return self._heap[0][0] if self._heap else None

    def pop(self) -> Event:
        if not self._heap:
            raise IndexError("pop from an empty event queue")
        event = heapq.heappop(self._heap)[3]
        self._now_s = event.time_s
        return event
