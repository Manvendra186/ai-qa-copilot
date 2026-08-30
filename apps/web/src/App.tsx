import { useCallback, useEffect, useState } from 'react';
import { EventLog } from './components/EventLog';
import { GeneratedTests } from './components/GeneratedTests';
import { Header } from './components/Header';
import { LoginForm } from './components/LoginForm';
import { PipelineView } from './components/PipelineView';
import { ProjectKnowledge } from './components/ProjectKnowledge';
import { RequirementForm } from './components/RequirementForm';
import { RequirementHistory } from './components/RequirementHistory';
import { RunsView } from './components/RunsView';
import { TestCaseList } from './components/TestCaseList';
import { useAuth } from './hooks/useAuth';
import { useJobEvents } from './hooks/useJobEvents';
import {
  createTestCaseJob,
  getRequirement,
  type DesignRequest,
  type RequirementOut,
} from './lib/api';

const FOOTER = 'AI QA Copilot · local LLM only · one step at a time (build bible §19)';

function messageOf(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

export default function App() {
  const auth = useAuth();
  const job = useJobEvents();
  const startJob = job.start; // stable identity (useCallback inside the hook)
  const [requirement, setRequirement] = useState<RequirementOut | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [fetchError, setFetchError] = useState<string | null>(null);
  const [tab, setTab] = useState<'design' | 'generated' | 'runs' | 'knowledge'>('design');

  // S1.3 read-back: once the job completes, the terminal `output_ref` is the
  // persisted requirement id — fetch it and render the suite.
  useEffect(() => {
    if (job.outcome !== 'completed' || job.outputRef === null) return;
    let cancelled = false;
    setFetchError(null);
    getRequirement(job.outputRef)
      .then((req) => {
        if (!cancelled) setRequirement(req);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setRequirement(null);
        setFetchError(messageOf(err));
      });
    return () => {
      cancelled = true;
    };
  }, [job.outcome, job.outputRef]);

  const handleSubmit = useCallback(
    (body: DesignRequest) => {
      setSubmitError(null);
      setRequirement(null);
      createTestCaseJob(body)
        .then(({ job_id }) => startJob(job_id))
        .catch((err: unknown) => setSubmitError(messageOf(err)));
    },
    [startJob],
  );

  if (auth.status === 'booting') {
    return (
      <div className="flex min-h-screen items-center justify-center bg-slate-950 text-slate-100">
        <span className="text-sm text-slate-400">Loading…</span>
      </div>
    );
  }

  if (auth.status !== 'authenticated') {
    return (
      <div className="flex min-h-screen flex-col bg-slate-950 text-slate-100">
        <Header />
        <LoginForm auth={auth} />
        <footer className="border-t border-slate-800 px-6 py-4 text-center text-xs text-slate-500">
          {FOOTER}
        </footer>
      </div>
    );
  }

  if (auth.project === null) {
    // Authenticated, but no project membership — the jobs API needs a project
    // (§31.3). Dev setup: add a membership via `scripts/seed.py`.
    return (
      <div className="flex min-h-screen flex-col bg-slate-950 text-slate-100">
        <Header user={auth.session?.user.email} onLogout={auth.logout} />
        <main className="mx-auto w-full max-w-2xl flex-1 px-6 py-16">
          <div className="rounded-xl border border-amber-700/60 bg-amber-950/30 p-6 text-sm text-amber-200">
            <h2 className="text-base font-semibold">No project membership</h2>
            <p className="mt-2 text-amber-200/80">
              Signed in as <code>{auth.session?.user.email}</code> with no role in any project. Dev
              setup: create one with <code>scripts/seed.py</code> and sign in again.
            </p>
          </div>
        </main>
        <footer className="border-t border-slate-800 px-6 py-4 text-center text-xs text-slate-500">
          {FOOTER}
        </footer>
      </div>
    );
  }

  // "Running" means an *active* job is in flight: the reducer's initial state
  // (no job submitted yet) is outcome 'running' + jobId null, so gating on the
  // outcome alone would keep the form disabled on every fresh page load.
  const running = job.jobId !== null && job.outcome === 'running';
  const failed = job.outcome === 'failed';
  const completed = job.outcome === 'completed';

  return (
    <div className="flex min-h-screen flex-col bg-slate-950 text-slate-100">
      <Header
        user={auth.session?.user.email}
        project={auth.project.name}
        status={job.jobId !== null ? job.connection : null}
        onLogout={auth.logout}
      />
      <main className="mx-auto w-full max-w-5xl flex-1 space-y-8 px-6 py-8">
        <nav
          className="flex gap-1 rounded-xl border border-slate-800 bg-slate-900/40 p-1"
          aria-label="Views"
        >
          <button
            type="button"
            onClick={() => setTab('design')}
            className={`rounded-lg px-4 py-1.5 text-sm transition ${
              tab === 'design'
                ? 'bg-slate-800 text-slate-100'
                : 'text-slate-400 hover:text-slate-200'
            }`}
          >
            Test design
          </button>
          <button
            type="button"
            onClick={() => setTab('generated')}
            className={`rounded-lg px-4 py-1.5 text-sm transition ${
              tab === 'generated'
                ? 'bg-slate-800 text-slate-100'
                : 'text-slate-400 hover:text-slate-200'
            }`}
          >
            Generated tests
          </button>
          <button
            type="button"
            onClick={() => setTab('runs')}
            className={`rounded-lg px-4 py-1.5 text-sm transition ${
              tab === 'runs' ? 'bg-slate-800 text-slate-100' : 'text-slate-400 hover:text-slate-200'
            }`}
          >
            Runs
          </button>
          <button
            type="button"
            onClick={() => setTab('knowledge')}
            className={`rounded-lg px-4 py-1.5 text-sm transition ${
              tab === 'knowledge'
                ? 'bg-slate-800 text-slate-100'
                : 'text-slate-400 hover:text-slate-200'
            }`}
          >
            Knowledge
          </button>
        </nav>

        {tab === 'design' && (
          <>
            <section className="flex flex-wrap items-end justify-between gap-4">
              <div>
                <h2 className="text-2xl font-semibold tracking-tight">Test design</h2>
                <p className="mt-1 max-w-2xl text-sm text-slate-400">
                  Describe a requirement — the Test Design Agent runs it as a job (202 + job_id,
                  build bible §11) and the persisted suite is rendered when the job completes
                  (S1.3).
                </p>
              </div>
              <div className="flex items-center gap-3">
                {job.jobId !== null && (
                  <span className="font-mono text-xs text-slate-500">job {job.jobId}</span>
                )}
                {running && (
                  <button
                    type="button"
                    onClick={() => {
                      setSubmitError(null);
                      setRequirement(null);
                      job.reset();
                    }}
                    className="rounded-lg border border-slate-700 px-3 py-1.5 text-xs text-slate-300 transition hover:border-slate-500 hover:text-slate-100"
                  >
                    Start over
                  </button>
                )}
              </div>
            </section>

            <RequirementForm
              projectId={auth.project.id}
              disabled={running}
              error={submitError}
              onSubmit={handleSubmit}
            />

            {job.jobId !== null && <PipelineView stages={job.stages} />}

            {failed && job.error !== null && (
              <section className="rounded-xl border border-rose-800 bg-rose-950/40 px-4 py-3 text-sm text-rose-300">
                Job failed: {job.error}
              </section>
            )}

            {completed && job.outputRef !== null && requirement === null && fetchError === null && (
              <p className="text-sm text-slate-400">Fetching the persisted test cases…</p>
            )}

            {completed && fetchError !== null && (
              <section className="rounded-xl border border-rose-800 bg-rose-950/40 px-4 py-3 text-sm text-rose-300">
                Could not read back the suite (job succeeded): {fetchError}
              </section>
            )}

            {completed && requirement !== null && (
              <section className="space-y-4">
                <div>
                  <h3 className="text-lg font-semibold text-slate-100">{requirement.title}</h3>
                  <p className="mt-1 text-sm text-slate-400">{requirement.content}</p>
                  {requirement.acceptance_criteria.length > 0 && (
                    <ul className="mt-2 list-inside list-disc space-y-0.5 text-xs text-slate-400">
                      {requirement.acceptance_criteria.map((criterion, i) => (
                        <li key={i}>{criterion}</li>
                      ))}
                    </ul>
                  )}
                </div>
                <TestCaseList cases={requirement.test_cases} />
              </section>
            )}

            {/* Past runs: persisted requirements for this project, newest first
                (S1.3 history list). `refreshKey` re-loads the list once a
                design job completes, so the new requirement shows up here. */}
            <RequirementHistory projectId={auth.project.id} refreshKey={job.outputRef} />

            <EventLog entries={job.log} done={completed} failed={failed} />
          </>
        )}

        {tab === 'generated' && <GeneratedTests projectId={auth.project.id} />}

        {tab === 'runs' && <RunsView projectId={auth.project.id} />}

        {tab === 'knowledge' && <ProjectKnowledge projectId={auth.project.id} />}
      </main>
      <footer className="border-t border-slate-800 px-6 py-4 text-center text-xs text-slate-500">
        {FOOTER}
      </footer>
    </div>
  );
}
