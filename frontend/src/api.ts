const viteEnv = (import.meta as ImportMeta & { env?: Record<string, string | undefined> }).env;
export const API_BASE = viteEnv?.VITE_API_URL ?? 'http://localhost:8000';

export class ApiError extends Error {
  status: number;
  code: string | null;

  constructor(message: string, status: number, code: string | null = null) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
  }
}

function readNetworkError(error: unknown): ApiError {
  if (error instanceof ApiError) {
    return error;
  }

  if (error instanceof Error && error.name === 'AbortError') {
    return new ApiError('The request timed out.', 0, 'REQUEST_ABORTED');
  }

  return new ApiError(
    'Unable to reach the control plane. Check that the backend is running and the site can reach it.',
    0,
    'NETWORK_ERROR'
  );
}

function apiPath(path: string): string {
  return `${API_BASE}${path}`;
}

export function controlPlaneOrigin(): string {
  const trimmed = API_BASE.trim();
  if (trimmed.length > 0) {
    return trimmed;
  }
  if (typeof window !== 'undefined') {
    return window.location.origin;
  }
  return 'http://localhost:8080';
}

let csrfTokenCache: string | null = null;
let authRefreshPromise: Promise<AuthResponse> | null = null;

type RequestOptions = {
  csrf?: boolean;
  skipAuthRefresh?: boolean;
};

const AUTH_REFRESH_EXCLUDED_PATHS = new Set([
  '/api/auth/csrf',
  '/api/auth/login',
  '/api/auth/register',
  '/api/auth/forgot-password',
  '/api/auth/reset-password',
  '/api/auth/verify',
  '/api/auth/request-verify-token',
  '/api/auth/session',
  '/api/auth/refresh'
]);

function extractErrorDetails(value: unknown): { message: string | null; code: string | null } {
  if (typeof value === 'string') {
    const message = value.trim();
    return { message: message.length > 0 ? message : null, code: message.length > 0 ? message : null };
  }

  if (Array.isArray(value)) {
    const messages = value
      .map((item) => extractErrorDetails(item).message)
      .filter((message): message is string => Boolean(message));
    if (messages.length > 0) {
      return { message: messages.join('\n'), code: null };
    }
    return { message: null, code: null };
  }

  if (!value || typeof value !== 'object') {
    return { message: null, code: null };
  }

  const payload = value as Record<string, unknown>;
  if (typeof payload.reason === 'string' && payload.reason.trim().length > 0) {
    return {
      message: payload.reason.trim(),
      code: typeof payload.code === 'string' && payload.code.trim().length > 0 ? payload.code.trim() : null
    };
  }
  if (typeof payload.msg === 'string' && payload.msg.trim().length > 0) {
    const location = Array.isArray(payload.loc)
      ? payload.loc
          .filter((part): part is string | number => typeof part === 'string' || typeof part === 'number')
          .filter((part) => part !== 'body')
          .join('.')
      : '';
    return {
      message: location ? `${location}: ${payload.msg.trim()}` : payload.msg.trim(),
      code: typeof payload.type === 'string' ? payload.type : null
    };
  }

  const nested =
    extractErrorDetails(payload.detail).message !== null || extractErrorDetails(payload.detail).code !== null
      ? extractErrorDetails(payload.detail)
      : extractErrorDetails(payload.message).message !== null || extractErrorDetails(payload.message).code !== null
        ? extractErrorDetails(payload.message)
        : extractErrorDetails(payload.error);
  if (nested.message || nested.code) {
    return nested;
  }

  return {
    message: null,
    code: typeof payload.code === 'string' && payload.code.trim().length > 0 ? payload.code.trim() : null
  };
}

async function readError(response: Response): Promise<ApiError> {
  const fallback = `Request failed: ${response.status}`;
  try {
    const payload = (await response.json()) as { detail?: unknown };
    const details = extractErrorDetails(payload.detail);
    const payloadDetails = extractErrorDetails(payload);
    const message = details.message ?? payloadDetails.message;
    const code = details.code ?? payloadDetails.code;
    if (message) {
      return new ApiError(message, response.status, code);
    }
  } catch {
    // Ignore malformed JSON errors and fall back to plain status messaging.
  }
  return new ApiError(fallback, response.status);
}

function shouldAttemptAuthRefresh(path: string, options: RequestOptions, response: Response, hasRetried: boolean): boolean {
  return (
    !hasRetried &&
    !options.skipAuthRefresh &&
    response.status === 401 &&
    !AUTH_REFRESH_EXCLUDED_PATHS.has(path)
  );
}

async function sendRequest(
  path: string,
  init: RequestInit,
  options: RequestOptions,
  hasRetried: boolean
): Promise<Response> {
  let response: Response;
  try {
    response = await fetch(apiPath(path), {
      credentials: 'include',
      ...init
    });
  } catch (error) {
    throw readNetworkError(error);
  }
  if (!response.ok) {
    if (shouldAttemptAuthRefresh(path, options, response, hasRetried)) {
      await refreshAuthSession();
      return sendRequest(path, init, { ...options, skipAuthRefresh: true }, true);
    }
    throw await readError(response);
  }
  return response;
}

async function request(
  path: string,
  init: RequestInit = {},
  options: RequestOptions = {}
): Promise<Response> {
  const headers = new Headers(init.headers ?? {});
  if (options.csrf) {
    const csrfToken = await ensureCsrfToken();
    headers.set('X-CSRF-Token', csrfToken);
  }
  return sendRequest(
    path,
    {
      ...init,
      headers
    },
    options,
    false
  );
}

async function requestJson<T>(
  path: string,
  init: RequestInit = {},
  options: RequestOptions = {}
): Promise<T> {
  const response = await request(path, init, options);
  return (await response.json()) as T;
}

export async function ensureCsrfToken(force = false): Promise<string> {
  if (!force && csrfTokenCache) {
    return csrfTokenCache;
  }
  let response: Response;
  try {
    response = await fetch(apiPath('/api/auth/csrf'), {
      credentials: 'include'
    });
  } catch (error) {
    throw readNetworkError(error);
  }
  if (!response.ok) {
    throw await readError(response);
  }
  const payload = (await response.json()) as { csrf_token: string };
  csrfTokenCache = payload.csrf_token;
  return csrfTokenCache;
}

export type AuthUser = {
  id: string;
  email: string;
  is_active: boolean;
  is_superuser: boolean;
  is_verified: boolean;
  display_name: string | null;
  avatar_url: string | null;
  last_login_at: string | null;
  created_at: string | null;
  updated_at: string | null;
};

export type AuthResponse = {
  user: AuthUser;
  access_token_expires_in: number;
  refresh_token_expires_in: number;
};

export type AuthSessionState = {
  authenticated: boolean;
  user: AuthUser | null;
};

export function clearCachedAuthState(): void {
  csrfTokenCache = null;
  authRefreshPromise = null;
}

export type AgentInstance = {
  id: string;
  machine_id: string;
  agent: string;
  display_name: string | null;
  hostname: string | null;
  executor_name: string | null;
  version: string | null;
  status: string;
  workspace_roots: string[];
  last_seen_at: string | null;
  revoked_at: string | null;
  created_at: string | null;
  updated_at: string | null;
};

export type PairingCodeResponse = {
  pairing_code: string;
  agent: string;
  expires_at: string;
};

export type SessionPointer = {
  id: string;
  agent_instance_id: string;
  agent: string;
  display_name: string | null;
  hostname: string | null;
  executor_name: string | null;
  agent_instance_status: string;
  remote_session_id: string;
  title_hint: string | null;
  status: string;
  last_seen_at: string | null;
  created_at: string | null;
  updated_at: string | null;
};

export async function getCurrentUser(): Promise<AuthUser> {
  return requestJson<AuthUser>('/api/users/me');
}

export async function getAuthSession(): Promise<AuthSessionState> {
  return requestJson<AuthSessionState>('/api/auth/session');
}

export async function refreshAuthSession(): Promise<AuthResponse> {
  if (!authRefreshPromise) {
    authRefreshPromise = requestJson<AuthResponse>(
      '/api/auth/refresh',
      {
        method: 'POST'
      },
      { csrf: true, skipAuthRefresh: true }
    ).finally(() => {
      authRefreshPromise = null;
    });
  }
  return authRefreshPromise;
}

export async function restoreAuthSession(): Promise<AuthSessionState> {
  const sessionState = await getAuthSession();
  if (sessionState.authenticated && sessionState.user) {
    return sessionState;
  }
  try {
    const response = await refreshAuthSession();
    return {
      authenticated: true,
      user: response.user
    };
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) {
      return {
        authenticated: false,
        user: null
      };
    }
    throw error;
  }
}

export async function login(payload: { email: string; password: string }): Promise<AuthResponse> {
  return requestJson<AuthResponse>(
    '/api/auth/login',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    },
    { csrf: true }
  );
}

export async function registerUser(payload: {
  email: string;
  password: string;
  display_name?: string;
}): Promise<AuthUser> {
  return requestJson<AuthUser>(
    '/api/auth/register',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    },
    { csrf: true }
  );
}

export async function logout(): Promise<void> {
  try {
    await request(
      '/api/auth/logout',
      {
        method: 'POST'
      },
      { csrf: true }
    );
  } finally {
    clearCachedAuthState();
  }
}

export async function updateCurrentUserProfile(payload: {
  display_name?: string | null;
}): Promise<AuthUser> {
  return requestJson<AuthUser>(
    '/api/users/me',
    {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    },
    { csrf: true }
  );
}

export async function changePassword(payload: {
  current_password: string;
  new_password: string;
}): Promise<void> {
  await request(
    '/api/auth/change-password',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    },
    { csrf: true }
  );
}

export async function requestPasswordReset(email: string): Promise<void> {
  await request(
    '/api/auth/forgot-password',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email })
    },
    { csrf: true }
  );
}

export async function resetPassword(token: string, password: string): Promise<void> {
  await request(
    '/api/auth/reset-password',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token, password })
    },
    { csrf: true }
  );
}

export async function verifyEmail(token: string): Promise<void> {
  await request(
    '/api/auth/verify',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token })
    },
    { csrf: true }
  );
}

export async function requestVerifyToken(email: string): Promise<void> {
  await request(
    '/api/auth/request-verify-token',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email })
    },
    { csrf: true }
  );
}

export async function listAgentInstances(): Promise<AgentInstance[]> {
  return requestJson<AgentInstance[]>('/api/agent-instances');
}

export async function createPairingCode(payload: {
  agent: string;
  display_name?: string;
  ttl_minutes?: number;
}): Promise<PairingCodeResponse> {
  return requestJson<PairingCodeResponse>(
    '/api/machines/pairings',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    },
    { csrf: true }
  );
}

export async function listSessionPointers(): Promise<SessionPointer[]> {
  return requestJson<SessionPointer[]>('/api/session-pointers');
}

export type SessionStatus =
  | 'idle'
  | 'queued'
  | 'assigned'
  | 'running'
  | 'completed'
  | 'cancelled'
  | 'failed'
  | 'executor_disconnected';

export type Session = {
  session_id: string;
  title: string;
  cwd: string;
  agent_instance_id: string | null;
  status: SessionStatus;
  remote_updated_at: string | null;
  created_at: string;
  updated_at: string;
};

export type PromptTextItem = {
  type: 'text';
  text: string;
};

export type PromptImageItem = {
  type: 'image';
  image_url: string;
  name?: string | null;
  mime_type?: string | null;
  size?: number | null;
};

export type PromptResourceLinkItem = {
  type: 'resource_link';
  uri: string;
  name: string;
  relative_path: string;
  kind: 'file' | 'directory';
};

export type PromptItem = PromptTextItem | PromptImageItem | PromptResourceLinkItem;

export type TranscriptStep = {
  step_id: string;
  summary: string;
  detail: string | null;
  created_at: string;
  session_update: string | null;
  data: Record<string, unknown>;
};

export type UserTranscriptItem = {
  kind: 'user_message';
  message_id: string;
  session_id: string;
  run_id: string;
  content: string;
  items: PromptItem[];
  status: 'completed';
  created_at: string;
  updated_at: string;
};

export type AssistantTranscriptItem = {
  kind: 'assistant_segment';
  segment_id: string;
  session_id: string;
  run_id: string;
  segment_kind: 'message' | 'thought' | 'steps';
  content: string;
  status: 'completed' | 'streaming' | 'error' | 'cancelled';
  steps: TranscriptStep[];
  created_at: string;
  updated_at: string;
};

export type PermissionStatus = 'pending' | 'selected' | 'cancelled';
export type PermissionDecision = 'selected' | 'cancelled';

export type PermissionRequest = {
  approval_id: string;
  run_id: string;
  session_id: string;
  status: PermissionStatus;
  tool_call: Record<string, unknown>;
  options: Array<Record<string, unknown>>;
  decision: PermissionDecision | null;
  option_id: string | null;
  created_at: string;
  decided_at: string | null;
};

export type PermissionTranscriptItem = PermissionRequest & {
  kind: 'permission_request';
};

export type TranscriptItem = UserTranscriptItem | AssistantTranscriptItem | PermissionTranscriptItem;

export type ContextCandidate = {
  kind: 'file' | 'directory';
  name: string;
  relative_path: string;
  uri: string;
};

export async function listSessions(): Promise<{ items: Session[] }> {
  return requestJson<{ items: Session[] }>('/v1/sessions');
}

export async function createSession(payload: {
  agent_instance_id: string;
  title?: string;
  cwd?: string;
}): Promise<{
  session_id: string;
  title: string;
  cwd: string;
  agent_instance_id: string;
  status: SessionStatus;
  created_at: string;
}> {
  return requestJson(
    '/v1/sessions',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    }
  );
}

export async function getSessionTranscript(sessionId: string): Promise<{ session: Session; items: TranscriptItem[] }> {
  return requestJson<{ session: Session; items: TranscriptItem[] }>(
    `/v1/sessions/${encodeURIComponent(sessionId)}/transcript`
  );
}

export async function sendMessage(
  sessionId: string,
  payload: { content: string; items: PromptItem[] }
): Promise<{
  run_id: string;
  user_message_id: string;
  assistant_message_id: string;
  stream_url: string;
}> {
  return requestJson(`/v1/sessions/${encodeURIComponent(sessionId)}/messages`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  });
}

export async function cancelRun(runId: string): Promise<void> {
  await request(`/v1/runs/${runId}/cancel`, {
    method: 'POST'
  });
}

export function createRunEventSource(runId: string): EventSource {
  return new EventSource(apiPath(`/v1/runs/${runId}/events`), { withCredentials: true });
}

export async function decideRunPermission(
  runId: string,
  approvalId: string,
  decision: PermissionDecision,
  optionId?: string
): Promise<PermissionRequest> {
  return requestJson<PermissionRequest>(`/v1/runs/${runId}/permissions/${approvalId}/decision`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      decision,
      option_id: decision === 'selected' ? optionId ?? null : null
    })
  });
}

export async function searchContext(
  query: string,
  options?: {
    cwd?: string;
    agentInstanceId?: string | null;
    limit?: number;
  }
): Promise<{ items: ContextCandidate[] }> {
  const params = new URLSearchParams();
  params.set('q', query);
  params.set('limit', String(options?.limit ?? 20));
  if (options?.cwd) {
    params.set('cwd', options.cwd);
  }
  if (options?.agentInstanceId) {
    params.set('agent_instance_id', options.agentInstanceId);
  }
  return requestJson<{ items: ContextCandidate[] }>(`/v1/context/search?${params.toString()}`);
}
