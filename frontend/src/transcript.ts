import { AssistantTranscriptItem, PermissionTranscriptItem, TranscriptItem, UserTranscriptItem } from './api';

// Acceptance target for transcript rendering:
// - one prompt turn can materialize multiple assistant bubbles
// - permission requests render as standalone cards between assistant segments
// - step-only assistant transcript items stay visible after run completion

export function isUserTranscriptItem(item: TranscriptItem): item is UserTranscriptItem {
  return item.kind === 'user_message';
}

export function isAssistantTranscriptItem(item: TranscriptItem): item is AssistantTranscriptItem {
  return item.kind === 'assistant_segment';
}

export function isPermissionTranscriptItem(item: TranscriptItem): item is PermissionTranscriptItem {
  return item.kind === 'permission_request';
}
