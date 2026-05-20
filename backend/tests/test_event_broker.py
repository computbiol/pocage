from __future__ import annotations

import unittest

from app.events import EventBroker, StreamEvent


class EventBrokerTests(unittest.IsolatedAsyncioTestCase):
    async def test_session_updates_are_coalesced(self) -> None:
        broker = EventBroker(queue_maxsize=3)
        queue = await broker.subscribe("run-1")

        await broker.publish("run-1", StreamEvent(event_type="run.session_update", payload={"seq": 1}))
        await broker.publish("run-1", StreamEvent(event_type="run.session_update", payload={"seq": 2}))

        self.assertEqual(queue.qsize(), 1)
        event = queue.get_nowait()
        self.assertEqual(event.event_type, "run.session_update")
        self.assertEqual(event.payload["seq"], 2)

    async def test_critical_event_displaces_low_priority_when_queue_is_full(self) -> None:
        broker = EventBroker(queue_maxsize=2)
        queue = await broker.subscribe("run-1")

        await broker.publish("run-1", StreamEvent(event_type="run.started", payload={"seq": 1}))
        await broker.publish("run-1", StreamEvent(event_type="run.session_update", payload={"seq": 2}))
        await broker.publish("run-1", StreamEvent(event_type="run.completed", payload={"seq": 3}))

        items = [queue.get_nowait(), queue.get_nowait()]
        event_types = {item.event_type for item in items}
        self.assertIn("run.completed", event_types)
        self.assertEqual(len(items), 2)
