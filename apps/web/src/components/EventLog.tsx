import type { EventLogEntry } from '../lib/pipeline';

interface Props {
  entries: EventLogEntry[];
  done: boolean;
  failed?: boolean;
}

export function EventLog({ entries, done, failed = false }: Props) {
  return (
    <section className="rounded-xl border border-slate-800 bg-slate-900/40">
      <header className="border-b border-slate-800 px-4 py-2.5 text-xs font-semibold uppercase tracking-wide text-slate-400">
        Live event stream (SSE)
      </header>
      <ol
        className="max-h-64 overflow-y-auto px-4 py-3 font-mono text-xs leading-6"
        aria-live="polite"
      >
        {entries.length === 0 ? (
          <li className="text-slate-500">Waiting for events…</li>
        ) : (
          entries.map((entry, index) => (
            <li key={`${entry.time}-${index}`} className="flex gap-3">
              <span className="shrink-0 text-slate-600">{entry.time}</span>
              <span className="w-32 shrink-0 text-indigo-300">{entry.event}</span>
              <span className="break-all text-slate-400">{entry.detail}</span>
            </li>
          ))
        )}
        {done && (
          <li className="text-emerald-300">
            job completed — the persisted test cases are rendered above
          </li>
        )}
        {failed && (
          <li className="text-rose-300">stream ended with an error — submit again to retry</li>
        )}
      </ol>
    </section>
  );
}
