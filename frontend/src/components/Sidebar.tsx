import { useEffect, useMemo, useRef, useState } from 'react';
import {
  ChevronDown,
  ChevronUp,
  ChevronsLeft,
  ChevronsRight,
  LogOut,
  MonitorSmartphone,
  Plus,
  Settings
} from 'lucide-react';
import { AgentInstance, AuthUser, Session } from '../api';
import pocageLogo from '../assets/brand/pocage-logo.svg';
import { buildSessionPath } from '../sessionRoute';

type WorkspaceView = 'chat' | 'machines' | 'settings';
const ALL_SESSIONS_FILTER = 'all';
const SESSION_FILTER_STORAGE_KEY = 'pocage.sessionListFilter';

type SidebarProps = {
  sessions: Session[];
  activeSessionId: string | null;
  isDraftSession: boolean;
  collapsed: boolean;
  currentUser: AuthUser;
  agentInstances: AgentInstance[];
  loadingAgentInstances: boolean;
  activePane: WorkspaceView;
  onOpenSettings: () => void;
  onOpenMachines: () => void;
  onLogout: () => Promise<void>;
  onCreateSession: () => Promise<void>;
  onSelectSession: (sessionId: string) => Promise<void>;
  onToggleCollapse: () => void;
  sessionTitle: (session: Session) => string;
};

function userDisplayText(user: AuthUser): string {
  return user.display_name?.trim() || user.email;
}

function userInitials(user: AuthUser): string {
  const source = user.display_name?.trim() || user.email.trim();
  const parts = source
    .split(/[\s@._-]+/)
    .map((part) => part.trim())
    .filter(Boolean);
  if (parts.length === 0) {
    return 'PC';
  }
  return parts
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase() ?? '')
    .join('');
}

function unavailableSessionStatusLabel(status: string | null | undefined): string {
  if (status === 'revoked') {
    return 'revoked';
  }
  if (status === 'paired' || status === 'offline') {
    return 'offline';
  }
  return 'unavailable';
}

function machineStatusLabel(status: string | null | undefined): string {
  if (status === 'online') {
    return 'online';
  }
  return unavailableSessionStatusLabel(status);
}

function machineDisplayText(agentInstance: AgentInstance): string {
  return (
    agentInstance.display_name?.trim() ||
    agentInstance.hostname?.trim() ||
    agentInstance.executor_name?.trim() ||
    'Machine'
  );
}

function readStoredSessionFilter(): string {
  if (typeof window === 'undefined') {
    return ALL_SESSIONS_FILTER;
  }
  try {
    const value = window.localStorage.getItem(SESSION_FILTER_STORAGE_KEY);
    return value && value.trim().length > 0 ? value : ALL_SESSIONS_FILTER;
  } catch {
    return ALL_SESSIONS_FILTER;
  }
}

function storeSessionFilter(value: string): void {
  if (typeof window === 'undefined') {
    return;
  }
  try {
    if (value === ALL_SESSIONS_FILTER) {
      window.localStorage.removeItem(SESSION_FILTER_STORAGE_KEY);
    } else {
      window.localStorage.setItem(SESSION_FILTER_STORAGE_KEY, value);
    }
  } catch {
    // Ignore storage write failures and keep the in-memory filter value.
  }
}

export function Sidebar({
  sessions,
  activeSessionId,
  isDraftSession,
  collapsed,
  currentUser,
  agentInstances,
  loadingAgentInstances,
  activePane,
  onOpenSettings,
  onOpenMachines,
  onLogout,
  onCreateSession,
  onSelectSession,
  onToggleCollapse,
  sessionTitle
}: SidebarProps) {
  const [menuOpen, setMenuOpen] = useState(false);
  const [sessionFilterId, setSessionFilterId] = useState(() => readStoredSessionFilter());
  const menuRef = useRef<HTMLDivElement | null>(null);
  const userName = userDisplayText(currentUser);
  const userInitialsText = useMemo(() => userInitials(currentUser), [currentUser]);
  const onlineMachineCount = useMemo(
    () => agentInstances.filter((agentInstance) => agentInstance.status === 'online').length,
    [agentInstances]
  );
  const machineSummary = loadingAgentInstances
    ? 'Loading machines…'
    : agentInstances.length === 0
      ? 'No machines paired'
      : `${onlineMachineCount} online · ${agentInstances.length} machines`;
  const agentInstancesById = useMemo(
    () => new Map(agentInstances.map((agentInstance) => [agentInstance.id, agentInstance])),
    [agentInstances]
  );
  const sortedAgentInstances = useMemo(
    () =>
      [...agentInstances].sort((left, right) => {
        if (left.status === 'online' && right.status !== 'online') {
          return -1;
        }
        if (left.status !== 'online' && right.status === 'online') {
          return 1;
        }
        return machineDisplayText(left).localeCompare(machineDisplayText(right), undefined, { sensitivity: 'base' });
      }),
    [agentInstances]
  );
  const showSessionFilter = sortedAgentInstances.length > 1;
  const filteredSessions = useMemo(
    () =>
      sessionFilterId === ALL_SESSIONS_FILTER
        ? sessions
        : sessions.filter((session) => session.agent_instance_id === sessionFilterId),
    [sessionFilterId, sessions]
  );
  const showMachineLabelInSessionList = sessionFilterId === ALL_SESSIONS_FILTER && sortedAgentInstances.length > 1;

  useEffect(() => {
    if (!menuOpen) {
      return undefined;
    }

    function handlePointerDown(event: MouseEvent): void {
      if (menuRef.current && !menuRef.current.contains(event.target as Node)) {
        setMenuOpen(false);
      }
    }

    function handleKeyDown(event: KeyboardEvent): void {
      if (event.key === 'Escape') {
        setMenuOpen(false);
      }
    }

    document.addEventListener('mousedown', handlePointerDown);
    document.addEventListener('keydown', handleKeyDown);
    return () => {
      document.removeEventListener('mousedown', handlePointerDown);
      document.removeEventListener('keydown', handleKeyDown);
    };
  }, [menuOpen]);

  useEffect(() => {
    storeSessionFilter(sessionFilterId);
  }, [sessionFilterId]);

  useEffect(() => {
    if (loadingAgentInstances || sessionFilterId === ALL_SESSIONS_FILTER) {
      return;
    }
    if (agentInstancesById.has(sessionFilterId)) {
      return;
    }
    setSessionFilterId(ALL_SESSIONS_FILTER);
  }, [agentInstancesById, loadingAgentInstances, sessionFilterId]);

  function closeMenu(): void {
    setMenuOpen(false);
  }

  return (
    <aside className={`sidebar${collapsed ? ' collapsed' : ''}`}>
      <header className="sidebar-header">
        <div className={`sidebar-brand${collapsed ? ' collapsed' : ''}`} aria-label="pocage">
          <img className="sidebar-brand-logo" src={pocageLogo} alt="pocage logo" />
          {!collapsed ? <span className="sidebar-brand-text">pocage</span> : null}
        </div>
        <button
          className={`collapse-button${collapsed ? ' collapsed' : ''}`}
          type="button"
          aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
          aria-pressed={collapsed}
          onClick={() => {
            closeMenu();
            onToggleCollapse();
          }}
        >
          {collapsed ? <ChevronsRight size={16} strokeWidth={1.9} /> : <ChevronsLeft size={16} strokeWidth={1.9} />}
        </button>
      </header>

      <div className="sidebar-actions">
        <button
          className={`new-session-button${collapsed ? ' compact' : ''}`}
          type="button"
          onClick={() => {
            closeMenu();
            void onCreateSession();
          }}
          aria-label="New session"
          title="New session"
        >
          <Plus className="new-session-icon" size={16} strokeWidth={2} aria-hidden="true" />
          {!collapsed ? <span>New session</span> : null}
        </button>
      </div>

      {!collapsed ? (
        <>
          <p className="sidebar-section-title">Sessions</p>
          {showSessionFilter ? (
            <div className="sidebar-session-filter">
              <select
                className="sidebar-filter-select"
                aria-label="Filter sessions by machine"
                value={sessionFilterId}
                onChange={(event) => {
                  setSessionFilterId(event.target.value);
                }}
              >
                <option value={ALL_SESSIONS_FILTER}>All machines</option>
                {sortedAgentInstances.map((agentInstance) => (
                  <option key={agentInstance.id} value={agentInstance.id}>
                    {`${machineDisplayText(agentInstance)} · ${machineStatusLabel(agentInstance.status)}`}
                  </option>
                ))}
              </select>
            </div>
          ) : null}
        </>
      ) : null}

      {!collapsed ? (
        <div className="session-list">
          {filteredSessions.length === 0 ? (
            <p className="sidebar-empty-copy">
              {sessionFilterId === ALL_SESSIONS_FILTER ? 'No sessions yet.' : 'No sessions on this machine.'}
            </p>
          ) : null}
          {filteredSessions.map((session) => {
            const title = sessionTitle(session);
            const agentInstance =
              session.agent_instance_id && !loadingAgentInstances
                ? agentInstancesById.get(session.agent_instance_id) ?? null
                : null;
            const sessionAvailable =
              loadingAgentInstances ||
              (agentInstance !== null
                ? agentInstance.status === 'online'
                : Boolean(session.agent_instance_id && agentInstancesById.has(session.agent_instance_id)));
            const unavailableLabel = sessionAvailable
              ? null
              : unavailableSessionStatusLabel(agentInstance?.status ?? null);
            const machineLabel =
              showMachineLabelInSessionList && session.agent_instance_id
                ? agentInstance
                  ? machineDisplayText(agentInstance)
                  : 'Unavailable machine'
                : null;
            const className = `session-card${activeSessionId === session.session_id && !isDraftSession ? ' active' : ''}${sessionAvailable ? '' : ' unavailable'}`;
            const content = (
              <span className="session-row">
                <span className="session-dot" aria-hidden="true" />
                <span className="session-copy">
                  <span className="session-title">{title}</span>
                  {machineLabel || unavailableLabel ? (
                    <span className="session-meta-row">
                      {machineLabel ? <span className="session-machine-label">{machineLabel}</span> : null}
                      {unavailableLabel ? <span className="session-status-badge">{unavailableLabel}</span> : null}
                    </span>
                  ) : null}
                </span>
              </span>
            );

            if (!sessionAvailable) {
              return (
                <div
                  key={session.session_id}
                  className={className}
                  aria-label={`${title} (${unavailableLabel})`}
                  title={`${title} (${unavailableLabel})`}
                >
                  {content}
                </div>
              );
            }

            return (
              <a
                key={session.session_id}
                href={buildSessionPath(session.session_id)}
                className={className}
                aria-label={title}
                title={title}
                onClick={(event) => {
                  event.preventDefault();
                  closeMenu();
                  void onSelectSession(session.session_id);
                }}
              >
                {content}
              </a>
            );
          })}
        </div>
      ) : (
        <div className="sidebar-spacer" aria-hidden="true" />
      )}

      <div className={`sidebar-footer${collapsed ? ' compact' : ''}`}>
        <div className="sidebar-user-shell" ref={menuRef}>
          {menuOpen ? (
            <div className={`sidebar-user-menu${collapsed ? ' compact' : ''}`}>
              <div className="sidebar-menu-profile">
                {currentUser.avatar_url ? (
                  <img className="sidebar-user-avatar-image" src={currentUser.avatar_url} alt={userName} />
                ) : (
                  <span className="sidebar-user-avatar">{userInitialsText}</span>
                )}
                <div className="sidebar-menu-profile-copy">
                  <p className="sidebar-menu-profile-name">{userName}</p>
                  <p className="sidebar-menu-profile-meta">{currentUser.email}</p>
                  <p className="sidebar-menu-profile-meta">{machineSummary}</p>
                </div>
              </div>

              <div className="sidebar-menu-group">
                <button
                  className={`sidebar-menu-item${activePane === 'settings' ? ' active' : ''}`}
                  type="button"
                  onClick={() => {
                    closeMenu();
                    onOpenSettings();
                  }}
                >
                  <span className="sidebar-menu-item-start">
                    <Settings className="sidebar-menu-icon" size={16} strokeWidth={1.85} aria-hidden="true" />
                    <span className="sidebar-menu-item-copy">
                      <span className="sidebar-menu-item-label">Settings</span>
                      <span className="sidebar-menu-item-meta">Profile and account preferences.</span>
                    </span>
                  </span>
                </button>

                <button
                  className={`sidebar-menu-item${activePane === 'machines' ? ' active' : ''}`}
                  type="button"
                  onClick={() => {
                    closeMenu();
                    onOpenMachines();
                  }}
                >
                  <span className="sidebar-menu-item-start">
                    <MonitorSmartphone className="sidebar-menu-icon" size={16} strokeWidth={1.85} aria-hidden="true" />
                    <span className="sidebar-menu-item-copy">
                      <span className="sidebar-menu-item-label">Machines</span>
                      <span className="sidebar-menu-item-meta">{machineSummary}</span>
                    </span>
                  </span>
                </button>
              </div>

              <div className="sidebar-menu-group">
                <button
                  className="sidebar-menu-item destructive"
                  type="button"
                  onClick={() => {
                    closeMenu();
                    void onLogout();
                  }}
                >
                  <span className="sidebar-menu-item-start">
                    <LogOut className="sidebar-menu-icon" size={16} strokeWidth={1.85} aria-hidden="true" />
                    <span className="sidebar-menu-item-copy">
                      <span className="sidebar-menu-item-label">Log out</span>
                      <span className="sidebar-menu-item-meta">End the current control plane session.</span>
                    </span>
                  </span>
                </button>
              </div>
            </div>
          ) : null}

          <button
            className={`sidebar-user-trigger${collapsed ? ' compact' : ''}`}
            type="button"
            aria-haspopup="menu"
            aria-expanded={menuOpen}
            onClick={() => setMenuOpen((current) => !current)}
          >
            {currentUser.avatar_url ? (
              <img className="sidebar-user-avatar-image" src={currentUser.avatar_url} alt={userName} />
            ) : (
              <span className="sidebar-user-avatar">{userInitialsText}</span>
            )}

            {!collapsed ? (
              <>
                <span className="sidebar-user-copy">
                  <span className="sidebar-user-name">{userName}</span>
                  <span className="sidebar-user-meta">{machineSummary}</span>
                </span>
                {menuOpen ? (
                  <ChevronUp className="sidebar-user-chevron" size={16} strokeWidth={1.85} aria-hidden="true" />
                ) : (
                  <ChevronDown className="sidebar-user-chevron" size={16} strokeWidth={1.85} aria-hidden="true" />
                )}
              </>
            ) : null}
          </button>
        </div>
      </div>
    </aside>
  );
}
