from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from app.runtime_state import RuntimeState


class RuntimeStateLimitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runtime = RuntimeState(
            max_events_per_run=2,
            max_event_payload_bytes=400,
            completed_run_ttl_seconds=60,
            max_local_runs_per_session=2,
            remote_transcript_ttl_seconds=60,
        )
        self.runtime.upsert_session(
            session_id="sess-1",
            daemon_id="daemon-1",
            title="Session",
            cwd="/tmp/work",
            updated_at="2026-03-18T00:00:00Z",
        )

    def _create_run(self) -> str:
        created = self.runtime.create_run(
            session_id="sess-1",
            daemon_id="daemon-1",
            cwd="/tmp/work",
            content="hello",
        )
        return created["run_id"]

    def test_run_event_seq_stays_monotonic_when_history_is_pruned(self) -> None:
        run_id = self._create_run()

        self.runtime.store_run_event(run_id, "run.queued", {"run_id": run_id, "index": 1})
        self.runtime.store_run_event(run_id, "run.started", {"run_id": run_id, "index": 2})
        self.runtime.store_run_event(run_id, "run.session_update", {"run_id": run_id, "index": 3, "update": {"sessionUpdate": "agent_message_chunk"}})
        self.runtime.store_run_event(run_id, "run.completed", {"run_id": run_id, "session_id": "sess-1", "stop_reason": "completed"})

        events = self.runtime.list_run_events(run_id)
        self.assertEqual([event["seq"] for event in events], [3, 4])
        self.assertEqual([event["event_type"] for event in events], ["run.session_update", "run.completed"])

    def test_oversized_session_update_is_truncated_and_projects_to_placeholder_step(self) -> None:
        run_id = self._create_run()

        self.runtime.store_run_event(
            run_id,
            "run.session_update",
            {
                "run_id": run_id,
                "update": {
                    "sessionUpdate": "agent_message_chunk",
                    "content": {
                        "type": "text",
                        "text": "x" * 2_000,
                    },
                    "title": "Large message chunk",
                },
            },
        )

        event = self.runtime.list_run_events(run_id)[0]
        self.assertTrue(event["payload"]["truncated"])
        self.assertTrue(event["payload"]["update"]["truncated"])

        items = self.runtime.build_session_transcript("sess-1")
        assistant_items = [item for item in items if item["kind"] == "assistant_segment"]
        self.assertEqual(len(assistant_items), 1)
        self.assertEqual(assistant_items[0]["segment_kind"], "steps")
        self.assertEqual(assistant_items[0]["steps"][0]["summary"], "Update truncated: agent_message_chunk")

    def test_cleanup_removes_expired_terminal_run_related_state(self) -> None:
        run_id = self._create_run()
        self.runtime.create_permission_request(
            run_id=run_id,
            approval_id="approval-1",
            executor_id="daemon-1",
            session_id="sess-1",
            tool_call={"tool_name": "test"},
            options=[{"id": "allow"}],
        )
        self.runtime.store_run_event(run_id, "run.queued", {"run_id": run_id})
        self.runtime.mark_run_completed(run_id, stop_reason="completed")

        expired_at = datetime.now(UTC) - timedelta(seconds=120)
        self.runtime._runs[run_id].terminal_at = expired_at.isoformat()
        self.runtime._runs[run_id].updated_at = expired_at.isoformat()

        result = self.runtime.cleanup(now=datetime.now(UTC))

        self.assertEqual(result["removed_runs"], 1)
        self.assertIsNone(self.runtime.get_run(run_id))
        self.assertEqual(self.runtime.list_run_events(run_id), [])
        self.assertEqual(self.runtime.list_permission_requests(run_id), [])

    def test_session_keeps_only_recent_terminal_runs(self) -> None:
        first_run = self._create_run()
        self.runtime.mark_run_completed(first_run, stop_reason="completed")

        second_run = self._create_run()
        self.runtime.mark_run_completed(second_run, stop_reason="completed")

        third_run = self._create_run()
        self.runtime.mark_run_completed(third_run, stop_reason="completed")

        self.assertIsNone(self.runtime.get_run(first_run))
        self.assertIsNotNone(self.runtime.get_run(second_run))
        self.assertIsNotNone(self.runtime.get_run(third_run))
