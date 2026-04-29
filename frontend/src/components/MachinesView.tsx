import { ArrowLeft, RefreshCw } from 'lucide-react';
import { AgentInstance, PairingCodeResponse, controlPlaneOrigin } from '../api';

type MachinesViewProps = {
  agentInstances: AgentInstance[];
  loadingAgentInstances: boolean;
  pairingDisplayName: string;
  pairingCode: PairingCodeResponse | null;
  pairingBusy: boolean;
  pairingErrorText: string | null;
  onPairingDisplayNameChange: (value: string) => void;
  onCreatePairingCode: () => Promise<void>;
  onDismissPairingCode: () => void;
  onRefreshAgentInstances: () => Promise<unknown>;
  onOpenChat: () => void;
};

function formatDateTime(value: string | null): string {
  if (!value) {
    return 'Never seen';
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }
  return parsed.toLocaleString();
}

function statusLabel(status: string): string {
  if (status === 'online') {
    return 'online';
  }
  if (status === 'paired') {
    return 'paired';
  }
  if (status === 'revoked') {
    return 'revoked';
  }
  return status || 'offline';
}

function machineDisplayText(agentInstance: AgentInstance): string {
  return (
    agentInstance.display_name?.trim() ||
    agentInstance.hostname?.trim() ||
    agentInstance.executor_name?.trim() ||
    'Machine'
  );
}

export function MachinesView({
  agentInstances,
  loadingAgentInstances,
  pairingDisplayName,
  pairingCode,
  pairingBusy,
  pairingErrorText,
  onPairingDisplayNameChange,
  onCreatePairingCode,
  onDismissPairingCode,
  onRefreshAgentInstances,
  onOpenChat
}: MachinesViewProps) {
  const onlineMachineCount = agentInstances.filter((agentInstance) => agentInstance.status === 'online').length;

  return (
    <section className="workspace">
      <header className="workspace-header">
        <div>
          <p className="workspace-eyebrow">Machines</p>
          <h2 className="workspace-title">Manage your machines</h2>
          <p className="workspace-context">Review paired machines and generate one-time CLI pairing codes.</p>
        </div>
        <button className="workspace-action-button" type="button" onClick={onOpenChat}>
          <ArrowLeft size={16} strokeWidth={1.9} aria-hidden="true" />
          <span>Back to chat</span>
        </button>
      </header>

      <div className="panel-scroll">
        <div className="panel-content">
          <section className="panel-card panel-stack">
            <div className="panel-section-head">
              <div>
                <p className="panel-eyebrow">Inventory</p>
                <h3 className="panel-title">Paired machines</h3>
                <p className="panel-copy">
                  {loadingAgentInstances
                    ? 'Loading machine inventory…'
                    : agentInstances.length === 0
                      ? 'No paired machines yet.'
                      : `${onlineMachineCount} online · ${agentInstances.length} total`}
                </p>
              </div>
              <button className="workspace-action-button" type="button" onClick={() => void onRefreshAgentInstances()}>
                <RefreshCw size={16} strokeWidth={1.9} aria-hidden="true" />
                <span>Refresh now</span>
              </button>
            </div>

            {loadingAgentInstances ? <p className="sidebar-empty-copy">Loading machines…</p> : null}
            {!loadingAgentInstances && agentInstances.length === 0 ? (
              <p className="sidebar-empty-copy">
                Generate a pairing code below, run it on a machine, and this page will refresh automatically once the daemon is online.
              </p>
            ) : null}

            {agentInstances.length > 0 ? (
              <div className="agent-list">
                {agentInstances.map((agentInstance) => (
                  <article key={agentInstance.id} className="agent-card machine-view-card">
                    <div className="agent-card-row">
                      <span className="agent-card-title">{machineDisplayText(agentInstance)}</span>
                      <span className={`agent-status ${statusLabel(agentInstance.status)}`}>{statusLabel(agentInstance.status)}</span>
                    </div>
                    <p className="agent-card-meta">{agentInstance.agent}</p>
                    {agentInstance.workspace_roots.length > 0 ? (
                      <p className="agent-card-meta">{agentInstance.workspace_roots.join(' · ')}</p>
                    ) : null}
                    <p className="agent-card-meta">Last seen {formatDateTime(agentInstance.last_seen_at)}</p>
                  </article>
                ))}
              </div>
            ) : null}
          </section>

          <section className="panel-card panel-stack">
            <div className="panel-section-head">
              <div>
                <p className="panel-eyebrow">Pair a machine</p>
                <h3 className="panel-title">Generate a one-time code</h3>
                <p className="panel-copy">Run the command below on your Linux or macOS machine to pair it with pocage.</p>
              </div>
            </div>

            <label className="panel-field">
              <span className="panel-field-label">Machine display name</span>
              <input
                className="sidebar-input"
                type="text"
                placeholder="Optional machine display name"
                value={pairingDisplayName}
                onChange={(event) => onPairingDisplayNameChange(event.target.value)}
              />
            </label>

            <div className="panel-actions">
              <button className="sidebar-primary-button" type="button" disabled={pairingBusy} onClick={() => void onCreatePairingCode()}>
                {pairingBusy ? 'Generating…' : 'Generate pairing code'}
              </button>
            </div>

            {pairingErrorText ? <p className="sidebar-inline-error">{pairingErrorText}</p> : null}

            {pairingCode ? (
              <div className="pairing-code-card">
                <div className="pairing-code-row">
                  <span className="pairing-code-label">Code</span>
                  <button className="sidebar-ghost-button" type="button" onClick={onDismissPairingCode}>
                    Clear
                  </button>
                </div>
                <code className="pairing-code-value">{pairingCode.pairing_code}</code>
                <p className="pairing-code-meta">Expires {formatDateTime(pairingCode.expires_at)}</p>
                <code className="pairing-command">
                  uv run pocage pair --agent {pairingCode.agent} --api-url {controlPlaneOrigin()} --pairing-code{' '}
                  {pairingCode.pairing_code}
                </code>
                <code className="pairing-command">uv run pocage --agent {pairingCode.agent}</code>
              </div>
            ) : null}
          </section>
        </div>
      </div>
    </section>
  );
}
