import type {
  AssistantTranscriptItem,
  PermissionDecision,
  PermissionTranscriptItem,
  TranscriptStep
} from './api';

type LiveRunStatus = 'queued' | 'accepted' | 'running' | 'completed' | 'cancelled' | 'error';

type LiveSegmentKind = AssistantTranscriptItem['segment_kind'];

export type RunStreamEventRow = {
  event_id: string;
  run_id: string;
  seq: number;
  event_type: string;
  payload: Record<string, unknown>;
  created_at: string;
};

type DraftAssistantSegment = {
  segment_id: string;
  session_id: string;
  run_id: string;
  segment_kind: LiveSegmentKind;
  content: string;
  status: AssistantTranscriptItem['status'];
  steps: TranscriptStep[];
  created_at: string;
  updated_at: string;
};

export type LiveRunProjection = {
  runId: string;
  sessionId: string;
  status: LiveRunStatus;
  nextSegmentIndex: number;
  items: Array<AssistantTranscriptItem | PermissionTranscriptItem>;
  activeSegment: DraftAssistantSegment | null;
  pendingSteps: TranscriptStep[];
};

function asRecord(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return null;
  }
  return value as Record<string, unknown>;
}

function readField<T>(value: Record<string, unknown> | null, keys: string[]): T | undefined {
  if (!value) {
    return undefined;
  }
  for (const key of keys) {
    if (Object.prototype.hasOwnProperty.call(value, key)) {
      return value[key] as T;
    }
  }
  return undefined;
}

function readString(value: Record<string, unknown> | null, keys: string[]): string | undefined {
  const field = readField<unknown>(value, keys);
  return typeof field === 'string' ? field : undefined;
}

function readArray(value: Record<string, unknown> | null, keys: string[]): unknown[] | undefined {
  const field = readField<unknown>(value, keys);
  return Array.isArray(field) ? field : undefined;
}

function compactJson(value: unknown): string | null {
  if (value === undefined || value === null) {
    return null;
  }
  try {
    const text = JSON.stringify(value, null, 0);
    if (!text || text === '{}' || text === '[]' || text === 'null' || text === '""') {
      return null;
    }
    return text.length <= 240 ? text : `${text.slice(0, 237)}...`;
  } catch {
    return null;
  }
}

function contentToText(content: unknown): string {
  const item = asRecord(content);
  if (!item) {
    return '';
  }

  const type = readString(item, ['type']);
  switch (type) {
    case 'text': {
      const text = readString(item, ['text']);
      return text ?? '';
    }
    case 'image':
      return '[image]';
    case 'audio':
      return '[audio]';
    case 'resource_link': {
      const name = readString(item, ['name']);
      if (name && name.trim()) {
        return `@${name.trim()}`;
      }
      const uri = readString(item, ['uri']);
      if (uri && uri.trim()) {
        return uri.trim();
      }
      return '[resource]';
    }
    case 'resource': {
      const resource = asRecord(readField<unknown>(item, ['resource']));
      const uri = readString(resource, ['uri']);
      if (uri && uri.trim()) {
        return uri.trim();
      }
      return '[resource]';
    }
    default:
      return '';
  }
}

function segmentStatus(status: LiveRunStatus): AssistantTranscriptItem['status'] {
  switch (status) {
    case 'completed':
      return 'completed';
    case 'cancelled':
      return 'cancelled';
    case 'error':
      return 'error';
    default:
      return 'streaming';
  }
}

function createProjection(runId: string, sessionId: string, status: LiveRunStatus = 'running'): LiveRunProjection {
  return {
    runId,
    sessionId,
    status,
    nextSegmentIndex: 0,
    items: [],
    activeSegment: null,
    pendingSteps: []
  };
}

function createAssistantSegment(params: {
  runId: string;
  sessionId: string;
  segmentId: string;
  segmentKind: LiveSegmentKind;
  content?: string;
  steps?: TranscriptStep[];
  createdAt: string;
  updatedAt?: string;
  status: AssistantTranscriptItem['status'];
}): DraftAssistantSegment {
  return {
    segment_id: params.segmentId,
    session_id: params.sessionId,
    run_id: params.runId,
    segment_kind: params.segmentKind,
    content: params.content ?? '',
    status: params.status,
    steps: params.steps ? [...params.steps] : [],
    created_at: params.createdAt,
    updated_at: params.updatedAt ?? params.createdAt
  };
}

function materializeAssistantSegment(segment: DraftAssistantSegment): AssistantTranscriptItem {
  return {
    kind: 'assistant_segment',
    segment_id: segment.segment_id,
    session_id: segment.session_id,
    run_id: segment.run_id,
    segment_kind: segment.segment_kind,
    content: segment.content,
    status: segment.status,
    steps: segment.steps,
    created_at: segment.created_at,
    updated_at: segment.updated_at
  };
}

function createStep(eventId: string, createdAt: string, sessionUpdate: string, rawUpdate: Record<string, unknown>, summary: string, detail: string | null): TranscriptStep {
  return {
    step_id: eventId,
    summary,
    detail,
    created_at: createdAt,
    session_update: sessionUpdate,
    data: rawUpdate
  };
}

function stepFromUpdate(
  update: Record<string, unknown>,
  rawUpdate: Record<string, unknown>,
  event: RunStreamEventRow
): TranscriptStep | null {
  const sessionUpdate = readString(update, ['sessionUpdate', 'session_update']);
  if (!sessionUpdate) {
    return null;
  }

  if (
    sessionUpdate === 'agent_message_chunk' ||
    sessionUpdate === 'agent_thought_chunk' ||
    sessionUpdate === 'user_message_chunk' ||
    sessionUpdate === 'usage_update'
  ) {
    return null;
  }

  if (sessionUpdate === 'tool_call' || sessionUpdate === 'tool_call_update') {
    const status = readString(update, ['status']);
    const rawOutput = readField<unknown>(update, ['rawOutput', 'raw_output']);
    const detail = status && status.trim().length > 0 ? status : compactJson(rawOutput);
    const title = readString(update, ['title']) ?? 'Tool call';
    return createStep(event.event_id, event.created_at, sessionUpdate, rawUpdate, title, detail);
  }

  if (sessionUpdate === 'plan') {
    const entries = readArray(update, ['entries']) ?? [];
    const detail = entries
      .map((entry) => {
        const entryRecord = asRecord(entry);
        const content = readString(entryRecord, ['content']);
        return content ?? '';
      })
      .filter((item) => item.trim().length > 0)
      .join(', ');
    return createStep(event.event_id, event.created_at, sessionUpdate, rawUpdate, 'Plan updated', detail || null);
  }

  if (sessionUpdate === 'current_mode_update') {
    const currentModeId = readString(update, ['currentModeId', 'current_mode_id']);
    return createStep(event.event_id, event.created_at, sessionUpdate, rawUpdate, 'Mode updated', currentModeId ?? null);
  }

  if (sessionUpdate === 'available_commands_update') {
    const availableCommands = readArray(update, ['availableCommands', 'available_commands']) ?? [];
    return createStep(
      event.event_id,
      event.created_at,
      sessionUpdate,
      rawUpdate,
      'Commands updated',
      `${availableCommands.length} available`
    );
  }

  return createStep(
    event.event_id,
    event.created_at,
    sessionUpdate,
    rawUpdate,
    `Update: ${sessionUpdate}`,
    compactJson(rawUpdate)
  );
}

function visibleSegmentFromUpdate(update: Record<string, unknown>): { segmentKind: LiveSegmentKind; text: string } | null {
  const sessionUpdate = readString(update, ['sessionUpdate', 'session_update']);
  if (sessionUpdate === 'agent_message_chunk') {
    return { segmentKind: 'message', text: contentToText(readField<unknown>(update, ['content'])) };
  }
  if (sessionUpdate === 'agent_thought_chunk') {
    return { segmentKind: 'thought', text: contentToText(readField<unknown>(update, ['content'])) };
  }
  return null;
}

function ensureProjection(
  state: LiveRunProjection | null,
  runId: string,
  sessionId: string,
  status: LiveRunStatus = 'running'
): LiveRunProjection {
  if (!state) {
    return createProjection(runId, sessionId, status);
  }
  return state;
}

function flushActiveSegment(state: LiveRunProjection, finalStatus?: LiveRunStatus): LiveRunProjection {
  const activeSegment = state.activeSegment;
  if (!activeSegment) {
    return state;
  }
  if (activeSegment.content.trim().length === 0 && activeSegment.steps.length === 0) {
    return { ...state, activeSegment: null };
  }

  const nextItems = [
    ...state.items,
    materializeAssistantSegment({
      ...activeSegment,
      status: segmentStatus(finalStatus ?? state.status)
    })
  ];

  return {
    ...state,
    items: nextItems,
    activeSegment: null
  };
}

function flushPendingSteps(state: LiveRunProjection, finalStatus?: LiveRunStatus): LiveRunProjection {
  if (state.pendingSteps.length === 0) {
    return state;
  }

  const status = segmentStatus(finalStatus ?? state.status);
  const segment: AssistantTranscriptItem = {
    kind: 'assistant_segment',
    segment_id: `${state.runId}:segment:${state.nextSegmentIndex}`,
    session_id: state.sessionId,
    run_id: state.runId,
    segment_kind: 'steps',
    content: '',
    status,
    steps: [...state.pendingSteps],
    created_at: state.pendingSteps[0].created_at,
    updated_at: state.pendingSteps[state.pendingSteps.length - 1].created_at
  };

  return {
    ...state,
    items: [...state.items, segment],
    nextSegmentIndex: state.nextSegmentIndex + 1,
    pendingSteps: []
  };
}

function startSegment(
  state: LiveRunProjection,
  segmentKind: LiveSegmentKind,
  createdAt: string,
  initialSteps: TranscriptStep[]
): LiveRunProjection {
  const segment = createAssistantSegment({
    runId: state.runId,
    sessionId: state.sessionId,
    segmentId: `${state.runId}:segment:${state.nextSegmentIndex}`,
    segmentKind,
    steps: initialSteps,
    createdAt,
    status: 'streaming'
  });

  return {
    ...state,
    nextSegmentIndex: state.nextSegmentIndex + 1,
    activeSegment: segment,
    pendingSteps: []
  };
}

function appendStepToCurrentSegment(state: LiveRunProjection, step: TranscriptStep): LiveRunProjection {
  if (!state.activeSegment) {
    return { ...state, pendingSteps: [...state.pendingSteps, step] };
  }

  return {
    ...state,
    activeSegment: {
      ...state.activeSegment,
      steps: [...state.activeSegment.steps, step],
      updated_at: step.created_at
    }
  };
}

function updatePermissionItem(
  item: PermissionTranscriptItem,
  decision: PermissionDecision,
  optionId: string | null,
  decidedAt: string
): PermissionTranscriptItem {
  return {
    ...item,
    status: decision,
    decision,
    option_id: optionId,
    decided_at: decidedAt
  };
}

export function materializeLiveTranscriptItems(state: LiveRunProjection | null): Array<AssistantTranscriptItem | PermissionTranscriptItem> {
  if (!state) {
    return [];
  }
  return state.activeSegment ? [...state.items, materializeAssistantSegment(state.activeSegment)] : [...state.items];
}

export function applyLivePermissionDecision(
  state: LiveRunProjection | null,
  approvalId: string,
  decision: PermissionDecision,
  optionId?: string | null,
  decidedAt = new Date().toISOString()
): LiveRunProjection | null {
  if (!state) {
    return null;
  }

  const nextItems = state.items.map((item) => {
    if (item.kind !== 'permission_request' || item.approval_id !== approvalId) {
      return item;
    }
    return updatePermissionItem(item, decision, optionId ?? null, decidedAt);
  });

  const activeSegment = state.activeSegment;
  return {
    ...state,
    items: nextItems,
    activeSegment: activeSegment ? { ...activeSegment } : null
  };
}

export function applyRunStreamEvent(
  state: LiveRunProjection | null,
  event: RunStreamEventRow,
  sessionIdHint: string | null = null
): LiveRunProjection | null {
  const payload = asRecord(event.payload);
  const runId = readString(payload, ['run_id', 'runId']) ?? event.run_id;
  const sessionId =
    readString(payload, ['session_id', 'sessionId']) ??
    sessionIdHint ??
    state?.sessionId ??
    null;

  if (state && state.runId !== runId) {
    return state;
  }

  if (!sessionId) {
    return state;
  }

  const nextState = ensureProjection(state, runId, sessionId);

  switch (event.event_type) {
    case 'run.queued':
      return {
        ...nextState,
        status: 'queued'
      } as LiveRunProjection;
    case 'run.accepted':
      return {
        ...nextState,
        status: 'accepted'
      } as LiveRunProjection;
    case 'run.session_update': {
      const update = asRecord(readField<unknown>(payload, ['update']));
      if (!update) {
        return nextState;
      }

      const visibleSegment = visibleSegmentFromUpdate(update);
      const step = stepFromUpdate(update, update, event);
      let current: LiveRunProjection = {
        ...nextState,
        status: 'running'
      };

      if (visibleSegment === null) {
        if (step) {
          current = appendStepToCurrentSegment(current, step);
        }
        return current;
      }

      if (current.activeSegment && current.activeSegment.segment_kind !== visibleSegment.segmentKind) {
        current = flushActiveSegment(current);
      }

      if (!current.activeSegment) {
        current = startSegment(current, visibleSegment.segmentKind, event.created_at, current.pendingSteps);
      }

      const activeSegment = current.activeSegment;
      if (!activeSegment) {
        return current;
      }

      const nextSegment: DraftAssistantSegment = {
        ...activeSegment,
        content: `${activeSegment.content}${visibleSegment.text}`,
        updated_at: event.created_at
      };
      current = {
        ...current,
        activeSegment: nextSegment
      };

      if (step) {
        current = appendStepToCurrentSegment(current, step);
      }

      return current;
    }
    case 'run.permission.requested': {
      let current: LiveRunProjection = {
        ...nextState,
        status: 'running'
      };
      current = flushActiveSegment(current);
      current = flushPendingSteps(current);

      const approvalId = readString(payload, ['approval_id', 'approvalId']);
      if (!approvalId) {
        return current;
      }

      const alreadyExists = current.items.some(
        (item) => item.kind === 'permission_request' && item.approval_id === approvalId
      );
      if (alreadyExists) {
        return current;
      }

      const toolCall = readField<unknown>(payload, ['tool_call', 'toolCall']);
      const options = readArray(payload, ['options']) ?? [];

      const permissionItem: PermissionTranscriptItem = {
        kind: 'permission_request',
        approval_id: approvalId,
        run_id: runId,
        session_id: sessionId,
        status: 'pending',
        tool_call: asRecord(toolCall) ?? {},
        options: options.map((option) => asRecord(option) ?? {}),
        decision: null,
        option_id: null,
        created_at: readString(payload, ['created_at', 'createdAt']) ?? event.created_at,
        decided_at: null
      };

      const nextCurrent: LiveRunProjection = {
        ...current,
        items: [...current.items, permissionItem]
      };
      return nextCurrent;
    }
    case 'run.permission.decision': {
      const approvalId = readString(payload, ['approval_id', 'approvalId']);
      const decision = readString(payload, ['decision']) as PermissionDecision | undefined;
      if (!approvalId || (decision !== 'selected' && decision !== 'cancelled')) {
        return nextState;
      }

      const updated = applyLivePermissionDecision(
        nextState,
        approvalId,
        decision,
        readString(payload, ['option_id', 'optionId']) ?? null,
        readString(payload, ['created_at', 'createdAt']) ?? event.created_at
      );
      if (!updated) {
        return nextState;
      }
      const nextCurrent: LiveRunProjection = {
        ...updated,
        status: 'running'
      };
      return nextCurrent;
    }
    case 'run.completed': {
      const stopReason = readString(payload, ['stop_reason', 'stopReason']);
      const finalStatus: LiveRunStatus = stopReason === 'cancelled' ? 'cancelled' : 'completed';
      let current: LiveRunProjection = {
        ...nextState,
        status: finalStatus
      };
      current = flushActiveSegment(current, finalStatus);
      current = flushPendingSteps(current, finalStatus);
      const nextCurrent: LiveRunProjection = {
        ...current,
        items: current.items.map((item) => {
          if (item.kind !== 'assistant_segment') {
            return item;
          }
          return {
            ...item,
            status: finalStatus
          };
        })
      };
      return nextCurrent;
    }
    case 'run.error': {
      let current: LiveRunProjection = {
        ...nextState,
        status: 'error'
      };
      current = flushActiveSegment(current, 'error');
      current = flushPendingSteps(current, 'error');
      const nextCurrent: LiveRunProjection = {
        ...current,
        items: current.items.map((item) => {
          if (item.kind !== 'assistant_segment') {
            return item;
          }
          return {
            ...item,
            status: 'error'
          };
        })
      };
      return nextCurrent;
    }
    default:
      return nextState;
  }
}
