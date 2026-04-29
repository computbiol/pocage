from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class StreamEvent:
    event_type: str
    payload: dict[str, Any]


class EventBroker:
    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue[StreamEvent]]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def publish(self, run_id: str, event: StreamEvent) -> None:
        async with self._lock:
            queues = list(self._subscribers.get(run_id, set()))

        for queue in queues:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                pass

    async def subscribe(self, run_id: str) -> asyncio.Queue[StreamEvent]:
        queue: asyncio.Queue[StreamEvent] = asyncio.Queue(maxsize=1000)
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
