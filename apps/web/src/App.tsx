import { EventLog } from './components/EventLog';
import { Header } from './components/Header';
import { PipelineView } from './components/PipelineView';
import { useJobEvents } from './hooks/useJobEvents';

export default function App() {
  const job = useJobEvents();
  return (
    <div className="flex min-h-screen flex-col bg-slate-950 text-slate-100">
      <Header connection={job.connection} />
      <main className="mx-auto w-full max-w-5xl flex-1 space-y-8 px-6 py-8">
        <section className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <h2 className="text-2xl font-semibold tracking-tight">Pipeline run</h2>
            <p className="mt-1 max-w-2xl text-sm text-slate-400">
              S0.7 shell — the stream comes from a dev-only mock endpoint (
              <code className="text-slate-300">/mock/events</code>). S0.9 will replace it with the
              real jobs API (202 + SSE).
            </p>
          </div>
          <div className="flex items-center gap-3">
            {job.jobId && <span className="font-mono text-xs text-slate-500">{job.jobId}</span>}
            <button
              type="button"
              onClick={job.replay}
              className="rounded-lg bg-indigo-500 px-4 py-2 text-sm font-medium text-white transition hover:bg-indigo-400"
            >
              {job.done ? 'Replay run' : 'Restart run'}
            </button>
          </div>
        </section>
        <PipelineView stages={job.stages} />
        <EventLog entries={job.log} done={job.done} />
      </main>
      <footer className="border-t border-slate-800 px-6 py-4 text-center text-xs text-slate-500">
        AI QA Copilot · local LLM only · one step at a time (build bible §19)
      </footer>
    </div>
  );
}
