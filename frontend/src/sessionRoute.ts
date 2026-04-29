export type SessionRoute = {
  sessionId: string;
};

const SESSION_ROUTE_PREFIX = 's';

export function parseSessionRoute(pathname: string): SessionRoute | null {
  const trimmed = pathname.replace(/^\/+|\/+$/g, '');
  if (!trimmed) {
    return null;
  }

  const segments = trimmed.split('/');
  if (segments.length !== 2 || segments[0] !== SESSION_ROUTE_PREFIX) {
    return null;
  }

  const sessionId = decodeURIComponent(segments[1] ?? '');
  if (!sessionId) {
    return null;
  }
  return { sessionId };
}

export function buildSessionPath(sessionId: string): string {
  return `/${SESSION_ROUTE_PREFIX}/${encodeURIComponent(sessionId)}`;
}
