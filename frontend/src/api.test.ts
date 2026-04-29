import assert from 'node:assert/strict';

import {
  ApiError,
  clearCachedAuthState,
  listAgentInstances,
  listSessions,
  restoreAuthSession
} from './api.ts';

type FetchCall = {
  path: string;
  headers: Headers;
};

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      'Content-Type': 'application/json'
    }
  });
}

function requestPath(input: RequestInfo | URL): string {
  const url =
    typeof input === 'string'
      ? input
      : input instanceof URL
        ? input.toString()
        : input.url;
  return new URL(url).pathname;
}

function installFetchMock(handler: (input: RequestInfo | URL, init?: RequestInit) => Promise<Response> | Response): FetchCall[] {
  const calls: FetchCall[] = [];
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    calls.push({
      path: requestPath(input),
      headers: new Headers(init?.headers ?? {})
    });
    return handler(input, init);
  }) as typeof fetch;
  return calls;
}

const originalFetch = globalThis.fetch;

try {
  {
    clearCachedAuthState();
    let protectedRequestCount = 0;
    const calls = installFetchMock((input, init) => {
      const path = requestPath(input);
      if (path === '/api/auth/csrf') {
        return jsonResponse(200, { csrf_token: 'csrf-token-1' });
      }
      if (path === '/api/auth/refresh') {
        assert.equal(new Headers(init?.headers ?? {}).get('X-CSRF-Token'), 'csrf-token-1');
        return jsonResponse(200, {
          user: {
            id: 'user-1',
            email: 'user@example.com',
            is_active: true,
            is_superuser: false,
            is_verified: true,
            display_name: 'User',
            avatar_url: null,
            last_login_at: null,
            created_at: null,
            updated_at: null
          },
          access_token_expires_in: 900,
          refresh_token_expires_in: 2_592_000
        });
      }
      if (path === '/api/agent-instances') {
        protectedRequestCount += 1;
        if (protectedRequestCount === 1) {
          return jsonResponse(401, { detail: 'Not authenticated.' });
        }
        return jsonResponse(200, []);
      }
      throw new Error(`Unexpected request path: ${path}`);
    });

    const items = await listAgentInstances();
    assert.deepEqual(items, []);
    assert.deepEqual(calls.map((call) => call.path), [
      '/api/agent-instances',
      '/api/auth/csrf',
      '/api/auth/refresh',
      '/api/agent-instances'
    ]);
  }

  {
    clearCachedAuthState();
    const calls = installFetchMock((input, init) => {
      const path = requestPath(input);
      if (path === '/api/auth/session') {
        return jsonResponse(200, { authenticated: false, user: null });
      }
      if (path === '/api/auth/csrf') {
        return jsonResponse(200, { csrf_token: 'csrf-token-2' });
      }
      if (path === '/api/auth/refresh') {
        assert.equal(new Headers(init?.headers ?? {}).get('X-CSRF-Token'), 'csrf-token-2');
        return jsonResponse(200, {
          user: {
            id: 'user-2',
            email: 'restore@example.com',
            is_active: true,
            is_superuser: false,
            is_verified: true,
            display_name: null,
            avatar_url: null,
            last_login_at: null,
            created_at: null,
            updated_at: null
          },
          access_token_expires_in: 900,
          refresh_token_expires_in: 2_592_000
        });
      }
      throw new Error(`Unexpected request path: ${path}`);
    });

    const session = await restoreAuthSession();
    assert.equal(session.authenticated, true);
    assert.equal(session.user?.email, 'restore@example.com');
    assert.deepEqual(calls.map((call) => call.path), ['/api/auth/session', '/api/auth/csrf', '/api/auth/refresh']);
  }

  {
    clearCachedAuthState();
    let refreshRequestCount = 0;
    installFetchMock((input, init) => {
      const path = requestPath(input);
      if (path === '/api/auth/csrf') {
        return jsonResponse(200, { csrf_token: 'csrf-token-3' });
      }
      if (path === '/api/auth/refresh') {
        refreshRequestCount += 1;
        assert.equal(new Headers(init?.headers ?? {}).get('X-CSRF-Token'), 'csrf-token-3');
        return jsonResponse(200, {
          user: {
            id: 'user-3',
            email: 'parallel@example.com',
            is_active: true,
            is_superuser: false,
            is_verified: true,
            display_name: null,
            avatar_url: null,
            last_login_at: null,
            created_at: null,
            updated_at: null
          },
          access_token_expires_in: 900,
          refresh_token_expires_in: 2_592_000
        });
      }
      if (path === '/api/agent-instances') {
        const authorizationAttempt = new Headers(init?.headers ?? {}).get('X-CSRF-Token');
        assert.equal(authorizationAttempt, null);
        return refreshRequestCount === 0 ? jsonResponse(401, { detail: 'Not authenticated.' }) : jsonResponse(200, []);
      }
      if (path === '/v1/sessions') {
        return refreshRequestCount === 0
          ? jsonResponse(401, { detail: 'Not authenticated.' })
          : jsonResponse(200, { items: [] });
      }
      throw new Error(`Unexpected request path: ${path}`);
    });

    const [agentInstances, sessions] = await Promise.all([listAgentInstances(), listSessions()]);
    assert.deepEqual(agentInstances, []);
    assert.deepEqual(sessions, { items: [] });
    assert.equal(refreshRequestCount, 1);
  }

  {
    clearCachedAuthState();
    installFetchMock((input) => {
      const path = requestPath(input);
      if (path === '/api/auth/csrf') {
        return jsonResponse(200, { csrf_token: 'csrf-token-4' });
      }
      if (path === '/api/auth/refresh') {
        return jsonResponse(401, { detail: 'Refresh token is invalid.' });
      }
      if (path === '/api/agent-instances') {
        return jsonResponse(401, { detail: 'Not authenticated.' });
      }
      throw new Error(`Unexpected request path: ${path}`);
    });

    await assert.rejects(
      () => listAgentInstances(),
      (error: unknown) =>
        error instanceof ApiError &&
        error.status === 401 &&
        error.message === 'Refresh token is invalid.'
    );
  }
} finally {
  globalThis.fetch = originalFetch;
  clearCachedAuthState();
}

console.log('api tests passed');
