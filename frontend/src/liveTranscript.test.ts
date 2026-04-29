import assert from 'node:assert/strict';

import { applyLivePermissionDecision, applyRunStreamEvent, materializeLiveTranscriptItems } from './liveTranscript.ts';

function runSessionUpdate(runId: string, sessionId: string, seq: number, update: Record<string, unknown>) {
  return {
    event_id: `${runId}-${seq}`,
    run_id: runId,
    seq,
    event_type: 'run.session_update',
    payload: {
      run_id: runId,
      update
    },
    created_at: `2026-03-18T12:00:0${seq}.000Z`
  };
}

function permissionRequested(runId: string, sessionId: string, approvalId: string, seq: number) {
  return {
    event_id: `${runId}-${seq}`,
    run_id: runId,
    seq,
    event_type: 'run.permission.requested',
    payload: {
      run_id: runId,
      approval_id: approvalId,
      session_id: sessionId,
      status: 'pending',
      tool_call: { tool_call_id: 'tool-1', title: 'Run tool' },
      options: [{ option_id: 'opt-1', label: 'Approve' }],
      created_at: `2026-03-18T12:00:0${seq}.000Z`
    },
    created_at: `2026-03-18T12:00:0${seq}.000Z`
  };
}

function permissionDecision(runId: string, approvalId: string, seq: number) {
  return {
    event_id: `${runId}-${seq}`,
    run_id: runId,
    seq,
    event_type: 'run.permission.decision',
    payload: {
      run_id: runId,
      approval_id: approvalId,
      decision: 'selected',
      option_id: 'opt-1'
    },
    created_at: `2026-03-18T12:00:0${seq}.000Z`
  };
}

function completed(runId: string, sessionId: string, seq: number, stopReason: 'completed' | 'cancelled' = 'completed') {
  return {
    event_id: `${runId}-${seq}`,
    run_id: runId,
    seq,
    event_type: 'run.completed',
    payload: {
      run_id: runId,
      session_id: sessionId,
      stop_reason: stopReason
    },
    created_at: `2026-03-18T12:00:0${seq}.000Z`
  };
}

{
  const runId = 'run-1';
  const sessionId = 'session-1';
  let state = applyRunStreamEvent(null, runSessionUpdate(runId, sessionId, 1, {
    sessionUpdate: 'agent_message_chunk',
    content: { type: 'text', text: 'Hello' }
  }), sessionId);
  state = applyRunStreamEvent(state, runSessionUpdate(runId, sessionId, 2, {
    sessionUpdate: 'plan',
    entries: [{ content: 'Step one' }]
  }), sessionId);
  state = applyRunStreamEvent(state, runSessionUpdate(runId, sessionId, 3, {
    sessionUpdate: 'agent_message_chunk',
    content: { type: 'text', text: ' world' }
  }), sessionId);
  state = applyRunStreamEvent(state, completed(runId, sessionId, 4), sessionId);

  const items = materializeLiveTranscriptItems(state);
  assert.equal(items.length, 1);
  assert.equal(items[0].kind, 'assistant_segment');
  assert.equal(items[0].segment_kind, 'message');
  assert.equal(items[0].content, 'Hello world');
  assert.equal(items[0].status, 'completed');
  assert.equal(items[0].steps.length, 1);
  assert.equal(items[0].steps[0].summary, 'Plan updated');
}

{
  const runId = 'run-2';
  const sessionId = 'session-2';
  let state = applyRunStreamEvent(null, runSessionUpdate(runId, sessionId, 1, {
    sessionUpdate: 'current_mode_update',
    currentModeId: 'plan'
  }), sessionId);
  state = applyRunStreamEvent(state, permissionRequested(runId, sessionId, 'approval-1', 2), sessionId);

  const items = materializeLiveTranscriptItems(state);
  assert.equal(items.length, 2);
  assert.equal(items[0].kind, 'assistant_segment');
  assert.equal(items[0].segment_kind, 'steps');
  assert.equal(items[0].steps.length, 1);
  assert.equal(items[1].kind, 'permission_request');
  assert.equal(items[1].status, 'pending');
}

{
  const runId = 'run-3';
  const sessionId = 'session-3';
  let state = applyRunStreamEvent(null, permissionRequested(runId, sessionId, 'approval-1', 1), sessionId);
  state = applyRunStreamEvent(state, permissionDecision(runId, 'approval-1', 2), sessionId);

  const items = materializeLiveTranscriptItems(state);
  assert.equal(items.length, 1);
  assert.equal(items[0].kind, 'permission_request');
  assert.equal(items[0].status, 'selected');
  assert.equal(items[0].decision, 'selected');
  assert.equal(items[0].option_id, 'opt-1');
}

{
  const runId = 'run-4';
  const sessionId = 'session-4';
  let state = applyRunStreamEvent(null, runSessionUpdate(runId, sessionId, 1, {
    sessionUpdate: 'agent_thought_chunk',
    content: { type: 'text', text: 'thinking...' }
  }), sessionId);
  state = applyRunStreamEvent(state, completed(runId, sessionId, 2, 'cancelled'), sessionId);

  const items = materializeLiveTranscriptItems(state);
  assert.equal(items.length, 1);
  assert.equal(items[0].status, 'cancelled');
  assert.equal(items[0].segment_kind, 'thought');
}

const optimisticPermissionState = applyLivePermissionDecision(
  applyRunStreamEvent(null, permissionRequested('run-5', 'session-5', 'approval-1', 1), 'session-5'),
  'approval-1',
  'cancelled'
);
assert.equal(materializeLiveTranscriptItems(optimisticPermissionState)[0].status, 'cancelled');

console.log('liveTranscript tests passed');
