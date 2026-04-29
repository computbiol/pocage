import { ChangeEvent, FormEvent, KeyboardEvent, RefObject, useRef, useState } from 'react';
import Markdown from 'react-markdown';
import {
  AgentInstance,
  ContextCandidate,
  PairingCodeResponse,
  PermissionDecision,
  PermissionRequest,
  PromptImageItem,
  PromptItem,
  PromptResourceLinkItem,
  Session,
  TranscriptItem,
  controlPlaneOrigin
} from '../api';
import {
  isAssistantTranscriptItem,
  isPermissionTranscriptItem,
  isUserTranscriptItem
} from '../transcript';
import { formatTime } from '../utils';

type PermissionOption = {
  option_id: string;
  label: string;
  decision: PermissionDecision;
};

type ChatAreaProps = {
  isDraftSession: boolean;
  activeSession: Session | null;
  activeRunId: string | null;
  loadingMessages: boolean;
  transcriptItems: TranscriptItem[];
  pendingPermissionDecisions: Record<string, boolean>;
  canCompose: boolean;
  canSend: boolean;
  agentInstances: AgentInstance[];
  loadingAgentInstances: boolean;
  draftAgentInstance: AgentInstance | null;
  draftAgentInstanceId: string | null;
  draftWorkspace: string;
  defaultAgentInstanceId: string | null;
  pairingCode: PairingCodeResponse | null;
  pairingBusy: boolean;
  composer: string;
  composerContextRefs: PromptResourceLinkItem[];
  composerImages: PromptImageItem[];
  mentionOpen: boolean;
  mentionLoading: boolean;
  mentionQuery: string;
  mentionResults: ContextCandidate[];
  highlightedMentionIndex: number;
  sending: boolean;
  errorText: string | null;
  messageListRef: RefObject<HTMLDivElement | null>;
  composerRef: RefObject<HTMLTextAreaElement | null>;
  onCancel: () => Promise<void>;
  onCreatePairingCode: () => Promise<void>;
  onRefreshAgentInstances: () => Promise<unknown>;
  onPermissionDecision: (
    request: PermissionRequest,
    decision: PermissionDecision,
    optionId?: string
  ) => Promise<void>;
  onSubmit: (event: FormEvent<HTMLFormElement>) => Promise<void>;
  onDraftAgentInstanceChange: (agentInstanceId: string) => void;
  onDraftWorkspaceChange: (workspace: string) => void;
  onSetDefaultAgentInstance: () => void;
  onComposerChange: (value: string, selectionStart: number | null) => void;
  onComposerKeyDown: (event: KeyboardEvent<HTMLTextAreaElement>) => void;
  onComposerSelectionChange: (selectionStart: number | null) => void;
  onSelectContextCandidate: (candidate: ContextCandidate) => void;
  onRemoveContextRef: (uri: string) => void;
  onImageFilesSelected: (files: FileList | null) => Promise<void>;
  onRemoveImage: (imageUrl: string) => void;
  sessionTitle: (session: Session) => string;
};

function asString(value: unknown): string | null {
  return typeof value === 'string' && value.trim().length > 0 ? value.trim() : null;
}

function formatAgentLabel(agent: string): string {
  if (agent === 'codex') {
    return 'Codex';
  }
  return agent.trim().length > 0 ? agent : 'Unknown agent';
}

function machineDisplayText(agentInstance: AgentInstance): string {
  return (
    agentInstance.display_name?.trim() ||
    agentInstance.hostname?.trim() ||
    agentInstance.executor_name?.trim() ||
    'Machine'
  );
}

function agentInstanceDisplayText(agentInstance: AgentInstance): string {
  return `${machineDisplayText(agentInstance)} · ${formatAgentLabel(agentInstance.agent)}`;
}

function agentInstanceSummaryText(agentInstance: AgentInstance): string {
  return `${agentInstanceDisplayText(agentInstance)} · ${agentInstance.status}`;
}

function agentInstanceRootsText(agentInstance: AgentInstance | null): string {
  if (!agentInstance || agentInstance.workspace_roots.length === 0) {
    return 'No advertised workspace roots.';
  }
  return agentInstance.workspace_roots.join(' · ');
}

function buildPairingCommand(pairingCode: PairingCodeResponse | null): string {
  const agent = pairingCode?.agent ?? 'codex';
  const code = pairingCode?.pairing_code ?? '<PAIRING_CODE>';
  return `uv run pocage pair --agent ${agent} --api-url ${controlPlaneOrigin()} --pairing-code ${code}`;
}

function buildDaemonCommand(pairingCode: PairingCodeResponse | null): string {
  const agent = pairingCode?.agent ?? 'codex';
  return `uv run pocage --agent ${agent}`;
}

function toolCallTitle(toolCall: Record<string, unknown>): string {
  return (
    asString(toolCall.title) ??
    asString(toolCall.name) ??
    asString(toolCall.tool) ??
    asString(toolCall.method) ??
    'Permission required'
  );
}

function toolCallDetail(toolCall: Record<string, unknown>): string | null {
  const value = toolCall.command ?? toolCall.cmd ?? toolCall.input ?? toolCall.raw_input;
  if (typeof value === 'string') {
    return value;
  }
  if (value && typeof value === 'object') {
    try {
      return JSON.stringify(value);
    } catch {
      return null;
    }
  }
  return null;
}

function toPermissionOptions(options: Array<Record<string, unknown>>): PermissionOption[] {
  const mapped: PermissionOption[] = [];

  for (const option of options) {
    const optionId = asString(option.option_id) ?? asString(option.optionId) ?? asString(option.id);
    if (!optionId) {
      continue;
    }

    const kind = (asString(option.kind) ?? '').toLowerCase();
    const label =
      asString(option.label) ??
      asString(option.title) ??
      asString(option.name) ??
      asString(option.description) ??
      optionId;
    const decision: PermissionDecision =
      kind.includes('reject') || kind.includes('deny') || kind.includes('cancelled')
        ? 'cancelled'
        : 'selected';

    mapped.push({
      option_id: optionId,
      label,
      decision
    });
  }

  return mapped;
}

function getMessageContextRefs(items: PromptItem[]): PromptResourceLinkItem[] {
  return items.filter((item): item is PromptResourceLinkItem => item.type === 'resource_link');
}

function getMessageImages(items: PromptItem[]): PromptImageItem[] {
  return items.filter((item): item is PromptImageItem => item.type === 'image');
}

function ContextChip({
  item,
  removable,
  onRemove
}: {
  item: PromptResourceLinkItem;
  removable?: boolean;
  onRemove?: (uri: string) => void;
}) {
  return (
    <span className={`context-chip${item.kind === 'directory' ? ' directory' : ''}`}>
      <span className="context-chip-prefix" aria-hidden="true">
        {item.kind === 'directory' ? '↳' : '@'}
      </span>
      <span className="context-chip-label">{item.relative_path}</span>
      {removable ? (
        <button
          type="button"
          className="context-chip-remove"
          onClick={() => onRemove?.(item.uri)}
          aria-label={`Remove ${item.relative_path}`}
        >
          ×
        </button>
      ) : null}
    </span>
  );
}

function ImageGrid({
  items,
  removable,
  onRemove
}: {
  items: PromptImageItem[];
  removable?: boolean;
  onRemove?: (imageUrl: string) => void;
}) {
  if (items.length === 0) {
    return null;
  }

  return (
    <div className="image-grid">
      {items.map((item, index) => (
        <figure key={`${item.image_url.slice(0, 48)}-${index}`} className="image-card">
          <img src={item.image_url} alt={item.name ?? 'Selected image'} />
          {item.name ? <figcaption>{item.name}</figcaption> : null}
          {removable ? (
            <button
              type="button"
              className="image-remove"
              onClick={() => onRemove?.(item.image_url)}
              aria-label={`Remove ${item.name ?? 'image'}`}
            >
              ×
            </button>
          ) : null}
        </figure>
      ))}
    </div>
  );
}

export function ChatArea({
  isDraftSession,
  activeSession,
  activeRunId,
  loadingMessages,
  transcriptItems,
  pendingPermissionDecisions,
  canCompose,
  canSend,
  agentInstances,
  loadingAgentInstances,
  draftAgentInstance,
  draftAgentInstanceId,
  draftWorkspace,
  defaultAgentInstanceId,
  pairingCode,
  pairingBusy,
  composer,
  composerContextRefs,
  composerImages,
  mentionOpen,
  mentionLoading,
  mentionQuery,
  mentionResults,
  highlightedMentionIndex,
  sending,
  errorText,
  messageListRef,
  composerRef,
  onCancel,
  onCreatePairingCode,
  onRefreshAgentInstances,
  onPermissionDecision,
  onSubmit,
  onDraftAgentInstanceChange,
  onDraftWorkspaceChange,
  onSetDefaultAgentInstance,
  onComposerChange,
  onComposerKeyDown,
  onComposerSelectionChange,
  onSelectContextCandidate,
  onRemoveContextRef,
  onImageFilesSelected,
  onRemoveImage,
  sessionTitle
}: ChatAreaProps) {
  const sortedTranscriptItems = [...transcriptItems].sort((a, b) => a.created_at.localeCompare(b.created_at));
  const hasPendingPermission = sortedTranscriptItems.some(
    (item) => isPermissionTranscriptItem(item) && item.status === 'pending'
  );
  const hasStreamingAssistant = sortedTranscriptItems.some(
    (item) => isAssistantTranscriptItem(item) && item.status === 'streaming'
  );
  const showPendingAssistant = sending && !loadingMessages && !hasStreamingAssistant && !hasPendingPermission;
  const showLoadingAssistant = loadingMessages && !sending;
  const pendingTime = formatTime(new Date().toISOString());
  const [expandedRunSteps, setExpandedRunSteps] = useState<Record<string, boolean>>({});
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const activeAgentInstance = activeSession?.agent_instance_id
    ? agentInstances.find((agentInstance) => agentInstance.id === activeSession.agent_instance_id) ?? null
    : null;
  const assistantName = formatAgentLabel(activeAgentInstance?.agent ?? draftAgentInstance?.agent ?? 'codex');
  const onlineAgentInstances = agentInstances.filter((agentInstance) => agentInstance.status === 'online');
  const showPairingOnboarding = isDraftSession && !loadingAgentInstances && agentInstances.length === 0;
  const showDaemonOnboarding =
    isDraftSession && !loadingAgentInstances && agentInstances.length > 0 && onlineAgentInstances.length === 0;
  const showDraftMachineGrid = isDraftSession && !showPairingOnboarding && !showDaemonOnboarding;

  function toggleRunSteps(segmentId: string): void {
    setExpandedRunSteps((prev) => ({ ...prev, [segmentId]: !prev[segmentId] }));
  }

  async function handleFileInputChange(event: ChangeEvent<HTMLInputElement>): Promise<void> {
    const input = event.currentTarget;
    const { files } = input;
    await onImageFilesSelected(files);
    input.value = '';
  }

  const emptyStateTitle = isDraftSession
    ? canCompose
      ? 'Start the conversation'
      : loadingAgentInstances
        ? 'Loading machines'
        : 'Choose a machine first'
    : canCompose
      ? 'Start the conversation'
      : 'Select a session';

  const composerPlaceholder = canCompose
    ? 'Type anything...\nEnter to send, Shift+Enter for newline'
    : isDraftSession
      ? loadingAgentInstances
        ? 'Loading machines...'
        : 'Choose a machine and workspace to start a new session'
      : 'Click New session and send your first message';

  const workspaceContext = isDraftSession
    ? draftAgentInstance
      ? `${agentInstanceDisplayText(draftAgentInstance)} · ${draftWorkspace || 'Set a workspace'}`
      : 'Choose a machine and workspace'
    : activeSession
      ? [
          activeAgentInstance
            ? agentInstanceDisplayText(activeAgentInstance)
            : 'Machine unavailable',
          activeSession.cwd
        ]
          .filter(Boolean)
          .join(' · ')
      : null;

  return (
    <section className="workspace">
      <header className="workspace-header">
        <div>
          <p className="workspace-eyebrow">{isDraftSession ? 'Draft' : 'Conversation'}</p>
          <h2 className="workspace-title">
            {isDraftSession ? 'New Session' : activeSession ? sessionTitle(activeSession) : 'New Session'}
          </h2>
          {workspaceContext ? <p className="workspace-context">{workspaceContext}</p> : null}
        </div>
        {activeRunId ? (
          <button className="ghost-button" type="button" onClick={() => void onCancel()}>
            Stop
          </button>
        ) : null}
      </header>

      <div className="message-scroll" ref={messageListRef}>
        <div className="message-list">
          {isDraftSession ? (
            <section className="draft-setup-card">
              <div className="draft-setup-head">
                <div>
                  <p className="draft-setup-eyebrow">Session Setup</p>
                  <h3 className="draft-setup-title">
                    {showPairingOnboarding
                      ? 'Pair your first machine'
                      : showDaemonOnboarding
                        ? 'Bring a paired machine online'
                        : 'Choose a machine and workspace'}
                  </h3>
                </div>
                <button
                  type="button"
                  className="draft-default-button"
                  disabled={!draftAgentInstanceId || draftAgentInstanceId === defaultAgentInstanceId}
                  onClick={onSetDefaultAgentInstance}
                >
                  {draftAgentInstanceId && draftAgentInstanceId === defaultAgentInstanceId
                    ? 'Default machine'
                    : 'Set as default'}
                </button>
              </div>

              {showPairingOnboarding ? (
                <div className="draft-onboarding-card">
                  <p className="draft-onboarding-copy">
                    Generate a pairing code here or from Machines in the user menu, run the pairing command on your
                    Linux or macOS machine, then start the daemon so pocage can route sessions to it.
                  </p>
                  <ol className="draft-onboarding-steps">
                    <li>Generate a one-time pairing code.</li>
                    <li>
                      <code className="draft-command">{buildPairingCommand(pairingCode)}</code>
                    </li>
                    <li>
                      <code className="draft-command">{buildDaemonCommand(pairingCode)}</code>
                    </li>
                    <li>Return here and wait a moment while pocage refreshes the machine list automatically.</li>
                  </ol>
                  <div className="draft-onboarding-actions">
                    {!pairingCode ? (
                      <button
                        type="button"
                        className="sidebar-primary-button"
                        disabled={pairingBusy}
                        onClick={() => void onCreatePairingCode()}
                      >
                        {pairingBusy ? 'Generating…' : 'Generate pairing code'}
                      </button>
                    ) : null}
                    <button type="button" className="draft-inline-button" onClick={() => void onRefreshAgentInstances()}>
                      Refresh now
                    </button>
                  </div>
                </div>
              ) : null}

              {showDaemonOnboarding ? (
                <div className="draft-onboarding-card">
                  <p className="draft-onboarding-copy">
                    Your machines are paired, but none of them are online right now. Start the daemon on one of them,
                    then wait a moment for this view to refresh automatically.
                  </p>
                  <div className="draft-machine-tags">
                    {agentInstances.map((agentInstance) => (
                      <span key={agentInstance.id} className="draft-machine-tag">
                        {agentInstanceDisplayText(agentInstance)}
                      </span>
                    ))}
                  </div>
                  <code className="draft-command">{buildDaemonCommand(pairingCode)}</code>
                  <div className="draft-onboarding-actions">
                    <button type="button" className="draft-inline-button" onClick={() => void onRefreshAgentInstances()}>
                      Refresh now
                    </button>
                  </div>
                </div>
              ) : null}

              {showDraftMachineGrid ? (
                <div className="draft-setup-grid">
                  <label className="draft-field">
                    <span className="draft-field-label">Machine</span>
                    <select
                      className="draft-select"
                      value={draftAgentInstanceId ?? ''}
                      onChange={(event) => onDraftAgentInstanceChange(event.currentTarget.value)}
                      disabled={loadingAgentInstances || agentInstances.length === 0}
                    >
                      <option value="">
                        {loadingAgentInstances
                          ? 'Loading machines…'
                          : agentInstances.length === 0
                            ? 'No machine available'
                            : 'Select a machine'}
                      </option>
                      {agentInstances.map((agentInstance) => (
                        <option key={agentInstance.id} value={agentInstance.id} disabled={agentInstance.status !== 'online'}>
                          {agentInstanceSummaryText(agentInstance)}
                        </option>
                      ))}
                    </select>
                    <span className="draft-field-hint">
                      {draftAgentInstance
                        ? agentInstanceRootsText(draftAgentInstance)
                        : loadingAgentInstances
                          ? 'Looking for connected machines…'
                          : 'Connect a machine to begin.'}
                    </span>
                  </label>

                  <label className="draft-field">
                    <span className="draft-field-label">Workspace</span>
                    <input
                      className="draft-input"
                      type="text"
                      value={draftWorkspace}
                      placeholder="~/.pocage/workspaces/ws-2026-03-16T14-33-06"
                      onChange={(event) => onDraftWorkspaceChange(event.currentTarget.value)}
                    />
                    <span className="draft-field-hint">
                      {draftAgentInstance
                        ? 'The first message creates or reuses this remote working directory.'
                        : 'Machine selection unlocks remote workspace creation.'}
                    </span>
                  </label>
                </div>
              ) : null}
            </section>
          ) : null}

          {showLoadingAssistant ? (
            <div className="bubble assistant loading-pill">
              <div className="bubble-head">
                <span>{assistantName}</span>
                <span className="typing-dots" aria-hidden="true">
                  <span />
                  <span />
                  <span />
                </span>
              </div>
            </div>
          ) : null}

          {!isDraftSession &&
          !loadingMessages &&
          sortedTranscriptItems.length === 0 &&
          !showPendingAssistant &&
          !showLoadingAssistant ? (
            <div className="empty-state">
              <h3>{emptyStateTitle}</h3>
            </div>
          ) : null}

          {sortedTranscriptItems.map((item) => {
            if (isUserTranscriptItem(item)) {
              const contextRefs = getMessageContextRefs(item.items);
              const images = getMessageImages(item.items);
              return (
                <article key={item.message_id} className="bubble user">
                  <div className="bubble-head">
                    <span>You</span>
                    <time dateTime={item.updated_at}>{formatTime(item.updated_at)}</time>
                  </div>

                  {item.content ? <p className="message-plain">{item.content}</p> : null}
                  {contextRefs.length > 0 ? (
                    <div className="message-item-row">
                      {contextRefs.map((contextItem) => (
                        <ContextChip key={contextItem.uri} item={contextItem} />
                      ))}
                    </div>
                  ) : null}
                  <ImageGrid items={images} />
                </article>
              );
            }

            if (isPermissionTranscriptItem(item)) {
              const detail = toolCallDetail(item.tool_call);
              const options = toPermissionOptions(item.options);
              const isPending = item.status === 'pending';
              const waitingDecision = Boolean(pendingPermissionDecisions[item.approval_id]);

              return (
                <article key={item.approval_id} className="bubble assistant permission-bubble" aria-live="polite">
                  <div className="bubble-head">
                    <span>{assistantName}</span>
                    <time dateTime={item.created_at}>{formatTime(item.created_at)}</time>
                  </div>
                  <div className="permission-card">
                    <p className="permission-title">{toolCallTitle(item.tool_call)}</p>
                    {detail ? <p className="permission-detail">{detail}</p> : null}
                    {options.length > 0 ? (
                      <div className="permission-options">
                        {options.map((option) => {
                          const isSelected =
                            item.option_id === option.option_id ||
                            (item.status === 'cancelled' &&
                              option.decision === 'cancelled' &&
                              !item.option_id);
                          const isDisabled = !isPending || waitingDecision;
                          const buttonClass = isSelected
                            ? option.decision === 'selected'
                              ? 'permission-option selected'
                              : 'permission-option cancelled'
                            : 'permission-option';

                          return (
                            <button
                              key={`${item.approval_id}-${option.option_id}`}
                              type="button"
                              disabled={isDisabled}
                              className={buttonClass}
                              onClick={() =>
                                void onPermissionDecision(
                                  item,
                                  option.decision,
                                  option.decision === 'selected' ? option.option_id : undefined
                                )
                              }
                            >
                              {option.label}
                            </button>
                          );
                        })}
                      </div>
                    ) : null}
                    {isPending && waitingDecision ? (
                      <p className="permission-meta">Submitting your choice...</p>
                    ) : item.status === 'selected' ? (
                      <p className="permission-meta">
                        Approved{item.decided_at ? ` · ${formatTime(item.decided_at)}` : ''}
                      </p>
                    ) : item.status === 'cancelled' ? (
                      <p className="permission-meta">
                        Rejected{item.decided_at ? ` · ${formatTime(item.decided_at)}` : ''}
                      </p>
                    ) : null}
                  </div>
                </article>
              );
            }

            if (!isAssistantTranscriptItem(item)) {
              return null;
            }

            const hasRunSteps = item.steps.length > 0;
            const stepsExpanded = Boolean(expandedRunSteps[item.segment_id]);
            const bubbleClass =
              item.segment_kind === 'thought' ? 'bubble assistant assistant-thought' : 'bubble assistant';

            return (
              <article key={item.segment_id} className={bubbleClass}>
                <div className="bubble-head">
                  <span>{item.segment_kind === 'thought' ? `${assistantName} · Thinking` : assistantName}</span>
                  <time dateTime={item.updated_at}>{formatTime(item.updated_at)}</time>
                </div>

                {hasRunSteps ? (
                  <div className="steps-panel">
                    <button
                      type="button"
                      className="steps-toggle"
                      onClick={() => toggleRunSteps(item.segment_id)}
                      aria-expanded={stepsExpanded}
                    >
                      <span className="steps-toggle-dot" aria-hidden="true">
                        •
                      </span>
                      <span>View Steps</span>
                      <span className={`steps-toggle-chevron${stepsExpanded ? ' open' : ''}`} aria-hidden="true">
                        ›
                      </span>
                    </button>

                    {stepsExpanded ? (
                      <ol className="steps-list">
                        {item.steps.map((step) => (
                          <li key={step.step_id} className="steps-item">
                            <div className="steps-item-head">
                              <span className="steps-item-summary">{step.summary}</span>
                              <time dateTime={step.created_at}>{formatTime(step.created_at)}</time>
                            </div>
                            {step.detail ? <p className="steps-item-detail">{step.detail}</p> : null}
                          </li>
                        ))}
                      </ol>
                    ) : null}
                  </div>
                ) : null}

                {item.content.trim().length > 0 ? (
                  <div className="message-markdown">
                    <Markdown>{item.content}</Markdown>
                  </div>
                ) : item.status === 'streaming' ? (
                  <div className="assistant-waiting-body">
                    <span className="typing-dots" aria-hidden="true">
                      <span />
                      <span />
                      <span />
                    </span>
                  </div>
                ) : item.status === 'error' ? (
                  <p className="assistant-fallback">Run failed before any response content was returned.</p>
                ) : item.status === 'cancelled' ? (
                  <p className="assistant-fallback">Run stopped.</p>
                ) : null}
              </article>
            );
          })}

          {showPendingAssistant ? (
            <article className="bubble assistant loading-pill assistant-waiting" aria-live="polite">
              <div className="bubble-head">
                <span>{assistantName}</span>
                <time>{pendingTime}</time>
              </div>
              <div className="assistant-waiting-body">
                <span className="typing-dots" aria-hidden="true">
                  <span />
                  <span />
                  <span />
                </span>
              </div>
            </article>
          ) : null}
        </div>
      </div>

      {errorText ? <p className="error-banner">{errorText}</p> : null}

      <form className="composer" onSubmit={(event) => void onSubmit(event)}>
        <div className="composer-shell">
          {composerContextRefs.length > 0 ? (
            <div className="composer-chip-row">
              {composerContextRefs.map((item) => (
                <ContextChip key={item.uri} item={item} removable onRemove={onRemoveContextRef} />
              ))}
            </div>
          ) : null}

          <ImageGrid items={composerImages} removable onRemove={onRemoveImage} />

          <textarea
            ref={composerRef}
            placeholder={composerPlaceholder}
            value={composer}
            onChange={(event) => onComposerChange(event.target.value, event.target.selectionStart)}
            onKeyDown={onComposerKeyDown}
            onSelect={(event) => onComposerSelectionChange(event.currentTarget.selectionStart)}
            onClick={(event) => onComposerSelectionChange(event.currentTarget.selectionStart)}
            onKeyUp={(event) => onComposerSelectionChange(event.currentTarget.selectionStart)}
            disabled={!canCompose || sending}
          />

          {mentionOpen ? (
            <div className="mention-menu" role="listbox" aria-label="Context search results">
              {mentionLoading ? <p className="mention-empty">Searching…</p> : null}
              {!mentionLoading && mentionQuery.trim().length === 0 ? (
                <p className="mention-empty">Type after @ to search files and folders.</p>
              ) : null}
              {!mentionLoading && mentionQuery.trim().length > 0 && mentionResults.length === 0 ? (
                <p className="mention-empty">No matching files or folders.</p>
              ) : null}
              {!mentionLoading && mentionResults.length > 0 ? (
                <div className="mention-options">
                  {mentionResults.map((item, index) => (
                    <button
                      key={item.uri}
                      type="button"
                      className={`mention-option${index === highlightedMentionIndex ? ' active' : ''}`}
                      onMouseDown={(event) => event.preventDefault()}
                      onClick={() => onSelectContextCandidate(item)}
                    >
                      <span className="mention-option-head">
                        <span className="mention-option-name">{item.name}</span>
                        <span className="mention-option-kind">{item.kind}</span>
                      </span>
                      <span className="mention-option-path">{item.relative_path}</span>
                    </button>
                  ))}
                </div>
              ) : null}
            </div>
          ) : null}

          <div className="composer-actions">
            <input
              ref={fileInputRef}
              className="hidden-file-input"
              type="file"
              accept="image/*"
              multiple
              onChange={(event) => void handleFileInputChange(event)}
            />
            <button
              className="attach-button"
              type="button"
              aria-label="Attach images"
              disabled={!canCompose || sending}
              onClick={() => fileInputRef.current?.click()}
            >
              +
            </button>
            <button className="send-button-circle" type="submit" disabled={!canSend}>
              ↑
            </button>
          </div>
        </div>
      </form>
    </section>
  );
}
