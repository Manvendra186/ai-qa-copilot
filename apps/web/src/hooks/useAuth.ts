import { useCallback, useEffect, useState } from 'react';
import {
  getToken,
  login as apiLogin,
  me as apiMe,
  setToken,
  type MeResponse,
  type ProjectRef,
} from '../lib/api';

export type AuthStatus = 'booting' | 'authenticated' | 'unauthenticated';

export interface Auth {
  status: AuthStatus;
  session: MeResponse | null;
  /**
   * The caller's strongest project (role rank owner > member > viewer, then
   * name) — dev-mode auth has a single project per user (§31.3).
   */
  project: ProjectRef | null;
  /** Last login error, if any. */
  error: string | null;
  /** `true` on success. */
  login: (email: string, password: string) => Promise<boolean>;
  logout: () => void;
}

const ROLE_RANK: Record<string, number> = { owner: 3, member: 2, viewer: 1 };

function bestProject(projects: ProjectRef[]): ProjectRef | null {
  if (projects.length === 0) return null;
  return [...projects].sort((a, b) => {
    const byRole = (ROLE_RANK[b.role] ?? 0) - (ROLE_RANK[a.role] ?? 0);
    if (byRole !== 0) return byRole;
    return a.name.localeCompare(b.name);
  })[0];
}

/**
 * Dev-mode session (build bible §31.3): boot restores a stored Bearer token
 * via `GET /auth/me`; `login` stores a new one; `logout` clears it.
 */
export function useAuth(): Auth {
  const [status, setStatus] = useState<AuthStatus>('booting');
  const [session, setSession] = useState<MeResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    // Boot: no stored token → straight to the login form.
    if (getToken() === null) {
      setStatus('unauthenticated');
      return;
    }
    let cancelled = false;
    apiMe()
      .then((me) => {
        if (cancelled) return;
        setSession(me);
        setStatus('authenticated');
      })
      .catch(() => {
        if (cancelled) return;
        setToken(null);
        setSession(null);
        setStatus('unauthenticated');
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const login = useCallback(async (email: string, password: string): Promise<boolean> => {
    try {
      const res = await apiLogin(email, password);
      setToken(res.token);
      setSession({ user: res.user, projects: res.projects });
      setError(null);
      setStatus('authenticated');
      return true;
    } catch (err) {
      const message = err instanceof Error ? err.message : 'login failed';
      setError(message);
      return false;
    }
  }, []);

  const logout = useCallback(() => {
    setToken(null);
    setSession(null);
    setError(null);
    setStatus('unauthenticated');
  }, []);

  return {
    status,
    session,
    project: session ? bestProject(session.projects) : null,
    error,
    login,
    logout,
  };
}
