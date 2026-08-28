import { useState, type FormEvent } from 'react';
import type { Auth } from '../hooks/useAuth';

const INPUT_CLASS =
  'mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100 outline-none focus:border-indigo-500';

/**
 * Dev-mode login (build bible §31.3, S0.8): email + password → Bearer token
 * via `POST /api/v1/auth/login`. Accounts come from `scripts/seed.py`.
 */
export function LoginForm({ auth }: { auth: Auth }) {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [localError, setLocalError] = useState<string | null>(null);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!email.trim() || !password) {
      setLocalError('Enter email and password.');
      return;
    }
    setBusy(true);
    setLocalError(null);
    await auth.login(email.trim(), password);
    setBusy(false);
  };

  const message = localError ?? auth.error;
  return (
    <main className="mx-auto w-full max-w-sm flex-1 px-6 py-16">
      <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-6">
        <h2 className="text-lg font-semibold text-slate-100">Sign in</h2>
        <p className="mt-1 text-xs text-slate-400">
          Dev-mode auth (build bible §31.3). Accounts are seeded by{' '}
          <code className="text-slate-300">scripts/seed.py</code>.
        </p>
        <form onSubmit={submit} className="mt-5 space-y-4">
          <label className="block text-xs">
            <span className="text-slate-300">Email</span>
            <input
              type="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              autoComplete="email"
              placeholder="you@example.com"
              className={INPUT_CLASS}
            />
          </label>
          <label className="block text-xs">
            <span className="text-slate-300">Password</span>
            <input
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              autoComplete="current-password"
              placeholder="••••••••"
              className={INPUT_CLASS}
            />
          </label>
          {message && <p className="text-xs text-rose-400">{message}</p>}
          <button
            type="submit"
            disabled={busy}
            className="w-full rounded-lg bg-indigo-500 px-4 py-2 text-sm font-medium text-white transition hover:bg-indigo-400 disabled:opacity-50"
          >
            {busy ? 'Signing in…' : 'Sign in'}
          </button>
        </form>
      </div>
    </main>
  );
}
