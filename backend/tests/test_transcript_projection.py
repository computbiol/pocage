from __future__ import annotations

import unittest

from acp import start_tool_call, update_agent_message_text, update_tool_call
from acp.schema import TextContentBlock

from app.transcript_projection import build_run_transcript_items


def _event(seq: int, created_at: str, update) -> dict:
    return {
        "event_id": f"evt-{seq}",
        "run_id": "run-1",
        "seq": seq,
        "event_type": "run.session_update",
        "payload": {
            "run_id": "run-1",
            "update": update.model_dump(mode="json", by_alias=True),
        },
        "created_at": created_at,
    }


class TranscriptProjectionTests(unittest.TestCase):
    def test_projection_splits_visible_assistant_phases_around_tool_activity(self) -> None:
        items = build_run_transcript_items(
            run_id="run-1",
            session_id="sess-1",
            created_at="2026-03-18T10:00:00Z",
            updated_at="2026-03-18T10:00:04Z",
            status="completed",
            session_updates=[
                _event(1, "2026-03-18T10:00:01Z", update_agent_message_text("First phase.")),
                _event(
                    2,
                    "2026-03-18T10:00:02Z",
                    start_tool_call("call-1", "Search workspace", kind="search", status="in_progress"),
                ),
                _event(
                    3,
                    "2026-03-18T10:00:03Z",
                    update_tool_call(
                        "call-1",
                        status="completed",
                        content=[
                            {
                                "type": "content",
                                "content": TextContentBlock(type="text", text="Found files"),
                            }
                        ],
                    ),
                ),
                _event(4, "2026-03-18T10:00:04Z", update_agent_message_text("Second phase.")),
            ],
            permissions=[],
        )

        assistant_segments = [item for item in items if item["kind"] == "assistant_segment"]
        self.assertEqual([item["content"] for item in assistant_segments], ["First phase.", "Second phase."])
        self.assertEqual(assistant_segments[0]["segment_kind"], "message")
        self.assertEqual(assistant_segments[1]["segment_kind"], "message")
        self.assertEqual(assistant_segments[0]["steps"], [])
        self.assertGreaterEqual(len(assistant_segments[1]["steps"]), 2)

    def test_projection_materializes_step_only_segment(self) -> None:
        items = build_run_transcript_items(
            run_id="run-2",
            session_id="sess-1",
            created_at="2026-03-18T10:10:00Z",
            updated_at="2026-03-18T10:10:03Z",
            status="completed",
            session_updates=[
                _event(
                    1,
                    "2026-03-18T10:10:01Z",
                    start_tool_call("call-2", "Read file", kind="read", status="in_progress"),
                ),
                _event(2, "2026-03-18T10:10:02Z", update_tool_call("call-2", status="completed")),
            ],
            permissions=[],
        )

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["kind"], "assistant_segment")
        self.assertEqual(items[0]["segment_kind"], "steps")
        self.assertEqual(items[0]["content"], "")
        self.assertEqual(items[0]["run_id"], "run-2")
        self.assertEqual(len(items[0]["steps"]), 2)

    def test_projection_emits_permission_item_between_segments(self) -> None:
        permission = {
            "approval_id": "approval-1",
            "run_id": "run-3",
            "session_id": "sess-1",
            "status": "pending",
            "tool_call": {"title": "Edit file"},
            "options": [{"option_id": "allow", "label": "Allow"}],
            "decision": None,
            "option_id": None,
            "created_at": "2026-03-18T10:20:02Z",
            "decided_at": None,
        }
        items = build_run_transcript_items(
            run_id="run-3",
            session_id="sess-1",
            created_at="2026-03-18T10:20:00Z",
            updated_at="2026-03-18T10:20:03Z",
            status="streaming",
            session_updates=[
                _event(1, "2026-03-18T10:20:01Z", update_agent_message_text("Need permission.")),
                _event(2, "2026-03-18T10:20:03Z", update_agent_message_text("Resumed after approval.")),
            ],
            permissions=[permission],
        )

        self.assertEqual([item["kind"] for item in items], ["assistant_segment", "permission_request", "assistant_segment"])
        self.assertEqual(items[1]["approval_id"], "approval-1")


if __name__ == "__main__":
    unittest.main()
