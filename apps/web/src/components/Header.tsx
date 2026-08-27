import type { ConnectionStatus } from '../hooks/useJobEvents';

const STATUS_META: Record<ConnectionStatus, { label: string; dot: string }> = {
  connecting: { label: 'Connecting…', dot: 'bg-amber-400' },
  open: { label: 'SSE live', dot: 'bg-emerald-400' },
  error: { label: 'Stream error', dot: 'bg-rose-400' },
  closed: { label: 'Stream closed', dot: 'bg-slate-400' },
};

interface Props {
  user?: string;
  project?: string;
  /** Live job stream status — hidden until a job starts. */
  status?: ConnectionStatus | null;
  onLogout?: () => void;
}

export function Header({ user, project, status, onLogout }: Props) {
  const meta = status ? STATUS_META[status] : null;
  return (
    <header className="border-b border-slate-800 bg-slate-900/60">
      <div className="mx-auto flex max-w-5xl flex-wrap items-center justify-between gap-4 px-6 py-4">
        <div className="flex items-center gap-3">
          <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-indigo-500 text-sm font-bold text-white">
            QA
          </div>
          <div>
            <h1 className="text-base font-semibold text-slate-100">AI QA Copilot</h1>
            <p className="text-xs text-slate-400">
              Requirement → test design → automation → execution → failure analysis → fix
            </p>
          </div>
        </div>
        <div className="flex items-center gap-4">
          {project && <span className="text-sm text-slate-300">{project}</span>}
          {user && <span className="text-xs text-slate-400">{user}</span>}
          {meta && (
            <span className="flex items-center gap-2 rounded-full border border-slate-700 bg-slate-800/60 px-3 py-1 text-xs text-slate-300">
              <span className={`h-2 w-2 rounded-full ${meta.dot}`} />
              {meta.label}
            </span>
          )}
          {onLogout && (
            <button
              type="button"
              onClick={onLogout}
              className="rounded-lg border border-slate-700 px-3 py-1.5 text-xs text-slate-300 transition hover:border-slate-500 hover:text-slate-100"
            >
              Sign out
            </button>
          )}
        </div>
      </div>
    </header>
  );
}
