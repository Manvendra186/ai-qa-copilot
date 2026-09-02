import { useEffect, useState } from 'react';
import { useJobEvents } from '../hooks/useJobEvents';
import {
  runRegressionAnalysis,
  runRegressionSet,
  type RegressionAnalysisRequest,
  type RegressionSet,
  type RunResult,
} from '../lib/api';

function messageOf(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

function pct(n: number | null | undefined): string {
  if (n === null || n === undefined || !Number.isFinite(n)) return 'n/a';
  return `${(n * 100).toFixed(0)}%`;
}

function riskLabel(score: number): string {
  if (score >= 0.8) return 'critical';
  if (score >= 0.5) return 'high';
  if (score >= 0.3) return 'medium';
  return 'low';
}

/** Parse a newline/comma-separated list of repo-relative paths (deduped). */
function parseList(text: string): string[] {
  const seen = new Set<string>();
  for (const raw of text.split(/[\n,]/)) {
    const v = raw.trim();
    if (v) seen.add(v);
  }
  return [...seen];
}

interface Props {
  projectId: string;
}

/**
 * "Regression analysis" — the S6.4 regression engine (build bible §7, §19 Phase 6).
 *
 * `POST /projects/{id}/regression/analyze` (202 + job_id) starts a
 * REGRESSION_ANALYZE job; its `regression.set` SSE event carries the
 * deterministic S6.1 impact set, S6.2 risk ranking, S6.3 top-N recommendation
 * set and the optional S6.5 advisor brief, all rendered here.
 *
 * "Run this set" reuses the existing S3 execution path:
 * `POST /projects/{id}/runs` (202 + job_id) runs the recommended tests via
 * `run_playwright` + `persist_run`, and the `run.result` SSE event carries the
 * persisted run id and totals (readable via the S3.2 `GET /runs/{id}` path).
 */
export function RegressionAnalysis({ projectId }: Props) {
  const job = useJobEvents();
  const running = job.jobId !== null && job.outcome === 'running';
  const failed = job.outcome === 'failed';

  // Latch the job payloads: "Run this set" is a *second* job on the same
  // event channel, so the regression set must survive starting that run.
  const [regression, setRegression] = useState<RegressionSet | null>(null);
  const [runResult, setRunResult] = useState<RunResult | null>(null);

  const [repositoryPath, setRepositoryPath] = useState('');
  const [filesText, setFilesText] = useState('');
  const [baseRef, setBaseRef] = useState('');
  const [headRef, setHeadRef] = useState('');
  const [topN, setTopN] = useState(10);
  const [submitError, setSubmitError] = useState<string | null>(null);

  useEffect(() => {
    if (job.lastRegressionSet) setRegression(job.lastRegressionSet as unknown as RegressionSet);
  }, [job.lastRegressionSet]);

  useEffect(() => {
    if (job.lastRunResult) setRunResult(job.lastRunResult as unknown as RunResult);
  }, [job.lastRunResult]);

  const files = parseList(filesText);
  const hasRefs = baseRef.trim() !== '' && headRef.trim() !== '';
  const hasChangeSource = hasRefs || files.length > 0;
  const canAnalyze = repositoryPath.trim() !== '' && hasChangeSource && !running;

  const recommendedTests =
    regression?.recommendation.recommendations.map((r) => r.test_key) ?? [];

  const onAnalyze = async () => {
    setSubmitError(null);
    const body: RegressionAnalysisRequest = {
      repository_path: repositoryPath.trim(),
      top_n: topN,
    };
    if (hasRefs) {
      body.base_ref = baseRef.trim();
      body.head_ref = headRef.trim();
    } else {
      body.files = files;
    }
    try {
      const { job_id } = await runRegressionAnalysis(projectId, body);
      setRunResult(null);
      job.start(job_id);
    } catch (err) {
      setSubmitError(messageOf(err));
    }
  };

  const onRunThisSet = async () => {
    setSubmitError(null);
    if (recommendedTests.length === 0) {
      setSubmitError('No recommended tests yet — run the analysis first.');
      return;
    }
    try {
      const { job_id } = await runRegressionSet(projectId, {
        repository_path: repositoryPath.trim(),
        tests: recommendedTests,
      });
      setRunResult(null);
      job.start(job_id);
    } catch (err) {
      setSubmitError(messageOf(err));
    }
  };
  return (
    <div className="space-y-6">
      {/* Controls */}
      <section className="rounded-xl border border-slate-800 bg-slate-900/40 p-5">
        <h3 className="text-sm font-semibold text-slate-200">Regression analysis</h3>
        <p className="mt-1 text-sm text-slate-400">
          Deterministic change-impact, risk ranking and test recommendation over this
          project's knowledge base, plus an optional advisor brief (build bible §7, §19
          Phase 6).
        </p>
        <div className="mt-4 space-y-4">
          <label className="flex flex-col gap-1 text-xs text-slate-400">
            Repository checkout path (server-local)
            <input
              type="text"
              placeholder="e.g. C:/repos/ai-qa-copilot or /home/agent/ai-qa-copilot"
              value={repositoryPath}
              disabled={running}
              onChange={(e) => setRepositoryPath(e.target.value)}
              className="rounded-lg border border-slate-700 bg-slate-950 px-3 py-1.5 font-mono text-sm text-slate-200"
            />
          </label>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <label className="flex flex-col gap-1 text-xs text-slate-400">
              Changed files (repo-relative, one per line or comma-separated)
              <textarea
                rows={3}
                placeholder={'src/app/login.ts\nsrc/app/session.ts'}
                value={filesText}
                disabled={running}
                onChange={(e) => setFilesText(e.target.value)}
                className="rounded-lg border border-slate-700 bg-slate-950 px-3 py-1.5 font-mono text-xs text-slate-200"
              />
            </label>
            <div className="flex flex-col gap-3">
              <div className="grid grid-cols-2 gap-3">
                <label className="flex flex-col gap-1 text-xs text-slate-400">
                  Base ref (optional)
                  <input
                    type="text"
                    placeholder="main"
                    value={baseRef}
                    disabled={running}
                    onChange={(e) => setBaseRef(e.target.value)}
                    className="rounded-lg border border-slate-700 bg-slate-950 px-3 py-1.5 font-mono text-sm text-slate-200"
                  />
                </label>
                <label className="flex flex-col gap-1 text-xs text-slate-400">
                  Head ref (optional)
                  <input
                    type="text"
                    placeholder="feature/login"
                    value={headRef}
                    disabled={running}
                    onChange={(e) => setHeadRef(e.target.value)}
                    className="rounded-lg border border-slate-700 bg-slate-950 px-3 py-1.5 font-mono text-sm text-slate-200"
                  />
                </label>
              </div>
              <label className="flex flex-col gap-1 text-xs text-slate-400">
                Top-N recommendations
                <input
                  type="number"
                  min={1}
                  max={500}
                  value={topN}
                  disabled={running}
                  onChange={(e) =>
                    setTopN(Math.max(1, Math.min(500, Number(e.target.value) || 1)))
                  }
                  className="w-28 rounded-lg border border-slate-700 bg-slate-950 px-3 py-1.5 text-sm text-slate-200"
                />
              </label>
            </div>
          </div>
          {!hasChangeSource && (
            <p className="text-xs text-amber-400/80">
              Provide changed files or a base/head ref pair to analyze.
            </p>
          )}
          <div className="flex flex-wrap gap-3 pt-1">
            <button
              type="button"
              onClick={onAnalyze}
              disabled={!canAnalyze}
              className="rounded-lg bg-indigo-600 px-4 py-1.5 text-sm font-medium text-white transition hover:bg-indigo-500 disabled:cursor-not-allowed disabled:opacity-40"
            >
              Analyze
            </button>
            <button
              type="button"
              onClick={onRunThisSet}
              disabled={running || recommendedTests.length === 0}
              className="rounded-lg border border-emerald-700 bg-emerald-500/10 px-4 py-1.5 text-sm font-medium text-emerald-300 transition hover:bg-emerald-500/20 disabled:cursor-not-allowed disabled:opacity-40"
            >
              Run this set{recommendedTests.length > 0 ? ` (${recommendedTests.length})` : ''}
            </button>
          </div>
        </div>
      </section>

      {submitError && (
        <section className="rounded-xl border border-rose-800 bg-rose-950/40 px-4 py-3 text-sm text-rose-300">
          {submitError}
        </section>
      )}

      {/* Running */}
      {running && (
        <section className="rounded-xl border border-slate-800 bg-slate-900/40 p-5">
          <p className="text-sm text-slate-300">
            {regression === null ? 'Analyzing change impact…' : 'Running the selected tests…'}
          </p>
        </section>
      )}

      {/* Job failed */}
      {failed && (
        <section className="rounded-xl border border-rose-800 bg-rose-950/40 px-4 py-3 text-sm text-rose-300">
          Job failed: {job.error}
        </section>
      )}

      {/* "Run this set" result (S3 execution path) */}
      {runResult && <RunResultView result={runResult} />}

      {/* Regression set */}
      {regression && <RegressionResultView result={regression} />}
    </div>
  );
}

/** "Run this set" result — the S3 execution path's `run.result` payload. */
function RunResultView({ result }: { result: RunResult }) {
  const { totals } = result;
  return (
    <section className="rounded-xl border border-emerald-800/60 bg-emerald-950/20 p-5">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-sm font-semibold text-emerald-200">Run result</h3>
        <span className="rounded-full border border-emerald-700 bg-emerald-500/15 px-2 py-0.5 text-[11px] font-medium text-emerald-300">
          {result.status}
        </span>
        <span className="ml-auto font-mono text-[11px] text-slate-500">run {result.run_id}</span>
      </div>
      <div className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-5">
        {(
          [
            ['total', totals.total],
            ['passed', totals.passed],
            ['failed', totals.failed],
            ['flaky', totals.flaky],
            ['skipped', totals.skipped],
          ] as const
        ).map(([label, value]) => (
          <div key={label} className="rounded-lg border border-slate-800 bg-slate-950/60 p-3">
            <div className="text-xl font-semibold text-slate-100">{value}</div>
            <div className="text-[11px] text-slate-400">{label}</div>
          </div>
        ))}
      </div>
      <p className="mt-2 text-xs text-slate-500">
        Persisted via the S3 execution path — details at{' '}
        <span className="font-mono">/runs/{result.run_id}</span>.
      </p>
    </section>
  );
}


/** The `regression.set` payload (S6.1 impact · S6.2 ranking · S6.3 set · S6.5 advice). */
function RegressionResultView({ result }: { result: RegressionSet }) {
  const { recommendation, impact, ranking, advice } = result;
  const recs = recommendation.recommendations;
  const topRisk = ranking.ranked.length > 0 ? ranking.ranked[0].risk_score : null;

  return (
    <div className="space-y-6">
      {/* Summary counts */}
      <section className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        <div className="rounded-xl border border-slate-800 bg-slate-900/40 p-4">
          <div className="text-2xl font-semibold text-slate-100">{recs.length}</div>
          <div className="text-xs text-slate-400">
            recommended tests (top {recommendation.top_n})
          </div>
        </div>
        <div className="rounded-xl border border-slate-800 bg-slate-900/40 p-4">
          <div className="text-2xl font-semibold text-slate-100">{impact.impacted.length}</div>
          <div className="text-xs text-slate-400">
            impacted test files ({impact.test_files_scanned} scanned)
          </div>
        </div>
        <div className="rounded-xl border border-slate-800 bg-slate-900/40 p-4">
          <div className="text-2xl font-semibold text-slate-100">
            {topRisk === null ? '—' : pct(topRisk)}
          </div>
          <div className="text-xs text-slate-400">top risk score</div>
        </div>
      </section>

      {/* Recommendation set (S6.3) */}
      <section className="rounded-xl border border-slate-800 bg-slate-900/40 p-5">
        <h3 className="text-sm font-semibold text-slate-200">
          Recommendation set{' '}
          <span className="font-normal text-slate-500">({recs.length})</span>
        </h3>
        {recs.length === 0 ? (
          <p className="mt-2 text-sm text-slate-500">
            No recommendations (empty corpus or insufficient samples).
          </p>
        ) : (
          <ol className="mt-3 space-y-2">
            {recs.map((r) => (
              <li
                key={`${r.test_key}-${r.rank}`}
                className="rounded-lg border border-slate-800 bg-slate-950/60 p-3"
              >
                <div className="flex flex-wrap items-center gap-2 text-xs">
                  <span className="font-mono text-slate-500">#{r.rank}</span>
                  <span className="font-mono font-medium text-slate-200">{r.test_key}</span>
                  <span className="ml-auto rounded-full border border-slate-700 bg-slate-800/60 px-2 py-0.5 text-slate-300">
                    {riskLabel(r.risk_score)}
                  </span>
                  <span className="font-mono text-slate-500">risk {pct(r.risk_score)}</span>
                </div>
                <div className="mt-1.5 flex flex-wrap gap-1.5">
                  {r.impact_kind && (
                    <span className="rounded-full border border-indigo-700 bg-indigo-500/10 px-2 py-0.5 text-[11px] text-indigo-300">
                      {r.impact_kind}
                    </span>
                  )}
                  {r.requirement_risk && (
                    <span className="rounded-full border border-amber-700 bg-amber-500/10 px-2 py-0.5 text-[11px] text-amber-300">
                      req {r.requirement_risk}
                    </span>
                  )}
                  {r.test_case_priority && (
                    <span className="rounded-full border border-slate-700 bg-slate-800/60 px-2 py-0.5 text-[11px] text-slate-300">
                      {r.test_case_priority}
                    </span>
                  )}
                  <span className="text-[11px] text-slate-500">
                    {r.stats.executions} runs · flaky {pct(r.stats.flakiness_rate)} · failing{' '}
                    {pct(r.stats.failure_rate)}
                  </span>
                </div>
                {r.changed_files.length > 0 && (
                  <div className="mt-1.5 flex flex-wrap gap-1">
                    {r.changed_files.map((f) => (
                      <span
                        key={f}
                        className="rounded border border-slate-800 bg-slate-950 px-1.5 py-0.5 font-mono text-[11px] text-slate-400"
                      >
                        {f}
                      </span>
                    ))}
                  </div>
                )}
                {r.rationale.length > 0 && (
                  <ul className="mt-1.5 list-inside list-disc space-y-0.5 text-xs text-slate-400">
                    {r.rationale.map((reason, i) => (
                      <li key={`${reason}-${i}`}>{reason}</li>
                    ))}
                  </ul>
                )}
              </li>
            ))}
          </ol>
        )}
      </section>
      {/* Impact set (S6.1) + risk ranking (S6.2) */}
      <section className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <div className="rounded-xl border border-slate-800 bg-slate-900/40 p-5">
          <h3 className="text-sm font-semibold text-slate-200">
            Impact set <span className="font-normal text-slate-500">({impact.impacted.length})</span>
          </h3>
          <p className="mt-1 text-xs text-slate-500">
            Test files impacted by the change, with the deterministic signals that fired.
          </p>
          {impact.impacted.length === 0 ? (
            <p className="mt-2 text-sm text-slate-500">No impacted test files found.</p>
          ) : (
            <ul className="mt-3 space-y-2">
              {impact.impacted.map((t) => (
                <li key={t.path} className="rounded-lg border border-slate-800 bg-slate-950/60 p-2.5">
                  <div className="flex flex-wrap items-center gap-2 text-xs">
                    <span className="font-mono font-medium text-slate-200">{t.path}</span>
                    <span className="ml-auto flex flex-wrap gap-1">
                      {t.kinds.map((k) => (
                        <span
                          key={k}
                          className="rounded-full border border-slate-700 bg-slate-800/60 px-2 py-0.5 text-[11px] text-slate-300"
                        >
                          {k}
                        </span>
                      ))}
                    </span>
                  </div>
                  {t.signals.length > 0 && (
                    <p className="mt-1 text-[11px] text-slate-500">{t.signals.join(' · ')}</p>
                  )}
                </li>
              ))}
            </ul>
          )}
          {impact.notes.length > 0 && (
            <ul className="mt-3 list-inside list-disc space-y-0.5 text-xs text-slate-500">
              {impact.notes.map((n, i) => (
                <li key={`${n}-${i}`}>{n}</li>
              ))}
            </ul>
          )}
        </div>
        <div className="rounded-xl border border-slate-800 bg-slate-900/40 p-5">
          <h3 className="text-sm font-semibold text-slate-200">
            Risk ranking <span className="font-normal text-slate-500">({ranking.ranked.length})</span>
          </h3>
          <p className="mt-1 text-xs text-slate-500">
            Deterministic f(impact, failure, flakiness, requirement risk, priority).
          </p>
          {ranking.ranked.length === 0 ? (
            <p className="mt-2 text-sm text-slate-500">No risk-ranked tests.</p>
          ) : (
            <table className="mt-3 w-full text-left text-xs">
              <thead>
                <tr className="text-[11px] text-slate-500">
                  <th className="pb-1.5 font-medium">test</th>
                  <th className="pb-1.5 text-right font-medium">risk</th>
                  <th className="pb-1.5 text-right font-medium">flaky</th>
                  <th className="pb-1.5 text-right font-medium">failing</th>
                </tr>
              </thead>
              <tbody>
                {ranking.ranked.map((t) => (
                  <tr key={t.test_key} className="border-t border-slate-800/60">
                    <td className="max-w-[14rem] truncate py-1.5 pr-2 font-mono text-slate-300">
                      {t.test_key}
                    </td>
                    <td className="py-1.5 text-right font-mono text-slate-300">
                      {pct(t.risk_score)}
                    </td>
                    <td className="py-1.5 text-right font-mono text-slate-400">
                      {pct(t.stats.flakiness_rate)}
                    </td>
                    <td className="py-1.5 text-right font-mono text-slate-400">
                      {pct(t.stats.failure_rate)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </section>

      {/* Advisor (S6.5) */}
      <section className="rounded-xl border border-slate-800 bg-slate-900/40 p-5">
        <h3 className="text-sm font-semibold text-slate-200">Advisor summary</h3>
        {advice ? (
          <div className="mt-2 space-y-2">
            <span className="inline-block rounded-full border border-indigo-700 bg-indigo-500/15 px-2 py-0.5 text-[11px] font-medium text-indigo-300">
              {advice.source}
            </span>
            <p className="whitespace-pre-wrap text-sm text-slate-300">{advice.summary}</p>
          </div>
        ) : (
          <p className="mt-2 text-sm text-slate-500">
            No advisor summary (advisor disabled, unavailable, or the LLM gateway is not
            configured).
          </p>
        )}
      </section>
    </div>
  );
}

