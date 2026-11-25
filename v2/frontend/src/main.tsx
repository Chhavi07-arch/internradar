import { StrictMode, useEffect, useState } from 'react';
import type { FormEvent } from 'react';
import { createRoot } from 'react-dom/client';
import './styles.css';

type Session = {
  authenticated: boolean;
  loginAvailable: boolean;
  name?: string | null;
  email?: string | null;
};

type Preferences = {
  roles: string[];
  locations: string[];
  remote: boolean;
  graduationYear: number | null;
};

type Profile = Preferences & { status: 'pending' | 'completed' | 'skipped' };

type LoadState<T> = { status: 'loading' } | { status: 'error' } | { status: 'ready'; data: T };

class RequestError extends Error {
  constructor(readonly status: number) {
    super(`Request failed (${status})`);
  }
}

async function request(path: string, init: RequestInit = {}) {
  const response = await fetch(path, { ...init, credentials: 'same-origin', cache: 'no-store', redirect: 'error' });
  if (!response.ok) throw new RequestError(response.status);
  return response;
}

function profileFailure(error: unknown, saving: boolean) {
  const action = saving ? 'Could not save your profile.' : 'Could not skip setup.';
  if (!(error instanceof RequestError)) return `${action} Check that the local app is running, then try again.`;
  if (error.status === 401) return `${action} Your session has ended. Reload and sign in again.`;
  if (error.status === 403) return `${action} The server rejected the request. Reload the page and try again (403).`;
  if (error.status === 400) return `${action} Check your roles, locations, and graduation year.`;
  return `${action} The server returned ${error.status}. Please try again.`;
}

async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  return (await request(path, { signal, headers: { Accept: 'application/json' } })).json();
}

async function mutate(path: string, method: 'PUT' | 'POST', body?: Preferences) {
  const { headerName, token } = await getJson<{ headerName: string; token: string }>('/api/csrf');
  const headers = new Headers({ Accept: 'application/json', [headerName]: token });
  if (body !== undefined) headers.set('Content-Type', 'application/json');
  return request(path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
}

function useResource<T>(path: string) {
  const [state, setState] = useState<LoadState<T>>({ status: 'loading' });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    getJson<T>(path, controller.signal).then(
      (data) => {
        if (!controller.signal.aborted) setState({ status: 'ready', data });
      },
      () => {
        if (!controller.signal.aborted) setState({ status: 'error' });
      },
    );
    return () => controller.abort();
  }, [path, attempt]);

  const retry = () => {
    setState({ status: 'loading' });
    setAttempt((value) => value + 1);
  };
  return { state, retry, setState };
}

function ProfileEditor({ profile, busy, onSave, onSkip, onCancel }: {
  profile: Profile;
  busy: 'save' | 'skip' | 'logout' | null;
  onSave: (preferences: Preferences) => void;
  onSkip: () => void;
  onCancel: () => void;
}) {
  const pending = profile.status === 'pending';
  const [validationError, setValidationError] = useState('');

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy !== null) return;
    const fields = new FormData(event.currentTarget);
    const list = (name: string) => [...new Set(String(fields.get(name)).split('\n').map((value) => value.trim()).filter(Boolean))];
    const roles = list('roles');
    const locations = list('locations');
    const year = fields.get('graduationYear');
    if (roles.length === 0) {
      setValidationError('Enter at least one target role.');
      return;
    }
    if ([roles, locations].some((entries) => entries.length > 10 || entries.some((entry) => entry.length > 80))) {
      setValidationError('Enter no more than 10 roles and 10 locations, with up to 80 characters per entry.');
      return;
    }
    setValidationError('');
    onSave({ roles, locations, remote: fields.has('remote'), graduationYear: year === '' ? null : Number(year) });
  }

  return (
    <form onSubmit={submit} aria-busy={busy !== null}>
      <fieldset disabled={busy !== null} aria-labelledby="profile-heading">
        <p id="list-help">Enter one role or location per line, up to 10 of each and 80 characters per entry.</p>
        {validationError && <p className="error" role="alert">{validationError}</p>}
        <label>
          Target roles (required)
          <textarea name="roles" rows={3} required aria-describedby="list-help" defaultValue={profile.roles.join('\n')} />
        </label>
        <label>
          Preferred locations (optional)
          <textarea name="locations" rows={3} aria-describedby="list-help" defaultValue={profile.locations.join('\n')} />
        </label>
        <label>
          Graduation year (optional)
          <input name="graduationYear" type="number" min={2000} max={2100} step={1} defaultValue={profile.graduationYear ?? ''} />
        </label>
        <label className="checkbox">
          <input name="remote" type="checkbox" defaultChecked={profile.remote} />
          Include remote internships
        </label>
        <div className="actions">
          <button className="primary" type="submit">{busy === 'save' ? 'Saving…' : pending ? 'Save and continue' : 'Save profile'}</button>
          {pending
            ? <button type="button" onClick={onSkip}>{busy === 'skip' ? 'Skipping…' : 'Skip for now'}</button>
            : <button type="button" onClick={onCancel}>Cancel</button>}
        </div>
      </fieldset>
    </form>
  );
}

function Account({ session, onSignedOut }: { session: Session; onSignedOut: () => void }) {
  const { state, retry, setState } = useResource<Profile>('/api/profile');
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState<'save' | 'skip' | 'logout' | null>(null);
  const [profileError, setProfileError] = useState('');
  const [logoutError, setLogoutError] = useState(false);

  async function updateProfile(preferences?: Preferences) {
    setBusy(preferences ? 'save' : 'skip');
    setProfileError('');
    try {
      const response = preferences
        ? await mutate('/api/profile', 'PUT', preferences)
        : await mutate('/api/profile/skip', 'POST');
      const data: Profile = await response.json();
      setState({ status: 'ready', data });
      setEditing(false);
    } catch (error) {
      setProfileError(profileFailure(error, preferences !== undefined));
    } finally {
      setBusy(null);
    }
  }

  async function signOut() {
    setBusy('logout');
    setLogoutError(false);
    try {
      const response = await mutate('/logout', 'POST');
      if (response.status !== 204) throw new Error('Unexpected sign-out response');
      onSignedOut();
    } catch {
      setLogoutError(true);
    } finally {
      setBusy(null);
    }
  }

  const profile = state.status === 'ready' ? state.data : null;
  const showEditor = profile?.status === 'pending' || editing;
  return (
    <>
      <section aria-labelledby="account-heading">
        <h2 id="account-heading">Account</h2>
        <p>{session.name ? `Signed in as ${session.name}` : session.email ? `Signed in as ${session.email}` : 'You’re signed in.'}
          {session.name && session.email && <><br />{session.email}</>}
        </p>
        {logoutError && <p className="error" role="alert">Could not sign out. Please try again.</p>}
        <button type="button" disabled={busy !== null} onClick={signOut}>{busy === 'logout' ? 'Signing out…' : 'Sign out'}</button>
      </section>
      <section aria-labelledby="profile-heading">
        <h2 id="profile-heading">{profile?.status === 'pending' ? 'Set up your profile' : editing ? 'Edit your profile' : 'Your profile'}</h2>
        {state.status === 'loading' && <p role="status">Loading your profile…</p>}
        {state.status === 'error' && <>
          <p className="error" role="alert">Could not load your profile.</p>
          <button type="button" onClick={retry}>Retry profile</button>
        </>}
        {profileError && <p className="error" role="alert">{profileError}</p>}
        {profile && (showEditor ? (
          <ProfileEditor profile={profile} busy={busy} onSave={updateProfile}
            onSkip={() => void updateProfile()} onCancel={() => { setEditing(false); setProfileError(''); }} />
        ) : (
          <>
            {profile.status === 'skipped' && <p>Profile setup was skipped. You can add preferences at any time.</p>}
            <dl>
              <dt>Target roles</dt><dd>{profile.roles.join(', ') || 'Not set'}</dd>
              <dt>Preferred locations</dt><dd>{profile.locations.join('; ') || 'Not set'}</dd>
              <dt>Include remote internships</dt><dd>{profile.remote ? 'Yes' : 'No'}</dd>
              <dt>Graduation year</dt><dd>{profile.graduationYear ?? 'Not set'}</dd>
            </dl>
            <div className="actions"><button type="button" disabled={busy !== null} onClick={() => setEditing(true)}>Edit profile</button></div>
          </>
        ))}
      </section>
    </>
  );
}

function App() {
  const { state, retry, setState } = useResource<Session>('/api/session');
  const [loginError, setLoginError] = useState(() => new URLSearchParams(window.location.search).get('loginError') === '1');
  const session = state.status === 'ready' ? state.data : null;

  return (
    <main>
      <header>
        <p className="eyebrow">Internship discovery</p>
        <h1>InternRadar v2</h1>
        <p>Save your preferences for future internship matching.</p>
      </header>
      {session?.authenticated ? (
        <Account session={session} onSignedOut={() => {
          setLoginError(false);
          setState({ status: 'ready', data: { authenticated: false, loginAvailable: session.loginAvailable } });
        }} />
      ) : (
        <section aria-labelledby="signin-heading">
          <h2 id="signin-heading">Sign in</h2>
          {loginError && <p className="error" role="alert">Google sign-in didn’t complete. Please try again.</p>}
          {state.status === 'loading' && <p role="status">Checking your sign-in status…</p>}
          {state.status === 'error' && <>
            <p className="error" role="alert">Could not check your sign-in status.</p>
            <button type="button" onClick={retry}>Try again</button>
          </>}
          {session && (session.loginAvailable ? (
            <>
              <p>Sign in with Google to set up your internship preferences.</p>
              <a className="button primary" href="/oauth2/authorization/google">Continue with Google</a>
            </>
          ) : <p>Google sign-in is currently unavailable.</p>)}
        </section>
      )}
      <section aria-labelledby="matching-heading">
        <h2 id="matching-heading">Internship matching</h2>
        <p>Matching isn’t available yet. No internship listings or recommendations are shown.</p>
      </section>
    </main>
  );
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
