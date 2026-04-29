import { FormEvent, useEffect, useMemo, useState } from 'react';
import { ArrowLeft, LogOut, Settings, UserRound } from 'lucide-react';
import { AuthUser } from '../api';

const MIN_PASSWORD_LENGTH = 8;

export type SettingsSection = 'profile' | 'account';

type SettingsViewProps = {
  currentUser: AuthUser;
  section: SettingsSection;
  onSectionChange: (section: SettingsSection) => void;
  onOpenChat: () => void;
  onUpdateProfile: (displayName: string) => Promise<AuthUser>;
  onChangePassword: (currentPassword: string, nextPassword: string) => Promise<void>;
  onLogout: () => Promise<void>;
};

function formatDateTime(value: string | null): string {
  if (!value) {
    return 'Unknown';
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }
  return parsed.toLocaleString();
}

export function SettingsView({
  currentUser,
  section,
  onSectionChange,
  onOpenChat,
  onUpdateProfile,
  onChangePassword,
  onLogout
}: SettingsViewProps) {
  const [displayName, setDisplayName] = useState(currentUser.display_name ?? '');
  const [profileBusy, setProfileBusy] = useState(false);
  const [profileError, setProfileError] = useState<string | null>(null);
  const [profileInfo, setProfileInfo] = useState<string | null>(null);
  const [currentPassword, setCurrentPassword] = useState('');
  const [nextPassword, setNextPassword] = useState('');
  const [accountBusy, setAccountBusy] = useState(false);
  const [accountError, setAccountError] = useState<string | null>(null);

  const normalizedCurrentDisplayName = useMemo(() => currentUser.display_name?.trim() ?? '', [currentUser.display_name]);
  const profileDirty = displayName.trim() !== normalizedCurrentDisplayName;

  useEffect(() => {
    setDisplayName(currentUser.display_name ?? '');
  }, [currentUser.display_name, currentUser.id]);

  async function handleProfileSubmit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (profileBusy || !profileDirty) {
      return;
    }
    setProfileBusy(true);
    setProfileError(null);
    setProfileInfo(null);
    try {
      const updated = await onUpdateProfile(displayName);
      setDisplayName(updated.display_name ?? '');
      setProfileInfo('Profile updated.');
    } catch (error) {
      setProfileError(error instanceof Error ? error.message : 'Failed to update profile');
    } finally {
      setProfileBusy(false);
    }
  }

  async function handleAccountSubmit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (accountBusy) {
      return;
    }
    if (nextPassword.length < MIN_PASSWORD_LENGTH) {
      setAccountError(`Password must be at least ${MIN_PASSWORD_LENGTH} characters long.`);
      return;
    }

    setAccountBusy(true);
    setAccountError(null);
    try {
      await onChangePassword(currentPassword, nextPassword);
    } catch (error) {
      setAccountError(error instanceof Error ? error.message : 'Failed to update password');
    } finally {
      setAccountBusy(false);
    }
  }

  return (
    <section className="workspace">
      <header className="workspace-header">
        <div>
          <p className="workspace-eyebrow">Settings</p>
          <h2 className="workspace-title">Manage your account</h2>
          <p className="workspace-context">Update your profile details and account security from one place.</p>
        </div>
        <button className="workspace-action-button" type="button" onClick={onOpenChat}>
          <ArrowLeft size={16} strokeWidth={1.9} aria-hidden="true" />
          <span>Back to chat</span>
        </button>
      </header>

      <div className="panel-scroll">
        <div className="panel-content">
          <div className="settings-shell">
            <aside className="settings-nav">
              <button
                className={`settings-nav-button${section === 'profile' ? ' active' : ''}`}
                type="button"
                onClick={() => onSectionChange('profile')}
              >
                <UserRound size={16} strokeWidth={1.85} aria-hidden="true" />
                <span>Profile</span>
              </button>
              <button
                className={`settings-nav-button${section === 'account' ? ' active' : ''}`}
                type="button"
                onClick={() => onSectionChange('account')}
              >
                <Settings size={16} strokeWidth={1.85} aria-hidden="true" />
                <span>Account</span>
              </button>
            </aside>

            <div className="settings-panel">
              {section === 'profile' ? (
                <section className="panel-card panel-stack">
                  <div className="panel-section-head">
                    <div>
                      <p className="panel-eyebrow">Profile</p>
                      <h3 className="panel-title">Public identity</h3>
                      <p className="panel-copy">Choose how your name appears across the pocage control plane.</p>
                    </div>
                  </div>

                  <form className="settings-form" onSubmit={(event) => void handleProfileSubmit(event)}>
                    <label className="panel-field">
                      <span className="panel-field-label">Display name</span>
                      <input
                        className="settings-input"
                        type="text"
                        value={displayName}
                        placeholder="Enter your display name"
                        onChange={(event) => {
                          setDisplayName(event.target.value);
                          setProfileError(null);
                          setProfileInfo(null);
                        }}
                      />
                      <span className="panel-field-hint">Optional. Leave empty to fall back to your email address.</span>
                    </label>

                    <div className="panel-field readonly">
                      <span className="panel-field-label">Email</span>
                      <div className="readonly-field">{currentUser.email}</div>
                      <span className="panel-field-hint">Email changes are not part of this version.</span>
                    </div>

                    <div className="panel-field readonly">
                      <span className="panel-field-label">Created</span>
                      <div className="readonly-field">{formatDateTime(currentUser.created_at)}</div>
                    </div>

                    {profileError ? <p className="sidebar-inline-error">{profileError}</p> : null}
                    {profileInfo ? <p className="panel-inline-info">{profileInfo}</p> : null}

                    <div className="panel-actions">
                      <button className="sidebar-primary-button" type="submit" disabled={!profileDirty || profileBusy}>
                        {profileBusy ? 'Saving…' : 'Save profile'}
                      </button>
                    </div>
                  </form>
                </section>
              ) : null}

              {section === 'account' ? (
                <section className="panel-card panel-stack">
                  <div className="panel-section-head">
                    <div>
                      <p className="panel-eyebrow">Account</p>
                      <h3 className="panel-title">Security</h3>
                      <p className="panel-copy">Change your password and manage the current browser session.</p>
                    </div>
                  </div>

                  <form className="settings-form" onSubmit={(event) => void handleAccountSubmit(event)}>
                    <label className="panel-field">
                      <span className="panel-field-label">Current password</span>
                      <input
                        className="settings-input"
                        type="password"
                        autoComplete="current-password"
                        value={currentPassword}
                        onChange={(event) => {
                          setCurrentPassword(event.target.value);
                          setAccountError(null);
                        }}
                      />
                    </label>

                    <label className="panel-field">
                      <span className="panel-field-label">New password</span>
                      <input
                        className="settings-input"
                        type="password"
                        autoComplete="new-password"
                        value={nextPassword}
                        onChange={(event) => {
                          setNextPassword(event.target.value);
                          setAccountError(null);
                        }}
                      />
                      <span className="panel-field-hint">Use at least {MIN_PASSWORD_LENGTH} characters.</span>
                    </label>

                    {accountError ? <p className="sidebar-inline-error">{accountError}</p> : null}

                    <div className="panel-actions between">
                      <button className="sidebar-primary-button" type="submit" disabled={accountBusy}>
                        {accountBusy ? 'Updating…' : 'Change password'}
                      </button>

                      <button className="ghost-button" type="button" onClick={() => void onLogout()}>
                        <LogOut size={16} strokeWidth={1.85} aria-hidden="true" />
                        <span>Sign out</span>
                      </button>
                    </div>
                  </form>
                </section>
              ) : null}
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
