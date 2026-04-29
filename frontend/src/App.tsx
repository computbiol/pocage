import { FormEvent, KeyboardEvent, useEffect, useMemo, useRef, useState } from 'react';
import {
  AgentInstance,
  ApiError,
  AuthUser,
  ContextCandidate,
  PermissionDecision,
  PermissionRequest,
  PairingCodeResponse,
  PromptImageItem,
  PromptItem,
  PromptResourceLinkItem,
  Session,
  SessionStatus,
  TranscriptItem,
  cancelRun,
  changePassword,
  clearCachedAuthState,
  createPairingCode,
  createRunEventSource,
  createSession,
  decideRunPermission,
  ensureCsrfToken,
  getSessionTranscript,
  listAgentInstances,
  listSessions,
  login,
  logout,
  restoreAuthSession,
  requestPasswordReset,
  requestVerifyToken,
  registerUser,
  resetPassword,
  searchContext,
  sendMessage,
  updateCurrentUserProfile,
  verifyEmail
} from './api';
import { ChatArea } from './components/ChatArea';
import { MarketingHome } from './components/MarketingHome';
import { MachinesView } from './components/MachinesView';
import { SiteFooter } from './components/SiteFooter';
import { SettingsView, type SettingsSection } from './components/SettingsView';
import {
  applyLivePermissionDecision,
  applyRunStreamEvent,
  materializeLiveTranscriptItems,
  type LiveRunProjection,
  type RunStreamEventRow
} from './liveTranscript';
import pocageLogo from './assets/brand/pocage-logo.svg';
import { Sidebar } from './components/Sidebar';
import { buildSessionPath, parseSessionRoute } from './sessionRoute';
import { sessionTitle } from './utils';

const MAX_IMAGE_SIZE_BYTES = 5 * 1024 * 1024;
const MAX_TOTAL_IMAGE_BYTES = 15 * 1024 * 1024;
const MENTION_SEARCH_DEBOUNCE_MS = 140;
const DEFAULT_AGENT_INSTANCE_STORAGE_KEY = 'pocage.defaultAgentInstanceId';
const SIDEBAR_COLLAPSED_STORAGE_KEY = 'pocage.sidebarCollapsed';
const DEFAULT_WORKSPACE_ROOT = '~/.pocage/workspaces';
const DEFAULT_PAIRING_AGENT = 'codex';
const MIN_AUTH_PASSWORD_LENGTH = 8;
const USER_NOT_VERIFIED_ERROR_CODE = 'USER_NOT_VERIFIED';
const ACTIVE_MACHINE_REFRESH_INTERVAL_MS = 5_000;
const WAITING_MACHINE_REFRESH_INTERVAL_MS = 3_000;
const DRAFT_MACHINE_REFRESH_INTERVAL_MS = 30_000;
const ACTIVE_RUN_RECONCILE_INTERVAL_MS = 3_000;
const STALE_RUN_STREAM_THRESHOLD_MS = 4_000;
type AuthScreenMode = 'login' | 'register' | 'forgot-password' | 'reset-password' | 'verify' | 'not-found';
type NavigableAuthMode = Exclude<AuthScreenMode, 'not-found'>;
type RefreshAgentInventoryOptions = {
  showLoading?: boolean;
  surfaceError?: boolean;
  clearOnError?: boolean;
  syncSessionsOnChange?: boolean;
};

const AUTH_ROUTE_PATHS: Record<NavigableAuthMode, string> = {
  login: '/sign-in',
  register: '/sign-up',
  'forgot-password': '/forgot-password',
  'reset-password': '/reset-password',
  verify: '/verify'
};

type WorkspacePaneMode = 'chat' | 'machines' | 'settings';

type AuthHistoryState = {
  allowVerifyRequest?: boolean;
  returnTo?: string;
};

type AuthRoute = {
  mode: AuthScreenMode;
  token: string | null;
  normalize: boolean;
  showHome: boolean;
  returnTo: string | null;
};

type MentionMatch = {
  start: number;
  end: number;
  query: string;
};

function findActiveRunId(items: TranscriptItem[]): string | null {
  const pendingPermission = [...items]
    .reverse()
    .find((item) => item.kind === 'permission_request' && item.status === 'pending');
  if (pendingPermission) {
    return pendingPermission.run_id;
  }
  const streamingAssistant = [...items]
    .reverse()
    .find((item) => item.kind === 'assistant_segment' && item.status === 'streaming');
  return streamingAssistant?.run_id ?? null;
}

function isSessionRunActive(status: SessionStatus): boolean {
  return status === 'queued' || status === 'assigned' || status === 'running';
}

function deriveActiveRunId(session: Session, items: TranscriptItem[], fallbackRunId: string | null): string | null {
  const activeRunId = findActiveRunId(items);
  if (activeRunId) {
    return activeRunId;
  }
  if (fallbackRunId && isSessionRunActive(session.status)) {
    return fallbackRunId;
  }
  return null;
}

function parseRunEventRow(data: string): RunStreamEventRow | null {
  try {
    const parsed = JSON.parse(data) as Partial<RunStreamEventRow> & { payload?: unknown };
    if (
      typeof parsed.event_id !== 'string' ||
      typeof parsed.run_id !== 'string' ||
      typeof parsed.event_type !== 'string' ||
      typeof parsed.seq !== 'number' ||
      typeof parsed.created_at !== 'string' ||
      !parsed.payload ||
      typeof parsed.payload !== 'object' ||
      Array.isArray(parsed.payload)
    ) {
      return null;
    }

    return {
      event_id: parsed.event_id,
      run_id: parsed.run_id,
      seq: parsed.seq,
      event_type: parsed.event_type,
      payload: parsed.payload as Record<string, unknown>,
      created_at: parsed.created_at
    };
  } catch {
    return null;
  }
}

function totalImageBytes(images: PromptImageItem[]): number {
  return images.reduce((sum, image) => sum + (image.size ?? 0), 0);
}

function findMentionMatch(value: string, caret: number | null): MentionMatch | null {
  if (caret === null || caret < 1) {
    return null;
  }

  let cursor = caret - 1;
  while (cursor >= 0) {
    const char = value[cursor];
    if (char === '@') {
      if (cursor > 0) {
        const previous = value[cursor - 1];
        if (!/\s/.test(previous)) {
          return null;
        }
      }

      const query = value.slice(cursor + 1, caret);
      if (/\s/.test(query)) {
        return null;
      }
      return {
        start: cursor,
        end: caret,
        query
      };
    }
    if (/\s/.test(char)) {
      return null;
    }
    cursor -= 1;
  }

  return null;
}

async function readFileAsDataUrl(file: File): Promise<string> {
  return await new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      if (typeof reader.result === 'string') {
        resolve(reader.result);
      } else {
        reject(new Error(`Failed to read ${file.name}`));
      }
    };
    reader.onerror = () => reject(reader.error ?? new Error(`Failed to read ${file.name}`));
    reader.readAsDataURL(file);
  });
}

function readStoredDefaultAgentInstanceId(): string | null {
  if (typeof window === 'undefined') {
    return null;
  }
  try {
    const value = window.localStorage.getItem(DEFAULT_AGENT_INSTANCE_STORAGE_KEY);
    return value && value.trim().length > 0 ? value : null;
  } catch {
    return null;
  }
}

function storeDefaultAgentInstanceId(agentInstanceId: string | null): void {
  if (typeof window === 'undefined') {
    return;
  }
  try {
    if (agentInstanceId) {
      window.localStorage.setItem(DEFAULT_AGENT_INSTANCE_STORAGE_KEY, agentInstanceId);
    } else {
      window.localStorage.removeItem(DEFAULT_AGENT_INSTANCE_STORAGE_KEY);
    }
  } catch {
    // Ignore storage write failures in private mode or locked-down environments.
  }
}

function readStoredSidebarCollapsed(): boolean {
  if (typeof window === 'undefined') {
    return false;
  }
  try {
    return window.localStorage.getItem(SIDEBAR_COLLAPSED_STORAGE_KEY) === '1';
  } catch {
    return false;
  }
}

function storeSidebarCollapsed(collapsed: boolean): void {
  if (typeof window === 'undefined') {
    return;
  }
  try {
    if (collapsed) {
      window.localStorage.setItem(SIDEBAR_COLLAPSED_STORAGE_KEY, '1');
    } else {
      window.localStorage.removeItem(SIDEBAR_COLLAPSED_STORAGE_KEY);
    }
  } catch {
    // Ignore storage write failures in private mode or locked-down environments.
  }
}

function padNumber(value: number, width = 2): string {
  return value.toString().padStart(width, '0');
}

function formatWorkspaceTimestamp(value: Date): string {
  return [
    value.getFullYear().toString(),
    '-',
    padNumber(value.getMonth() + 1),
    '-',
    padNumber(value.getDate()),
    'T',
    padNumber(value.getHours()),
    '-',
    padNumber(value.getMinutes()),
    '-',
    padNumber(value.getSeconds())
  ].join('');
}

function normalizeWorkspaceRoot(root?: string | null): string {
  const trimmed = root?.trim() || DEFAULT_WORKSPACE_ROOT;
  if (trimmed === '/' || trimmed === '~') {
    return trimmed;
  }
  return trimmed.replace(/[\\/]+$/, '');
}

function buildWorkspacePath(root?: string | null): string {
  const normalizedRoot = normalizeWorkspaceRoot(root);
  const suffix = `ws-${formatWorkspaceTimestamp(new Date())}`;
  if (normalizedRoot === '/') {
    return `/${suffix}`;
  }
  if (normalizedRoot === '~') {
    return `~/${suffix}`;
  }
  return `${normalizedRoot}/${suffix}`;
}

function findAgentInstance(
  agentInstances: AgentInstance[],
  agentInstanceId: string | null | undefined
): AgentInstance | null {
  if (!agentInstanceId) {
    return null;
  }
  return agentInstances.find((agentInstance) => agentInstance.id === agentInstanceId) ?? null;
}

function pickDraftAgentInstance(agentInstances: AgentInstance[], preferredId: string | null): AgentInstance | null {
  const onlineInstances = agentInstances.filter((agentInstance) => agentInstance.status === 'online');
  return findAgentInstance(onlineInstances, preferredId) ?? onlineInstances[0] ?? null;
}

function currentPathSessionRoute(): { sessionId: string } | null {
  if (typeof window === 'undefined') {
    return null;
  }
  const route = parseSessionRoute(window.location.pathname);
  if (!route) {
    return null;
  }
  return { sessionId: route.sessionId };
}

function navigateToSessionPath(sessionId: string, replace = false): void {
  if (typeof window === 'undefined') {
    return;
  }
  const nextPath = buildSessionPath(sessionId);
  if (window.location.pathname === nextPath) {
    return;
  }
  window.history[replace ? 'replaceState' : 'pushState']({}, '', nextPath);
}

function navigateToDraftPath(replace = false): void {
  if (typeof window === 'undefined') {
    return;
  }
  if (window.location.pathname === '/') {
    return;
  }
  window.history[replace ? 'replaceState' : 'pushState']({}, '', '/');
}

function upsertSessionList(prev: Session[], nextSession: Session): Session[] {
  const index = prev.findIndex((item) => item.session_id === nextSession.session_id);
  if (index < 0) {
    return [nextSession, ...prev];
  }
  const next = [...prev];
  next[index] = nextSession;
  return next;
}

function isUnauthorized(error: unknown): boolean {
  return error instanceof ApiError && error.status === 401;
}

function isNotFoundError(error: unknown): boolean {
  return error instanceof ApiError && error.status === 404;
}

function isVerificationRequired(error: unknown): boolean {
  return error instanceof ApiError && error.code === USER_NOT_VERIFIED_ERROR_CODE;
}

function shouldRefreshSessionsAfterAgentInventoryChange(previous: AgentInstance[], next: AgentInstance[]): boolean {
  if (previous.length !== next.length) {
    return true;
  }

  const previousById = new Map(previous.map((agentInstance) => [agentInstance.id, agentInstance]));
  for (const agentInstance of next) {
    const previousItem = previousById.get(agentInstance.id);
    if (!previousItem) {
      return true;
    }
    if (previousItem.status !== agentInstance.status) {
      return true;
    }
  }
  return false;
}

function canAccessVerifyRequestRoute(): boolean {
  if (typeof window === 'undefined') {
    return false;
  }
  const state = window.history.state as AuthHistoryState | null;
  return Boolean(state && typeof state === 'object' && (state as { allowVerifyRequest?: unknown }).allowVerifyRequest === true);
}

function isSafeInternalPath(value: unknown): value is string {
  return typeof value === 'string' && value.startsWith('/') && !value.startsWith('//');
}

function readAuthReturnTo(): string | null {
  if (typeof window === 'undefined') {
    return null;
  }
  const state = window.history.state as AuthHistoryState | null;
  if (!state || typeof state !== 'object' || !isSafeInternalPath(state.returnTo)) {
    return null;
  }
  return state.returnTo;
}

function buildAuthHistoryState(options?: { allowVerifyRequest?: boolean; returnTo?: string | null }): AuthHistoryState {
  const state: AuthHistoryState = {};
  if (options?.allowVerifyRequest) {
    state.allowVerifyRequest = true;
  }
  if (isSafeInternalPath(options?.returnTo)) {
    state.returnTo = options.returnTo;
  }
  return state;
}

function sameAuthHistoryState(left: unknown, right: AuthHistoryState): boolean {
  if (!left || typeof left !== 'object') {
    return Object.keys(right).length === 0;
  }
  const state = left as AuthHistoryState;
  return state.allowVerifyRequest === right.allowVerifyRequest && state.returnTo === right.returnTo;
}

function readSessionRouteFromInternalPath(path: string | null): { sessionId: string } | null {
  if (!path || typeof window === 'undefined' || !isSafeInternalPath(path)) {
    return null;
  }
  try {
    const url = new URL(path, window.location.origin);
    const route = parseSessionRoute(url.pathname);
    return route ? { sessionId: route.sessionId } : null;
  } catch {
    return null;
  }
}

function isKnownAuthPath(path: string): boolean {
  return Object.values(AUTH_ROUTE_PATHS).includes(path as NavigableAuthMode);
}

function isLegacyOrUnknownNonSessionPath(path: string): boolean {
  if (path === '/' || isKnownAuthPath(path)) {
    return false;
  }
  return parseSessionRoute(path) === null;
}

function readAuthRoute(): AuthRoute {
  if (typeof window === 'undefined') {
    return { mode: 'login', token: null, normalize: false, showHome: false, returnTo: null };
  }
  const path = window.location.pathname.replace(/\/+$/, '') || '/';
  const params = new URLSearchParams(window.location.search);
  const token = params.get('token');
  const returnTo = readAuthReturnTo();
  if (path === '/') {
    return { mode: 'login', token: null, normalize: false, showHome: true, returnTo: null };
  }
  if (path === AUTH_ROUTE_PATHS.login) {
    return { mode: 'login', token: null, normalize: false, showHome: false, returnTo };
  }
  if (path === AUTH_ROUTE_PATHS.register) {
    return { mode: 'register', token: null, normalize: false, showHome: false, returnTo };
  }
  if (path === '/reset-password') {
    return { mode: 'reset-password', token, normalize: false, showHome: false, returnTo };
  }
  if (path === '/verify') {
    if (token) {
      return { mode: 'verify', token, normalize: false, showHome: false, returnTo };
    }
    if (canAccessVerifyRequestRoute()) {
      return { mode: 'verify', token: null, normalize: false, showHome: false, returnTo };
    }
    return { mode: 'login', token: null, normalize: true, showHome: false, returnTo };
  }
  if (path === '/forgot-password') {
    return { mode: 'forgot-password', token: null, normalize: false, showHome: false, returnTo };
  }
  if (isLegacyOrUnknownNonSessionPath(path)) {
    return { mode: 'not-found', token: null, normalize: false, showHome: false, returnTo: null };
  }
  const sessionRoute = parseSessionRoute(path);
  if (sessionRoute) {
    return {
      mode: 'login',
      token: null,
      normalize: true,
      showHome: false,
      returnTo: `${buildSessionPath(sessionRoute.sessionId)}${window.location.search}`
    };
  }
  return { mode: 'login', token: null, normalize: true, showHome: false, returnTo };
}

function navigateAuthRoute(
  mode: NavigableAuthMode,
  replace = false,
  options?: { allowVerifyRequest?: boolean; returnTo?: string | null }
): void {
  if (typeof window === 'undefined') {
    return;
  }
  const path = AUTH_ROUTE_PATHS[mode];
  const state = buildAuthHistoryState({
    allowVerifyRequest: mode === 'verify' && options?.allowVerifyRequest,
    returnTo: options?.returnTo ?? null
  });
  if (window.location.pathname === path && window.location.search === '' && sameAuthHistoryState(window.history.state, state)) {
    return;
  }
  window.history[replace ? 'replaceState' : 'pushState'](state, '', path);
}

export function App() {
  const initialAuthRoute = useMemo(() => readAuthRoute(), []);
  const [authReady, setAuthReady] = useState(false);
  const [currentUser, setCurrentUser] = useState<AuthUser | null>(null);
  const [routeNotFound, setRouteNotFound] = useState(initialAuthRoute.mode === 'not-found');
  const [showMarketingHome, setShowMarketingHome] = useState(initialAuthRoute.showHome);
  const [authMode, setAuthMode] = useState<AuthScreenMode>(initialAuthRoute.mode);
  const [authActionToken, setAuthActionToken] = useState<string | null>(initialAuthRoute.token);
  const [authEmail, setAuthEmail] = useState('');
  const [authPassword, setAuthPassword] = useState('');
  const [authDisplayName, setAuthDisplayName] = useState('');
  const [authBusy, setAuthBusy] = useState(false);
  const [authErrorText, setAuthErrorText] = useState<string | null>(null);
  const [authInfoText, setAuthInfoText] = useState<string | null>(null);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [agentInstances, setAgentInstances] = useState<AgentInstance[]>([]);
  const [loadingAgentInstances, setLoadingAgentInstances] = useState(false);
  const [pairingDisplayName, setPairingDisplayName] = useState('');
  const [pairingCode, setPairingCode] = useState<PairingCodeResponse | null>(null);
  const [pairingBusy, setPairingBusy] = useState(false);
  const [pairingErrorText, setPairingErrorText] = useState<string | null>(null);
  const [workspacePaneMode, setWorkspacePaneMode] = useState<WorkspacePaneMode>('chat');
  const [settingsSection, setSettingsSection] = useState<SettingsSection>('profile');
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
  const [defaultAgentInstanceId, setDefaultAgentInstanceId] = useState<string | null>(() =>
    readStoredDefaultAgentInstanceId()
  );
  const [sidebarCollapsed, setSidebarCollapsed] = useState(() => readStoredSidebarCollapsed());
  const [draftAgentInstanceId, setDraftAgentInstanceId] = useState<string | null>(null);
  const [draftWorkspace, setDraftWorkspace] = useState(() => buildWorkspacePath());
  const [draftWorkspaceDirty, setDraftWorkspaceDirty] = useState(false);
  const [isDraftSession, setIsDraftSession] = useState(false);
  const [transcriptItems, setTranscriptItems] = useState<TranscriptItem[]>([]);
  const [composer, setComposer] = useState('');
  const [composerContextRefs, setComposerContextRefs] = useState<PromptResourceLinkItem[]>([]);
  const [composerImages, setComposerImages] = useState<PromptImageItem[]>([]);
  const [loadingMessages, setLoadingMessages] = useState(false);
  const [sending, setSending] = useState(false);
  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const [liveRunProjection, setLiveRunProjection] = useState<LiveRunProjection | null>(null);
  const [pendingPermissionDecisions, setPendingPermissionDecisions] = useState<Record<string, boolean>>({});
  const [errorText, setErrorText] = useState<string | null>(null);
  const [mentionResults, setMentionResults] = useState<ContextCandidate[]>([]);
  const [mentionQuery, setMentionQuery] = useState('');
  const [mentionOpen, setMentionOpen] = useState(false);
  const [mentionLoading, setMentionLoading] = useState(false);
  const [highlightedMentionIndex, setHighlightedMentionIndex] = useState(0);

  const eventSourceRef = useRef<EventSource | null>(null);
  const messageListRef = useRef<HTMLDivElement | null>(null);
  const composerRef = useRef<HTMLTextAreaElement | null>(null);
  const activeSessionIdRef = useRef<string | null>(null);
  const activeRunIdRef = useRef<string | null>(null);
  const sessionsRef = useRef<Session[]>([]);
  const agentInstancesRef = useRef<AgentInstance[]>([]);
  const isDraftSessionRef = useRef(false);
  const lastRunEventAtRef = useRef(0);
  const mentionMatchRef = useRef<MentionMatch | null>(null);
  const mentionSearchSeqRef = useRef(0);
  const lastGeneratedWorkspaceRef = useRef(draftWorkspace);
  const agentInventoryRefreshPromiseRef = useRef<Promise<AgentInstance[]> | null>(null);
  const refreshAgentInventoryRef = useRef<(options?: RefreshAgentInventoryOptions) => Promise<AgentInstance[]>>(async () => []);

  function clearRuntimeState(): void {
    closeRunStream();
    setSessions([]);
    setAgentInstances([]);
    agentInstancesRef.current = [];
    setActiveSessionId(null);
    setDraftAgentInstanceId(null);
    setIsDraftSession(false);
    setTranscriptItems([]);
    setActiveRunId(null);
    setLiveRunProjection(null);
    setPendingPermissionDecisions({});
    setLoadingMessages(false);
    setSending(false);
    setErrorText(null);
    setWorkspacePaneMode('chat');
    setSettingsSection('profile');
    resetComposerDraft();
  }

  async function refreshAgentInventory(options: RefreshAgentInventoryOptions = {}): Promise<AgentInstance[]> {
    const showLoading = options.showLoading ?? true;
    const surfaceError = options.surfaceError ?? true;
    const clearOnError = options.clearOnError ?? true;
    const syncSessionsOnChange = options.syncSessionsOnChange ?? true;

    if (agentInventoryRefreshPromiseRef.current) {
      return agentInventoryRefreshPromiseRef.current;
    }

    if (showLoading) {
      setLoadingAgentInstances(true);
    }

    const request = (async () => {
      try {
        const previousItems = agentInstancesRef.current;
        const items = await listAgentInstances();
        const shouldRefreshSessions =
          syncSessionsOnChange && shouldRefreshSessionsAfterAgentInventoryChange(previousItems, items);
        agentInstancesRef.current = items;
        setAgentInstances(items);
        if (shouldRefreshSessions) {
          void refreshSessions(false);
        }
        return items;
      } catch (error) {
        if (isUnauthorized(error)) {
          setCurrentUser(null);
          clearRuntimeState();
          return [];
        }
        if (surfaceError) {
          setErrorText(error instanceof Error ? error.message : 'Failed to load machines');
        }
        if (clearOnError) {
          setAgentInstances([]);
          agentInstancesRef.current = [];
          return [];
        }
        return agentInstances;
      } finally {
        agentInventoryRefreshPromiseRef.current = null;
        if (showLoading) {
          setLoadingAgentInstances(false);
        }
      }
    })();

    agentInventoryRefreshPromiseRef.current = request;
    return request;
  }

  async function loadAuthenticatedWorkspace(): Promise<void> {
    setErrorText(null);
    setRouteNotFound(false);
    if (typeof window !== 'undefined') {
      const path = window.location.pathname.replace(/\/+$/, '') || '/';
      if (isLegacyOrUnknownNonSessionPath(path)) {
        setRouteNotFound(true);
        return;
      }
    }
    const nextAgentInstances = await refreshAgentInventory({ syncSessionsOnChange: false });
    const nextSessions = await refreshSessions(false);
    if (currentPathSessionRoute()) {
      if (activeSessionIdRef.current || isDraftSessionRef.current) {
        await refreshSessions(true);
      }
      return;
    }

    const returnToRoute = readSessionRouteFromInternalPath(readAuthReturnTo());
    if (returnToRoute && nextSessions.some((session) => session.session_id === returnToRoute.sessionId)) {
      await loadSessionTranscript(returnToRoute.sessionId, { clearError: false });
      if (activeSessionIdRef.current || isDraftSessionRef.current) {
        await refreshSessions(true);
      }
      return;
    }

    const hasAvailableMachine = pickDraftAgentInstance(nextAgentInstances, defaultAgentInstanceId) !== null;
    const shouldOpenMachines = nextSessions.length === 0 || !hasAvailableMachine;

    setActiveSessionId(null);
    setTranscriptItems([]);
    setActiveRunId(null);
    setLiveRunProjection(null);
    setPendingPermissionDecisions({});
    setIsDraftSession(true);
    setWorkspacePaneMode(shouldOpenMachines ? 'machines' : 'chat');
    resetComposerDraft();
    resetDraftSetup(nextAgentInstances);
    navigateToDraftPath(true);

    if (activeSessionIdRef.current || isDraftSessionRef.current) {
      await refreshSessions(true);
    }
  }

  async function bootstrapAuthState(): Promise<void> {
    setAuthReady(false);
    try {
      await ensureCsrfToken();
      const sessionState = await restoreAuthSession();
      if (!sessionState.authenticated || !sessionState.user) {
        setCurrentUser(null);
        setAgentInstances([]);
        clearRuntimeState();
        return;
      }

      setCurrentUser(sessionState.user);
      await loadAuthenticatedWorkspace();
    } catch (error) {
      if (isVerificationRequired(error)) {
        enterVerifyRequestMode('Send a verification email to continue.');
      } else if (!isUnauthorized(error)) {
        setAuthErrorText(error instanceof Error ? error.message : 'Failed to initialize authentication');
      }
      setCurrentUser(null);
      setAgentInstances([]);
      clearRuntimeState();
    } finally {
      setAuthReady(true);
    }
  }

  useEffect(() => {
    activeSessionIdRef.current = activeSessionId;
  }, [activeSessionId]);

  useEffect(() => {
    activeRunIdRef.current = activeRunId;
    if (!activeRunId) {
      lastRunEventAtRef.current = 0;
    }
  }, [activeRunId]);

  useEffect(() => {
    sessionsRef.current = sessions;
  }, [sessions]);

  useEffect(() => {
    agentInstancesRef.current = agentInstances;
  }, [agentInstances]);

  useEffect(() => {
    isDraftSessionRef.current = isDraftSession;
  }, [isDraftSession]);

  useEffect(() => {
    refreshAgentInventoryRef.current = refreshAgentInventory;
  }, [refreshAgentInventory]);

  useEffect(() => {
    storeSidebarCollapsed(sidebarCollapsed);
  }, [sidebarCollapsed]);

  const activeSession = useMemo(
    () => sessions.find((session) => session.session_id === activeSessionId) ?? null,
    [sessions, activeSessionId]
  );

  const draftAgentInstance = useMemo(
    () => findAgentInstance(agentInstances, draftAgentInstanceId),
    [agentInstances, draftAgentInstanceId]
  );
  const hasOnlineAgentInstances = useMemo(
    () => agentInstances.some((agentInstance) => agentInstance.status === 'online'),
    [agentInstances]
  );
  const shouldAutoRefreshDraftMachines = workspacePaneMode === 'chat' && isDraftSession && !hasOnlineAgentInstances;
  const shouldAutoRefreshAgentInventory = workspacePaneMode === 'machines' || shouldAutoRefreshDraftMachines;
  const agentInventoryRefreshIntervalMs =
    workspacePaneMode === 'machines'
      ? hasOnlineAgentInstances
        ? ACTIVE_MACHINE_REFRESH_INTERVAL_MS
        : WAITING_MACHINE_REFRESH_INTERVAL_MS
      : DRAFT_MACHINE_REFRESH_INTERVAL_MS;

  const composerItems = useMemo<PromptItem[]>(
    () => [...composerContextRefs, ...composerImages],
    [composerContextRefs, composerImages]
  );

  const mentionSearchTarget = useMemo(
    () => ({
      agentInstanceId: isDraftSession ? draftAgentInstanceId : activeSession?.agent_instance_id ?? null,
      cwd: isDraftSession ? draftWorkspace.trim() || undefined : activeSession?.cwd
    }),
    [activeSession?.agent_instance_id, activeSession?.cwd, draftAgentInstanceId, draftWorkspace, isDraftSession]
  );

  const canCompose = Boolean(
    activeSessionId || (isDraftSession && draftAgentInstance?.status === 'online' && draftWorkspace.trim().length > 0)
  );
  const canSend =
    canCompose && !sending && !loadingMessages && (composer.trim().length > 0 || composerItems.length > 0);
  const visibleTranscriptItems = useMemo<TranscriptItem[]>(
    () => [...transcriptItems, ...materializeLiveTranscriptItems(liveRunProjection)],
    [transcriptItems, liveRunProjection]
  );
  const verifyWithToken = authMode === 'verify' && Boolean(authActionToken);
  const authTitle =
    authMode === 'login'
      ? 'Log in'
      : authMode === 'register'
        ? 'Sign up'
        : authMode === 'not-found'
          ? 'Not Found'
        : authMode === 'forgot-password'
          ? 'Reset password'
          : authMode === 'reset-password'
            ? 'Choose a new password'
            : 'Verify your email';
  const authSubtitle =
    authMode === 'login'
      ? 'Pair your machines and continue your sessions from anywhere.'
      : authMode === 'register'
        ? 'Pair your machines and continue your sessions from anywhere.'
        : authMode === 'not-found'
          ? 'The page you requested does not exist.'
        : authMode === 'forgot-password'
          ? 'Enter your email and we will send you a reset link.'
          : authMode === 'reset-password'
            ? 'Choose a new password to continue.'
            : verifyWithToken
              ? authErrorText
                ? 'This verification link could not be completed.'
                : 'Confirming your email now.'
              : 'Enter your email to get a new verification link.';

  useEffect(() => {
    void bootstrapAuthState();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (initialAuthRoute.normalize && initialAuthRoute.mode !== 'not-found') {
      navigateAuthRoute(initialAuthRoute.mode, true, { returnTo: initialAuthRoute.returnTo });
    }
  }, [initialAuthRoute]);

  useEffect(() => {
    if (currentUser) {
      return;
    }
    const onPopState = () => {
      const route = readAuthRoute();
      setRouteNotFound(route.mode === 'not-found');
      setShowMarketingHome(route.showHome);
      setAuthMode(route.mode);
      setAuthActionToken(route.token);
      setAuthErrorText(null);
      setAuthInfoText(null);
      if (route.normalize && route.mode !== 'not-found') {
        navigateAuthRoute(route.mode, true, { returnTo: route.returnTo });
      }
    };
    window.addEventListener('popstate', onPopState);
    return () => {
      window.removeEventListener('popstate', onPopState);
    };
  }, [currentUser]);

  useEffect(() => {
    if (!authReady || currentUser || authMode !== 'verify' || !authActionToken) {
      return;
    }
    void handleVerifyEmailSubmit();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [authReady, currentUser, authMode, authActionToken]);

  useEffect(() => {
    if (!currentUser) {
      closeRunStream();
      return;
    }
    const onPopState = () => {
      const path = window.location.pathname.replace(/\/+$/, '') || '/';
      if (isLegacyOrUnknownNonSessionPath(path)) {
        setRouteNotFound(true);
        return;
      }
      setRouteNotFound(false);
      const route = currentPathSessionRoute();
      if (route) {
        void handleSessionSelect(route.sessionId, false);
        return;
      }
      void handleCreateSession(false);
    };
    window.addEventListener('popstate', onPopState);
    return () => {
      window.removeEventListener('popstate', onPopState);
      closeRunStream();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentUser?.id]);

  useEffect(() => {
    const list = messageListRef.current;
    if (!list) {
      return;
    }
    list.scrollTop = list.scrollHeight;
  }, [visibleTranscriptItems, activeSessionId, isDraftSession]);

  useEffect(() => {
    if (!mentionOpen) {
      setMentionLoading(false);
      setMentionResults([]);
      setHighlightedMentionIndex(0);
      return;
    }

    const normalizedQuery = mentionQuery.trim();
    if (!normalizedQuery || !mentionSearchTarget.agentInstanceId) {
      setMentionLoading(false);
      setMentionResults([]);
      setHighlightedMentionIndex(0);
      return;
    }

    const currentSeq = mentionSearchSeqRef.current + 1;
    mentionSearchSeqRef.current = currentSeq;
    setMentionLoading(true);

    const timeoutId = window.setTimeout(() => {
      void searchContext(normalizedQuery, {
        cwd: mentionSearchTarget.cwd,
        agentInstanceId: mentionSearchTarget.agentInstanceId
      })
        .then((response) => {
          if (mentionSearchSeqRef.current !== currentSeq) {
            return;
          }
          setMentionResults(response.items);
          setHighlightedMentionIndex(0);
        })
        .catch((error) => {
          if (mentionSearchSeqRef.current !== currentSeq) {
            return;
          }
          if (isUnauthorized(error)) {
            setCurrentUser(null);
            clearRuntimeState();
            navigateToDraftPath(true);
            return;
          }
          setMentionResults([]);
          setErrorText(error instanceof Error ? error.message : 'Failed to search context');
        })
        .finally(() => {
          if (mentionSearchSeqRef.current === currentSeq) {
            setMentionLoading(false);
          }
        });
    }, MENTION_SEARCH_DEBOUNCE_MS);

    return () => {
      window.clearTimeout(timeoutId);
    };
    // clearRuntimeState intentionally stays out of the dependency list to avoid resetting
    // the debounced mention search effect on every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mentionOpen, mentionQuery, mentionSearchTarget.agentInstanceId, mentionSearchTarget.cwd]);

  useEffect(() => {
    if (!isDraftSession) {
      return;
    }

    const preferred = pickDraftAgentInstance(agentInstances, defaultAgentInstanceId);

    if (agentInstances.length === 0 || preferred === null) {
      setDraftAgentInstanceId(null);
      return;
    }

    if (
      draftAgentInstanceId &&
      agentInstances.some((agentInstance) => agentInstance.id === draftAgentInstanceId && agentInstance.status === 'online')
    ) {
      return;
    }

    setDraftAgentInstanceId(preferred.id);
    if (!draftWorkspaceDirty || draftWorkspace === lastGeneratedWorkspaceRef.current || draftWorkspace.trim() === '') {
      const nextWorkspace = buildWorkspacePath(preferred.workspace_roots[0]);
      lastGeneratedWorkspaceRef.current = nextWorkspace;
      setDraftWorkspace(nextWorkspace);
      setDraftWorkspaceDirty(false);
    }
  }, [agentInstances, defaultAgentInstanceId, draftAgentInstanceId, draftWorkspace, draftWorkspaceDirty, isDraftSession]);

  useEffect(() => {
    if (!currentUser || !shouldAutoRefreshAgentInventory || typeof window === 'undefined' || typeof document === 'undefined') {
      return;
    }

    let timeoutId: number | null = null;
    let disposed = false;

    const refreshSilently = async (): Promise<void> => {
      if (document.hidden) {
        return;
      }
      await refreshAgentInventoryRef.current({
        showLoading: false,
        surfaceError: false,
        clearOnError: false
      });
    };

    const clearScheduledRefresh = (): void => {
      if (timeoutId !== null) {
        window.clearTimeout(timeoutId);
        timeoutId = null;
      }
    };

    const scheduleNextRefresh = (): void => {
      clearScheduledRefresh();
      if (disposed || document.hidden) {
        return;
      }
      timeoutId = window.setTimeout(() => {
        void refreshSilently().finally(() => {
          if (!disposed) {
            scheduleNextRefresh();
          }
        });
      }, agentInventoryRefreshIntervalMs);
    };

    const handleVisibilityChange = (): void => {
      if (document.hidden) {
        clearScheduledRefresh();
        return;
      }
      void refreshSilently().finally(() => {
        if (!disposed) {
          scheduleNextRefresh();
        }
      });
    };

    const handleWindowFocus = (): void => {
      if (document.hidden) {
        return;
      }
      void refreshSilently();
    };

    scheduleNextRefresh();
    window.addEventListener('focus', handleWindowFocus);
    document.addEventListener('visibilitychange', handleVisibilityChange);

    return () => {
      disposed = true;
      clearScheduledRefresh();
      window.removeEventListener('focus', handleWindowFocus);
      document.removeEventListener('visibilitychange', handleVisibilityChange);
    };
  }, [agentInventoryRefreshIntervalMs, currentUser, shouldAutoRefreshAgentInventory]);

  function closeRunStream(): void {
    if (eventSourceRef.current) {
      eventSourceRef.current.close();
      eventSourceRef.current = null;
    }
    lastRunEventAtRef.current = 0;
  }

  function applyTranscriptSnapshot(
    sessionId: string,
    response: { session: Session; items: TranscriptItem[] },
    options: {
      fallbackRunId?: string | null;
      resetComposer?: boolean;
      updateHistory?: boolean;
    } = {}
  ): string | null {
    const nextActiveRunId = deriveActiveRunId(response.session, response.items, options.fallbackRunId ?? null);
    setRouteNotFound(false);
    setSessions((prev) => upsertSessionList(prev, response.session));
    setTranscriptItems(response.items);
    setActiveSessionId(sessionId);
    setIsDraftSession(false);
    setWorkspacePaneMode('chat');
    if (options.updateHistory ?? true) {
      navigateToSessionPath(sessionId, true);
    }
    setActiveRunId(nextActiveRunId);
    setLiveRunProjection(null);
    setPendingPermissionDecisions({});
    if (options.resetComposer ?? true) {
      resetComposerDraft();
    }
    return nextActiveRunId;
  }

  function clearMentionState(): void {
    mentionMatchRef.current = null;
    mentionSearchSeqRef.current += 1;
    setMentionOpen(false);
    setMentionQuery('');
    setMentionLoading(false);
    setMentionResults([]);
    setHighlightedMentionIndex(0);
  }

  function resetComposerDraft(): void {
    setComposer('');
    setComposerContextRefs([]);
    setComposerImages([]);
    clearMentionState();
  }

  function resetDraftSetup(agentInstancesForDraft: AgentInstance[]): void {
    const preferred = pickDraftAgentInstance(agentInstancesForDraft, defaultAgentInstanceId);
    setDraftAgentInstanceId(preferred?.id ?? null);
    const nextWorkspace = buildWorkspacePath(preferred?.workspace_roots[0]);
    lastGeneratedWorkspaceRef.current = nextWorkspace;
    setDraftWorkspace(nextWorkspace);
    setDraftWorkspaceDirty(false);
  }

  function syncMentionState(nextValue: string, selectionStart: number | null): void {
    const match = findMentionMatch(nextValue, selectionStart);
    mentionMatchRef.current = match;
    if (!match) {
      clearMentionState();
      return;
    }
    setMentionQuery(match.query);
    setMentionOpen(true);
  }

  async function refreshSessions(selectFirst: boolean): Promise<Session[]> {
    try {
      const response = await listSessions();
      sessionsRef.current = response.items;
      setSessions(response.items);

      const route = currentPathSessionRoute();
      const currentSessionId = activeSessionIdRef.current;
      const inDraft = isDraftSessionRef.current;

      if (route) {
        if (currentSessionId !== route.sessionId || inDraft || transcriptItems.length === 0) {
          await loadSessionTranscript(route.sessionId, { clearError: false });
        }
        return response.items;
      }

      if (response.items.length === 0) {
        if (!inDraft) {
          setActiveSessionId(null);
          setTranscriptItems([]);
          setActiveRunId(null);
        }
        return response.items;
      }

      if (inDraft) {
        return response.items;
      }

      if (currentSessionId && response.items.some((session) => session.session_id === currentSessionId)) {
        return response.items;
      }

      if (!currentSessionId && !selectFirst) {
        return response.items;
      }

      navigateToSessionPath(response.items[0].session_id, true);
      await loadSessionTranscript(response.items[0].session_id, { clearError: false });
      return response.items;
    } catch (error) {
      if (isUnauthorized(error)) {
        setCurrentUser(null);
        clearRuntimeState();
        return [];
      }
      setErrorText(error instanceof Error ? error.message : 'Failed to load sessions');
      return [];
    }
  }

  async function loadSessionTranscript(
    sessionId: string,
    options: {
      clearError?: boolean;
      fallbackRunId?: string | null;
      showLoading?: boolean;
    } = {}
  ): Promise<void> {
    const clearError = options.clearError ?? true;
    const fallbackRunId = options.fallbackRunId ?? null;
    const showLoading = options.showLoading ?? true;
    if (showLoading) {
      setLoadingMessages(true);
    }
    if (clearError) {
      setErrorText(null);
    }
    try {
      const response = await getSessionTranscript(sessionId);
      applyTranscriptSnapshot(sessionId, response, {
        fallbackRunId,
        resetComposer: true,
        updateHistory: true
      });
    } catch (error) {
      if (isUnauthorized(error)) {
        await handleLogout();
        return;
      }
      if (isNotFoundError(error)) {
        setRouteNotFound(true);
        return;
      }
      setErrorText(error instanceof Error ? error.message : 'Failed to load transcript');
    } finally {
      if (showLoading) {
        setLoadingMessages(false);
      }
    }
  }

  async function handleCreateSession(updateHistory = true): Promise<void> {
    setErrorText(null);
    setRouteNotFound(false);
    closeRunStream();
    setSending(false);
    setActiveRunId(null);
    setLiveRunProjection(null);
    setPendingPermissionDecisions({});
    setActiveSessionId(null);
    setTranscriptItems([]);
    setIsDraftSession(true);
    setWorkspacePaneMode('chat');
    resetComposerDraft();
    if (updateHistory) {
      navigateToDraftPath();
    }

    const nextAgentInstances = await refreshAgentInventory();
    resetDraftSetup(nextAgentInstances);
  }

  async function handleSessionSelect(sessionId: string, updateHistory = true): Promise<void> {
    if (sessionId === activeSessionIdRef.current && !isDraftSessionRef.current) {
      if (updateHistory) {
        navigateToSessionPath(sessionId);
      }
      return;
    }
    setRouteNotFound(false);
    closeRunStream();
    setSending(false);
    setActiveRunId(null);
    setLiveRunProjection(null);
    setPendingPermissionDecisions({});
    setIsDraftSession(false);
    setWorkspacePaneMode('chat');
    resetComposerDraft();
    if (updateHistory) {
      navigateToSessionPath(sessionId);
    }
    await loadSessionTranscript(sessionId);
  }

  function bindRunStream(runId: string, sessionId: string): void {
    closeRunStream();
    lastRunEventAtRef.current = Date.now();

    const source = createRunEventSource(runId);
    eventSourceRef.current = source;

    const applyEvent = (event: MessageEvent<string>): RunStreamEventRow | null => {
      const row = parseRunEventRow(event.data);
      if (!row) {
        return null;
      }
      lastRunEventAtRef.current = Date.now();
      setLiveRunProjection((prev) => applyRunStreamEvent(prev, row, sessionId));
      return row;
    };

    source.addEventListener('run.queued', (event) => {
      void applyEvent(event as MessageEvent<string>);
    });

    source.addEventListener('run.accepted', (event) => {
      void applyEvent(event as MessageEvent<string>);
    });

    source.addEventListener('run.session_update', (event) => {
      void applyEvent(event as MessageEvent<string>);
    });

    source.addEventListener('run.permission.requested', (event) => {
      void applyEvent(event as MessageEvent<string>);
    });

    source.addEventListener('run.permission.decision', (event) => {
      const row = applyEvent(event as MessageEvent<string>);
      const payload = row?.payload ?? null;
      const approvalId = typeof payload?.approval_id === 'string' ? payload.approval_id : null;
      if (approvalId) {
        setPendingPermissionDecisions((prev) => {
          const next = { ...prev };
          delete next[approvalId];
          return next;
        });
      }
    });

    source.addEventListener('run.error', (event) => {
      const row = applyEvent(event as MessageEvent<string>);
      let message = 'Run failed unexpectedly';
      const payload = row?.payload ?? null;
      if (typeof payload?.error_message === 'string') {
        message = payload.error_message;
      }
      setErrorText(message);
      setActiveRunId(null);
      setSending(false);
      setPendingPermissionDecisions({});
      source.close();
      eventSourceRef.current = null;
      if (activeSessionIdRef.current === sessionId) {
        void loadSessionTranscript(sessionId, { clearError: false, showLoading: false });
      } else {
        void refreshSessions(false);
      }
    });

    source.addEventListener('run.completed', (event) => {
      void applyEvent(event as MessageEvent<string>);
      setActiveRunId(null);
      setSending(false);
      setPendingPermissionDecisions({});
      source.close();
      eventSourceRef.current = null;
      if (activeSessionIdRef.current === sessionId) {
        void loadSessionTranscript(sessionId, { showLoading: false });
      } else {
        void refreshSessions(false);
      }
    });

    source.onerror = () => {
      setErrorText((prev) => prev ?? 'Run stream disconnected unexpectedly.');
      setActiveRunId(null);
      setSending(false);
      setPendingPermissionDecisions({});
      source.close();
      eventSourceRef.current = null;
      if (activeSessionIdRef.current === sessionId) {
        void loadSessionTranscript(sessionId, { clearError: false, showLoading: false });
      } else {
        void refreshSessions(false);
      }
    };
  }

  useEffect(() => {
    if (
      !currentUser ||
      !activeRunId ||
      !activeSessionId ||
      isDraftSession ||
      typeof window === 'undefined' ||
      typeof document === 'undefined'
    ) {
      return;
    }

    let timeoutId: number | null = null;
    let disposed = false;
    let inFlight = false;

    const clearScheduledRefresh = (): void => {
      if (timeoutId !== null) {
        window.clearTimeout(timeoutId);
        timeoutId = null;
      }
    };

    const scheduleNextRefresh = (): void => {
      clearScheduledRefresh();
      if (disposed || document.hidden) {
        return;
      }
      timeoutId = window.setTimeout(() => {
        void reconcileTranscript();
      }, ACTIVE_RUN_RECONCILE_INTERVAL_MS);
    };

    const reconcileTranscript = async (): Promise<void> => {
      if (disposed || document.hidden || inFlight) {
        scheduleNextRefresh();
        return;
      }
      const lastEventAt = lastRunEventAtRef.current;
      if (lastEventAt > 0 && Date.now() - lastEventAt < STALE_RUN_STREAM_THRESHOLD_MS) {
        scheduleNextRefresh();
        return;
      }

      inFlight = true;
      try {
        const response = await getSessionTranscript(activeSessionId);
        if (disposed || activeSessionIdRef.current !== activeSessionId || activeRunIdRef.current !== activeRunId) {
          return;
        }
        const nextActiveRunId = applyTranscriptSnapshot(activeSessionId, response, {
          fallbackRunId: activeRunId,
          resetComposer: false,
          updateHistory: false
        });
        if (!nextActiveRunId) {
          setSending(false);
          closeRunStream();
        }
      } catch (error) {
        if (isUnauthorized(error)) {
          await handleLogout();
          return;
        }
        if (isNotFoundError(error)) {
          setRouteNotFound(true);
          return;
        }
      } finally {
        inFlight = false;
        if (!disposed && activeSessionIdRef.current === activeSessionId && activeRunIdRef.current === activeRunId) {
          scheduleNextRefresh();
        }
      }
    };

    const handleVisibilityChange = (): void => {
      if (document.hidden) {
        clearScheduledRefresh();
        return;
      }
      void reconcileTranscript();
    };

    const handleWindowFocus = (): void => {
      if (document.hidden) {
        return;
      }
      void reconcileTranscript();
    };

    scheduleNextRefresh();
    window.addEventListener('focus', handleWindowFocus);
    document.addEventListener('visibilitychange', handleVisibilityChange);

    return () => {
      disposed = true;
      clearScheduledRefresh();
      window.removeEventListener('focus', handleWindowFocus);
      document.removeEventListener('visibilitychange', handleVisibilityChange);
    };
  }, [activeRunId, activeSessionId, currentUser, isDraftSession]);

  function handleComposerChange(value: string, selectionStart: number | null): void {
    setComposer(value);
    syncMentionState(value, selectionStart);
  }

  function handleComposerSelectionChange(selectionStart: number | null): void {
    syncMentionState(composer, selectionStart);
  }

  function handleDraftAgentInstanceChange(agentInstanceId: string): void {
    const normalizedAgentInstanceId = agentInstanceId.trim() || null;
    setDraftAgentInstanceId(normalizedAgentInstanceId);
    const agentInstance = findAgentInstance(agentInstances, normalizedAgentInstanceId);
    if (!draftWorkspaceDirty || draftWorkspace === lastGeneratedWorkspaceRef.current || draftWorkspace.trim() === '') {
      const nextWorkspace = buildWorkspacePath(agentInstance?.workspace_roots[0]);
      lastGeneratedWorkspaceRef.current = nextWorkspace;
      setDraftWorkspace(nextWorkspace);
      setDraftWorkspaceDirty(false);
    }
  }

  function handleDraftWorkspaceChange(value: string): void {
    setDraftWorkspace(value);
    setDraftWorkspaceDirty(value !== lastGeneratedWorkspaceRef.current);
  }

  function handleSetDefaultAgentInstance(): void {
    if (!draftAgentInstanceId) {
      return;
    }
    setDefaultAgentInstanceId(draftAgentInstanceId);
    storeDefaultAgentInstanceId(draftAgentInstanceId);
  }

  function handleSelectContextCandidate(candidate: ContextCandidate): void {
    const match = mentionMatchRef.current;
    if (!match) {
      return;
    }

    setComposer((prev) => `${prev.slice(0, match.start)}${prev.slice(match.end)}`);
    setComposerContextRefs((prev) => {
      if (prev.some((item) => item.uri === candidate.uri)) {
        return prev;
      }
      return [
        ...prev,
        {
          type: 'resource_link',
          uri: candidate.uri,
          name: candidate.name,
          relative_path: candidate.relative_path,
          kind: candidate.kind
        }
      ];
    });
    clearMentionState();

    window.requestAnimationFrame(() => {
      if (!composerRef.current) {
        return;
      }
      composerRef.current.focus();
      composerRef.current.setSelectionRange(match.start, match.start);
    });
  }

  function handleRemoveContextRef(uri: string): void {
    setComposerContextRefs((prev) => prev.filter((item) => item.uri !== uri));
  }

  async function handleImageFilesSelected(files: FileList | null): Promise<void> {
    if (!files || files.length === 0) {
      return;
    }

    const selectedFiles = [...files];
    const invalidType = selectedFiles.find((file) => !file.type.startsWith('image/'));
    if (invalidType) {
      setErrorText(`Only image files are supported: ${invalidType.name}`);
      return;
    }

    const oversized = selectedFiles.find((file) => file.size > MAX_IMAGE_SIZE_BYTES);
    if (oversized) {
      setErrorText(`Image is too large (max 5 MB): ${oversized.name}`);
      return;
    }

    const nextTotalBytes = totalImageBytes(composerImages) + selectedFiles.reduce((sum, file) => sum + file.size, 0);
    if (nextTotalBytes > MAX_TOTAL_IMAGE_BYTES) {
      setErrorText('Images exceed the total 15 MB limit for one message.');
      return;
    }

    try {
      const drafts = await Promise.all(
        selectedFiles.map(async (file) => ({
          type: 'image' as const,
          image_url: await readFileAsDataUrl(file),
          name: file.name,
          mime_type: file.type,
          size: file.size
        }))
      );

      setComposerImages((prev) => {
        const existing = new Set(prev.map((item) => item.image_url));
        return [...prev, ...drafts.filter((item) => !existing.has(item.image_url))];
      });
      setErrorText(null);
    } catch (error) {
      setErrorText(error instanceof Error ? error.message : 'Failed to read image');
    }
  }

  function handleRemoveImage(imageUrl: string): void {
    setComposerImages((prev) => prev.filter((item) => item.image_url !== imageUrl));
  }

  async function handleSend(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    const content = composer.trim();
    const items = [...composerContextRefs, ...composerImages] as PromptItem[];
    if ((content.length === 0 && items.length === 0) || sending || loadingMessages) {
      return;
    }

    setErrorText(null);

    let sessionId = activeSessionIdRef.current;
    if (!sessionId) {
      if (!isDraftSessionRef.current) {
        return;
      }

      const agentInstanceId = draftAgentInstanceId;
      const workspace = draftWorkspace.trim();

      if (!agentInstanceId) {
        setErrorText('Select a machine before sending the first message.');
        return;
      }
      if (draftAgentInstance?.status !== 'online') {
        setErrorText('Selected machine is offline.');
        return;
      }
      if (!workspace) {
        setErrorText('Set a workspace before sending the first message.');
        return;
      }

      try {
        const created = await createSession({
          cwd: workspace,
          agent_instance_id: agentInstanceId
        });
        const now = new Date().toISOString();
        sessionId = created.session_id;
        const createdSessionId = sessionId;
        setSessions((prev) =>
          upsertSessionList(prev, {
            session_id: createdSessionId,
            title: created.title,
            cwd: created.cwd,
            agent_instance_id: created.agent_instance_id,
            status: created.status,
            remote_updated_at: created.created_at,
            created_at: created.created_at,
            updated_at: now
          })
        );
        setActiveSessionId(createdSessionId);
        setIsDraftSession(false);
        navigateToSessionPath(createdSessionId);
      } catch (error) {
        if (isUnauthorized(error)) {
          await handleLogout();
          return;
        }
        setErrorText(error instanceof Error ? error.message : 'Failed to create session');
        return;
      }
    }

    const previousComposer = composer;
    const previousContextRefs = composerContextRefs;
    const previousImages = composerImages;
    const optimisticMessageId = `optimistic-${Date.now()}-${Math.random().toString(36).slice(2)}`;
    const optimisticRunId = `optimistic-run-${Date.now()}-${Math.random().toString(36).slice(2)}`;
    const optimisticTimestamp = new Date().toISOString();
    const resolvedSessionId = sessionId;
    resetComposerDraft();
    setPendingPermissionDecisions({});
    setLiveRunProjection(null);
    setSending(true);
    setTranscriptItems((prev) => [
      ...prev,
      {
        kind: 'user_message',
        message_id: optimisticMessageId,
        session_id: resolvedSessionId,
        run_id: optimisticRunId,
        content,
        items,
        status: 'completed',
        created_at: optimisticTimestamp,
        updated_at: optimisticTimestamp
      }
    ]);

    try {
      const response = await sendMessage(resolvedSessionId, { content, items });
      setActiveRunId(response.run_id);
      bindRunStream(response.run_id, resolvedSessionId);
    } catch (error) {
      if (isUnauthorized(error)) {
        await handleLogout();
        return;
      }
      setSending(false);
      setComposer(previousComposer);
      setComposerContextRefs(previousContextRefs);
      setComposerImages(previousImages);
      setTranscriptItems((prev) => prev.filter((item) => item.kind !== 'user_message' || item.message_id !== optimisticMessageId));
      setErrorText(error instanceof Error ? error.message : 'Failed to send message');
    }
  }

  function handleComposerKeyDown(event: KeyboardEvent<HTMLTextAreaElement>): void {
    if (mentionOpen) {
      if (event.key === 'ArrowDown') {
        if (mentionResults.length > 0) {
          event.preventDefault();
          setHighlightedMentionIndex((prev) => (prev + 1) % mentionResults.length);
        }
        return;
      }
      if (event.key === 'ArrowUp') {
        if (mentionResults.length > 0) {
          event.preventDefault();
          setHighlightedMentionIndex((prev) => (prev - 1 + mentionResults.length) % mentionResults.length);
        }
        return;
      }
      if (event.key === 'Escape') {
        event.preventDefault();
        clearMentionState();
        return;
      }
      if ((event.key === 'Enter' || event.key === 'Tab') && mentionResults.length > 0) {
        event.preventDefault();
        const selected = mentionResults[highlightedMentionIndex] ?? mentionResults[0];
        if (selected) {
          handleSelectContextCandidate(selected);
        }
        return;
      }
    }

    if (event.key !== 'Enter' || event.shiftKey) {
      return;
    }
    event.preventDefault();
    if (canSend) {
      event.currentTarget.form?.requestSubmit();
    }
  }

  async function handleCancel(): Promise<void> {
    if (!activeRunId) {
      return;
    }
    try {
      await cancelRun(activeRunId);
    } catch (error) {
      if (isUnauthorized(error)) {
        await handleLogout();
        return;
      }
      setErrorText(error instanceof Error ? error.message : 'Failed to cancel run');
    }
  }

  async function handlePermissionDecision(
    request: PermissionRequest,
    decision: PermissionDecision,
    optionId?: string
  ): Promise<void> {
    if (pendingPermissionDecisions[request.approval_id]) {
      return;
    }

    setErrorText(null);
    setPendingPermissionDecisions((prev) => ({ ...prev, [request.approval_id]: true }));
    try {
      await decideRunPermission(request.run_id, request.approval_id, decision, optionId);
      setLiveRunProjection((prev) =>
        applyLivePermissionDecision(
          prev,
          request.approval_id,
          decision,
          decision === 'selected' ? optionId ?? null : null
        )
      );
    } catch (error) {
      if (isUnauthorized(error)) {
        await handleLogout();
        return;
      }
      setErrorText(error instanceof Error ? error.message : 'Failed to decide permission');
    } finally {
      setPendingPermissionDecisions((prev) => {
        const next = { ...prev };
        delete next[request.approval_id];
        return next;
      });
    }
  }

  async function handleAuthSubmit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (authBusy) {
      return;
    }
    if (authMode === 'register' && authPassword.length < MIN_AUTH_PASSWORD_LENGTH) {
      setAuthErrorText(`Password must be at least ${MIN_AUTH_PASSWORD_LENGTH} characters long.`);
      return;
    }
    setAuthBusy(true);
    setAuthErrorText(null);
    setAuthInfoText(null);
    try {
      await ensureCsrfToken();
      if (authMode === 'register') {
        await registerUser({
          email: authEmail.trim(),
          password: authPassword,
          display_name: authDisplayName.trim() || undefined
        });
        setAuthPassword('');
        enterVerifyRequestMode('Check your inbox for a verification link.');
        return;
      }
      const response = await login({
        email: authEmail.trim(),
        password: authPassword
      });
      setCurrentUser(response.user);
      setAuthPassword('');
      await loadAuthenticatedWorkspace();
    } catch (error) {
      if (isVerificationRequired(error)) {
        setAuthPassword('');
        enterVerifyRequestMode('Check your inbox or send a new verification email.');
        return;
      }
      setAuthErrorText(error instanceof Error ? error.message : 'Authentication failed');
    } finally {
      setAuthBusy(false);
      setAuthReady(true);
    }
  }

  function resetToLoggedOutState(options: { infoText?: string | null; prefilledEmail?: string } = {}): void {
    clearCachedAuthState();
    setCurrentUser(null);
    setRouteNotFound(false);
    setShowMarketingHome(false);
    setPairingCode(null);
    setPairingDisplayName('');
    setPairingErrorText(null);
    setAuthMode('login');
    setAuthEmail(options.prefilledEmail ?? '');
    setAuthPassword('');
    setAuthDisplayName('');
    setAuthActionToken(null);
    setAuthErrorText(null);
    setAuthInfoText(options.infoText ?? null);
    clearRuntimeState();
    navigateAuthRoute('login', true);
  }

  async function handleLogout(): Promise<void> {
    try {
      await logout();
    } catch {
      // Best-effort logout; continue clearing local state even if the server session has already expired.
    }
    resetToLoggedOutState();
  }

  async function handleCreatePairingCode(): Promise<void> {
    setPairingBusy(true);
    setPairingErrorText(null);
    try {
      const response = await createPairingCode({
        agent: DEFAULT_PAIRING_AGENT,
        display_name: pairingDisplayName.trim() || undefined
      });
      setPairingCode(response);
    } catch (error) {
      if (isUnauthorized(error)) {
        await handleLogout();
        return;
      }
      setPairingErrorText(error instanceof Error ? error.message : 'Failed to create pairing code');
    } finally {
      setPairingBusy(false);
    }
  }

  function handleOpenSettings(): void {
    setSettingsSection('profile');
    setWorkspacePaneMode('settings');
  }

  async function handleOpenMachines(): Promise<void> {
    setWorkspacePaneMode('machines');
    await refreshAgentInventory();
  }

  async function handleUpdateProfile(displayName: string): Promise<AuthUser> {
    try {
      const updatedUser = await updateCurrentUserProfile({
        display_name: displayName.trim() || null
      });
      setCurrentUser(updatedUser);
      return updatedUser;
    } catch (error) {
      if (isUnauthorized(error)) {
        resetToLoggedOutState();
      }
      throw error instanceof Error ? error : new Error('Failed to update profile');
    }
  }

  async function handleSettingsPasswordChange(currentPassword: string, nextPassword: string): Promise<void> {
    const prefilledEmail = currentUser?.email ?? '';
    try {
      await changePassword({
        current_password: currentPassword,
        new_password: nextPassword
      });
      resetToLoggedOutState({
        infoText: 'Password updated. Please log in again.',
        prefilledEmail
      });
    } catch (error) {
      if (isUnauthorized(error)) {
        resetToLoggedOutState({ prefilledEmail });
      }
      throw error instanceof Error ? error : new Error('Failed to update password');
    }
  }

  function handleOpenChatPane(): void {
    setRouteNotFound(false);
    setWorkspacePaneMode('chat');
  }

  function switchAuthMode(nextMode: NavigableAuthMode): void {
    setRouteNotFound(false);
    setShowMarketingHome(false);
    setAuthMode(nextMode);
    setAuthErrorText(null);
    setAuthInfoText(null);
    if (nextMode !== 'reset-password' && nextMode !== 'verify') {
      setAuthActionToken(null);
    }
    navigateAuthRoute(nextMode, false, { returnTo: readAuthReturnTo() });
  }

  function enterVerifyRequestMode(message: string | null): void {
    setRouteNotFound(false);
    setShowMarketingHome(false);
    setAuthMode('verify');
    setAuthActionToken(null);
    setAuthErrorText(null);
    setAuthInfoText(message);
    navigateAuthRoute('verify', true, { allowVerifyRequest: true, returnTo: readAuthReturnTo() });
  }

  async function handleForgotPasswordSubmit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (authBusy) {
      return;
    }
    setAuthBusy(true);
    setAuthErrorText(null);
    setAuthInfoText(null);
    try {
      await ensureCsrfToken();
      await requestPasswordReset(authEmail.trim());
      setAuthInfoText('If the address exists, a reset email has been sent.');
    } catch (error) {
      setAuthErrorText(error instanceof Error ? error.message : 'Failed to request password reset');
    } finally {
      setAuthBusy(false);
    }
  }

  async function handleRequestVerifyToken(): Promise<void> {
    if (authBusy) {
      return;
    }
    setAuthBusy(true);
    setAuthErrorText(null);
    setAuthInfoText(null);
    try {
      await ensureCsrfToken();
      await requestVerifyToken(authEmail.trim());
      setAuthInfoText('If the address exists, a verification email has been sent.');
    } catch (error) {
      setAuthErrorText(error instanceof Error ? error.message : 'Failed to send verification email');
    } finally {
      setAuthBusy(false);
    }
  }

  async function handleVerifyRequestSubmit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    await handleRequestVerifyToken();
  }

  async function handleResetPasswordSubmit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (authBusy) {
      return;
    }
    if (authPassword.length < MIN_AUTH_PASSWORD_LENGTH) {
      setAuthErrorText(`Password must be at least ${MIN_AUTH_PASSWORD_LENGTH} characters long.`);
      return;
    }
    if (!authActionToken) {
      setAuthErrorText('Missing reset token in the URL.');
      return;
    }
    setAuthBusy(true);
    setAuthErrorText(null);
    setAuthInfoText(null);
    try {
      await ensureCsrfToken();
      await resetPassword(authActionToken, authPassword);
      setAuthInfoText('Password updated. Sign in with your new password.');
      setAuthPassword('');
      setAuthMode('login');
      navigateAuthRoute('login', true, { returnTo: readAuthReturnTo() });
    } catch (error) {
      setAuthErrorText(error instanceof Error ? error.message : 'Failed to reset password');
    } finally {
      setAuthBusy(false);
    }
  }

  async function handleVerifyEmailSubmit(): Promise<void> {
    if (authBusy) {
      return;
    }
    if (!authActionToken) {
      setAuthErrorText('Missing verification token in the URL.');
      return;
    }
    setAuthBusy(true);
    setAuthErrorText(null);
    setAuthInfoText(null);
    try {
      await ensureCsrfToken();
      await verifyEmail(authActionToken);
      setAuthInfoText('Email verified. Sign in to continue.');
      setAuthMode('login');
      navigateAuthRoute('login', true, { returnTo: readAuthReturnTo() });
    } catch (error) {
      setAuthErrorText(error instanceof Error ? error.message : 'Failed to verify email');
    } finally {
      setAuthBusy(false);
    }
  }

  function openMarketingHome(): void {
    setRouteNotFound(false);
    setShowMarketingHome(true);
    setAuthMode('login');
    setAuthActionToken(null);
    setAuthErrorText(null);
    setAuthInfoText(null);
    if (typeof window !== 'undefined') {
      window.history.pushState({}, '', '/');
    }
  }

  async function handleGoToWorkspace(): Promise<void> {
    setRouteNotFound(false);
    navigateToDraftPath(true);
    await loadAuthenticatedWorkspace();
  }

  if (!authReady) {
    return (
      <main className="auth-screen">
        <section className="auth-card">
          <img className="auth-logo" src={pocageLogo} alt="pocage logo" />
          <h1>Loading</h1>
        </section>
      </main>
    );
  }

  if (!currentUser) {
    if (showMarketingHome) {
      return (
        <MarketingHome
          onOpenLogin={() => switchAuthMode('login')}
          onOpenRegister={() => switchAuthMode('register')}
        />
      );
    }
    if (authMode === 'not-found') {
      return (
        <main className="public-page">
          <section className="public-page-body">
            <section className="auth-card">
              <img className="auth-logo" src={pocageLogo} alt="pocage logo" />
              <h1>{authTitle}</h1>
              <p className="auth-copy">{authSubtitle}</p>
              <div className="auth-form">
                <button className="auth-submit" type="button" onClick={() => switchAuthMode('login')}>
                  Go to sign in
                </button>
                <button className="auth-switch secondary" type="button" onClick={openMarketingHome}>
                  Go home
                </button>
              </div>
            </section>
          </section>

          <div className="public-page-footer">
            <SiteFooter variant="compact" />
          </div>
        </main>
      );
    }
    const isLoginLike = authMode === 'login' || authMode === 'register';
    return (
      <main className="auth-screen">
        <section className="auth-card">
          <img className="auth-logo" src={pocageLogo} alt="pocage logo" />
          <h1>{authTitle}</h1>
          <p className="auth-copy">{authSubtitle}</p>

          {authInfoText ? <p className="auth-info">{authInfoText}</p> : null}

          {isLoginLike ? (
            <form className="auth-form" onSubmit={(event) => void handleAuthSubmit(event)}>
              {authMode === 'register' ? (
                <label className="auth-field">
                  <span className="auth-visually-hidden">Display name</span>
                  <input
                    type="text"
                    value={authDisplayName}
                    onChange={(event) => setAuthDisplayName(event.target.value)}
                    autoComplete="nickname"
                    placeholder="Enter your display name (optional)"
                  />
                </label>
              ) : null}

              <label className="auth-field">
                <span className="auth-visually-hidden">Email</span>
                <input
                  type="email"
                  value={authEmail}
                  onChange={(event) => setAuthEmail(event.target.value)}
                  required
                  autoComplete="email"
                  placeholder="Enter your email"
                />
              </label>

              <label className="auth-field">
                <span className="auth-visually-hidden">Password</span>
                <input
                  type="password"
                  value={authPassword}
                  onChange={(event) => setAuthPassword(event.target.value)}
                  required
                  minLength={authMode === 'login' ? undefined : MIN_AUTH_PASSWORD_LENGTH}
                  autoComplete={authMode === 'login' ? 'current-password' : 'new-password'}
                  placeholder="Enter your password"
                />
              </label>

              {authMode === 'login' ? (
                <button className="auth-inline-link auth-inline-link-right" type="button" onClick={() => switchAuthMode('forgot-password')}>
                  Forgot your password?
                </button>
              ) : null}

              {authErrorText ? <p className="auth-error">{authErrorText}</p> : null}

              <button className="auth-submit" type="submit" disabled={authBusy}>
                {authBusy ? 'Working…' : 'Continue'}
              </button>
            </form>
          ) : null}

          {authMode === 'forgot-password' ? (
            <form className="auth-form" onSubmit={(event) => void handleForgotPasswordSubmit(event)}>
              <label className="auth-field">
                <span className="auth-visually-hidden">Email</span>
                <input
                  type="email"
                  value={authEmail}
                  onChange={(event) => setAuthEmail(event.target.value)}
                  required
                  autoComplete="email"
                  placeholder="Enter your email"
                />
              </label>
              {authErrorText ? <p className="auth-error">{authErrorText}</p> : null}
              <button className="auth-submit" type="submit" disabled={authBusy}>
                {authBusy ? 'Sending…' : 'Continue'}
              </button>
            </form>
          ) : null}

          {authMode === 'reset-password' ? (
            <form className="auth-form" onSubmit={(event) => void handleResetPasswordSubmit(event)}>
              <label className="auth-field">
                <span className="auth-visually-hidden">New password</span>
                <input
                  type="password"
                  value={authPassword}
                  onChange={(event) => setAuthPassword(event.target.value)}
                  required
                  minLength={MIN_AUTH_PASSWORD_LENGTH}
                  autoComplete="new-password"
                  placeholder="Enter a new password"
                />
              </label>
              {authErrorText ? <p className="auth-error">{authErrorText}</p> : null}
              <button className="auth-submit" type="submit" disabled={authBusy}>
                {authBusy ? 'Updating…' : 'Continue'}
              </button>
            </form>
          ) : null}

          {authMode === 'verify' ? (
            verifyWithToken ? (
              <div className="auth-form">
                {authErrorText ? <p className="auth-error">{authErrorText}</p> : null}
                {authErrorText ? (
                  <button className="auth-submit" type="button" disabled={authBusy} onClick={() => void handleVerifyEmailSubmit()}>
                    {authBusy ? 'Verifying…' : 'Continue'}
                  </button>
                ) : null}
                {authErrorText ? (
                  <button className="auth-switch secondary" type="button" disabled={authBusy} onClick={() => enterVerifyRequestMode('Enter your email to send a new verification link.')}>
                    Send a new link
                  </button>
                ) : null}
              </div>
            ) : (
              <form className="auth-form" onSubmit={(event) => void handleVerifyRequestSubmit(event)}>
                <label className="auth-field">
                  <span className="auth-visually-hidden">Email</span>
                  <input
                    type="email"
                    value={authEmail}
                    onChange={(event) => setAuthEmail(event.target.value)}
                    required
                    autoComplete="email"
                    placeholder="Enter your email"
                  />
                </label>
                {authErrorText ? <p className="auth-error">{authErrorText}</p> : null}
                <button className="auth-submit" type="submit" disabled={authBusy || authEmail.trim().length === 0}>
                  {authBusy ? 'Sending…' : 'Continue'}
                </button>
              </form>
            )
          ) : null}

          {authMode === 'login' ? (
            <p className="auth-footer-copy">
              Need an account?{' '}
              <button className="auth-switch inline" type="button" onClick={() => switchAuthMode('register')}>
                Sign up
              </button>
            </p>
          ) : null}

          {authMode === 'register' ? (
            <p className="auth-footer-copy">
              Already have an account?{' '}
              <button className="auth-switch inline" type="button" onClick={() => switchAuthMode('login')}>
                Log in
              </button>
            </p>
          ) : null}

          {(authMode === 'forgot-password' || authMode === 'reset-password' || authMode === 'verify') ? (
            <p className="auth-footer-copy">
              <button className="auth-switch inline" type="button" onClick={() => switchAuthMode('login')}>
                Back to log in
              </button>
            </p>
          ) : null}
        </section>
      </main>
    );
  }

  if (routeNotFound) {
    return (
      <main className="auth-screen">
        <section className="auth-card">
          <img className="auth-logo" src={pocageLogo} alt="pocage logo" />
          <h1>Not Found</h1>
          <p className="auth-copy">The page you requested does not exist.</p>
          <div className="auth-form">
            <button className="auth-submit" type="button" onClick={() => void handleGoToWorkspace()}>
              Go to workspace
            </button>
          </div>
        </section>
      </main>
    );
  }

  return (
    <main className={`app-shell${sidebarCollapsed ? ' sidebar-collapsed' : ''}`}>
      <Sidebar
        sessions={sessions}
        activeSessionId={activeSessionId}
        isDraftSession={isDraftSession}
        collapsed={sidebarCollapsed}
        currentUser={currentUser}
        agentInstances={agentInstances}
        loadingAgentInstances={loadingAgentInstances}
        activePane={workspacePaneMode}
        onOpenSettings={handleOpenSettings}
        onOpenMachines={() => void handleOpenMachines()}
        onLogout={handleLogout}
        onCreateSession={handleCreateSession}
        onSelectSession={handleSessionSelect}
        onToggleCollapse={() => setSidebarCollapsed((prev) => !prev)}
        sessionTitle={sessionTitle}
      />

      {workspacePaneMode === 'chat' ? (
        <ChatArea
          isDraftSession={isDraftSession}
          activeSession={activeSession}
          activeRunId={activeRunId}
          loadingMessages={loadingMessages}
          transcriptItems={visibleTranscriptItems}
          pendingPermissionDecisions={pendingPermissionDecisions}
          canCompose={canCompose}
          canSend={canSend}
          agentInstances={agentInstances}
          loadingAgentInstances={loadingAgentInstances}
          draftAgentInstance={draftAgentInstance}
          draftAgentInstanceId={draftAgentInstanceId}
          draftWorkspace={draftWorkspace}
          defaultAgentInstanceId={defaultAgentInstanceId}
          pairingCode={pairingCode}
          pairingBusy={pairingBusy}
          composer={composer}
          composerContextRefs={composerContextRefs}
          composerImages={composerImages}
          mentionOpen={mentionOpen}
          mentionLoading={mentionLoading}
          mentionQuery={mentionQuery}
          mentionResults={mentionResults}
          highlightedMentionIndex={highlightedMentionIndex}
          sending={sending}
          errorText={errorText}
          messageListRef={messageListRef}
          composerRef={composerRef}
          onCancel={handleCancel}
          onCreatePairingCode={handleCreatePairingCode}
          onRefreshAgentInstances={refreshAgentInventory}
          onPermissionDecision={handlePermissionDecision}
          onSubmit={handleSend}
          onDraftAgentInstanceChange={handleDraftAgentInstanceChange}
          onDraftWorkspaceChange={handleDraftWorkspaceChange}
          onSetDefaultAgentInstance={handleSetDefaultAgentInstance}
          onComposerChange={handleComposerChange}
          onComposerKeyDown={handleComposerKeyDown}
          onComposerSelectionChange={handleComposerSelectionChange}
          onSelectContextCandidate={handleSelectContextCandidate}
          onRemoveContextRef={handleRemoveContextRef}
          onImageFilesSelected={handleImageFilesSelected}
          onRemoveImage={handleRemoveImage}
          sessionTitle={sessionTitle}
        />
      ) : null}

      {workspacePaneMode === 'machines' ? (
        <MachinesView
          agentInstances={agentInstances}
          loadingAgentInstances={loadingAgentInstances}
          pairingDisplayName={pairingDisplayName}
          pairingCode={pairingCode}
          pairingBusy={pairingBusy}
          pairingErrorText={pairingErrorText}
          onPairingDisplayNameChange={setPairingDisplayName}
          onCreatePairingCode={handleCreatePairingCode}
          onDismissPairingCode={() => setPairingCode(null)}
          onRefreshAgentInstances={refreshAgentInventory}
          onOpenChat={handleOpenChatPane}
        />
      ) : null}

      {workspacePaneMode === 'settings' ? (
        <SettingsView
          currentUser={currentUser}
          section={settingsSection}
          onSectionChange={setSettingsSection}
          onOpenChat={handleOpenChatPane}
          onUpdateProfile={handleUpdateProfile}
          onChangePassword={handleSettingsPasswordChange}
          onLogout={handleLogout}
        />
      ) : null}
    </main>
  );
}
