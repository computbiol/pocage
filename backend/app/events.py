from __future__ import annotations

import asyncio
import json
import contextlib
from collections import defaultdict
from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class StreamEvent:
    event_type: str
    payload: dict[str, Any]


CRITICAL_EVENT_TYPES = {
    "run.accepted",
    "run.completed",
    "run.error",
    "run.permission.decision",
    "run.permission.requested",
}
COALESCING_EVENT_TYPES = {"run.session_update"}
LOW_PRIORITY_EVENT_TYPES = COALESCING_EVENT_TYPES | {"run.queued", "run.started"}


class EventBroker:
    def __init__(self, *, queue_maxsize: int = 1000) -> None:
        self._subscribers: dict[str, set[asyncio.Queue[StreamEvent]]] = defaultdict(set)
        self._lock = asyncio.Lock()
        self._queue_maxsize = max(1, int(queue_maxsize))

    def _drop_first_matching(
        self,
        queue: asyncio.Queue[StreamEvent],
        predicate,
    ) -> bool:
        kept: list[StreamEvent] = []
        dropped = False
        while True:
            try:
                current = queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if not dropped and predicate(current):
                dropped = True
                continue
            kept.append(current)

        for item in kept:
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(item)
        return dropped

    def _drop_oldest(self, queue: asyncio.Queue[StreamEvent]) -> bool:
        with contextlib.suppress(asyncio.QueueEmpty):
            queue.get_nowait()
            return True
        return False

    def _publish_to_queue(self, queue: asyncio.Queue[StreamEvent], event: StreamEvent) -> None:
        if event.event_type in COALESCING_EVENT_TYPES:
            self._drop_first_matching(queue, lambda current: current.event_type == event.event_type)

        if not queue.full():
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(event)
            return

        if event.event_type in CRITICAL_EVENT_TYPES:
            dropped = self._drop_first_matching(queue, lambda current: current.event_type in LOW_PRIORITY_EVENT_TYPES)
            if not dropped:
                self._drop_oldest(queue)
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(event)
            return

        if event.event_type in LOW_PRIORITY_EVENT_TYPES:
            if self._drop_first_matching(queue, lambda current: current.event_type in LOW_PRIORITY_EVENT_TYPES):
                with contextlib.suppress(asyncio.QueueFull):
                    queue.put_nowait(event)
            return

        if self._drop_first_matching(queue, lambda current: current.event_type in LOW_PRIORITY_EVENT_TYPES):
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(event)

    async def publish(self, run_id: str, event: StreamEvent) -> None:
        async with self._lock:
            queues = list(self._subscribers.get(run_id, set()))

        for queue in queues:
            self._publish_to_queue(queue, event)

    async def subscribe(self, run_id: str) -> asyncio.Queue[StreamEvent]:
        queue: asyncio.Queue[StreamEvent] = asyncio.Queue(maxsize=self._queue_maxsize)
        async with self._lock:
            self._subscribers[run_id].add(queue)
        return queue

    async def unsubscribe(self, run_id: str, queue: asyncio.Queue[StreamEvent]) -> None:
        async with self._lock:
            subscribers = self._subscribers.get(run_id)
            if subscribers is None:
                return
            subscribers.discard(queue)
            if not subscribers:
                self._subscribers.pop(run_id, None)


def format_sse(event_type: str, payload: dict[str, Any]) -> str:
    return f"event: {event_type}\ndata: {json.dumps(payload, ensure_ascii=True)}\n\n"
