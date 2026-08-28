import { useCallback, useEffect, useState } from 'react';
import {
  fetchArtifactBlob,
  getRun,
  listRuns,
  type ArtifactOut,
  type RunDetail,
  type RunListItem,
} from '../lib/api';

const STATUS_TONES: Record<string, string> = {
  passed: 'border-emerald-700 bg-emerald-500/15 text-emerald-300',
  completed: 'border-emerald-700 bg-emerald-500/15 text-emerald-300',
  failed: 'border-rose-700 bg-rose-500/15 text-rose-300',
  flaky: 'border-amber-700 bg-amber-500/15 text-amber-300',
  running: 'border-amber-700 bg-amber-500/15 text-amber-300',
  skipped: 'border-slate-600 bg-slate-500/15 text-slate-300',
  pending: 'border-indigo-700 bg-indigo-500/15 text-indigo-300',
};

const NEUTRAL_TONE = 'border-slate-600 bg-slate-500/15 text-slate-300';

function Badge({ label, status }: { label: string; status?: string }) {
  const tone = STATUS_TONES[status ?? label] ?? NEUTRAL_TONE;
  return (
    <span className={`rounded-full border px-2 py-0.5 text-[11px] font-medium ${tone}`}>
      {label}
    </span>
  );
}

function formatTime(iso: string | null): string {
  if (!iso) return '—';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

function shortSha(sha: string | null): string {
  return sha ? sha.slice(0, 7) : '—';
}

function durationLabel(seconds: number | null): string {
  if (seconds === null) return '—';
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return `${m}m ${s}s`;
}

function fileNameOf(uri: string, fallback: string): string {
  const parts = uri.split('/');
  return parts[parts.length - 1] || fallback;
}

interface Preview {
  artifactId: string;
  url: string;
  isImage: boolean;
  filename: string;
}

/**
 * S3.2 run history (build bible §10, §15): the project's runs, newest first,
 * with per-run totals/duration, per-test outcomes + failure diagnosis, and
 * artifact preview / download through the Bearer-authenticated `/content` API.
 */
export function RunsView({ projectId }: { projectId: string }) {
  const [runs, setRuns] = useState<RunListItem[] | null>(null);
  const [runsError, setRunsError] = useState<string | null>(null);

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<RunDetail | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);

  const [preview, setPreview] = useState<Preview | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);

  // 1) Load the project's runs once (auto-select the newest).
  useEffect(() => {
    let cancelled = false;
    setRuns(null);
    setRunsError(null);
    listRuns(projectId)
      .then((items) => {
        if (cancelled) return;
        setRuns(items);
        if (items.length > 0) setSelectedId(items[0].id);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setRunsError(err instanceof Error ? err.message : String(err));
      });
    return () => {
      cancelled = true;
    };
  }, [projectId]);

  // 2) Load the selected run's detail (results + artifacts in one call).
  useEffect(() => {
    if (selectedId === null) return;
    let cancelled = false;
    setDetail(null);
    setDetailError(null);
    setPreview(null);
    getRun(selectedId)
      .then((d) => {
        if (!cancelled) setDetail(d);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setDetailError(err instanceof Error ? err.message : String(err));
      });
    return () => {
      cancelled = true;
    };
  }, [selectedId]);

  // Revoke the inline-preview object URL when it changes or on unmount.
  useEffect(() => {
    return () => {
      if (preview) URL.revokeObjectURL(preview.url);
    };
  }, [preview]);

  const handlePreview = useCallback(async (artifact: ArtifactOut) => {
    setPreviewError(null);
    try {
      const blob = await fetchArtifactBlob(artifact);
      const url = URL.createObjectURL(blob);
      setPreview({
        artifactId: artifact.id,
        url,
        isImage: blob.type.startsWith('image/'),
        filename: fileNameOf(artifact.uri, artifact.type),
      });
    } catch (err) {
      setPreview(null);
      setPreviewError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  const handleDownload = useCallback(async (artifact: ArtifactOut) => {
    setPreviewError(null);
    try {
      const blob = await fetchArtifactBlob(artifact);
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = fileNameOf(artifact.uri, artifact.type) || 'artifact';
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      setPreviewError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  return (
    <div className="space-y-6">
      <section className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h2 className="text-2xl font-semibold tracking-tight">Runs</h2>
          <p className="mt-1 max-w-2xl text-sm text-slate-400">
            Execution history for this project — run status, per-test outcomes, failure diagnosis
            and downloadable artifacts (build bible §15, S3.2).
          </p>
        </div>
      </section>

      {runsError !== null && (
        <section className="rounded-xl border border-rose-800 bg-rose-950/40 px-4 py-3 text-sm text-rose-300">
          Could not load runs: {runsError}
        </section>
      )}

      {runs !== null && runs.length === 0 && (
        <section className="rounded-xl border border-slate-800 bg-slate-900/40 p-6 text-sm text-slate-400">
          No runs recorded for this project yet. Runs appear here once a test suite has been
          executed (S3.1).
        </section>
      )}

      {runs !== null && runs.length > 0 && (
        <div className="grid gap-6 lg:grid-cols-[300px_1fr]">
          <div>
            <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-slate-500">
              Run history
            </h3>
            <ul className="space-y-2" aria-label="Run history">
              {runs.map((run) => (
                <li key={run.id}>
                  <button
                    type="button"
                    onClick={() => setSelectedId(run.id)}
                    className={`w-full rounded-xl border p-3 text-left transition ${
                      run.id === selectedId
                        ? 'border-indigo-600 bg-indigo-500/10'
                        : 'border-slate-800 bg-slate-900/40 hover:border-slate-600'
                    }`}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-mono text-xs text-slate-300">
                        {shortSha(run.commit_sha)}
                      </span>
                      <Badge label={run.status} status={run.status} />
                    </div>
                    <p className="mt-1 text-xs text-slate-500">
                      {formatTime(run.started_at ?? run.created_at)}
                    </p>
                  </button>
                </li>
              ))}
            </ul>
          </div>

          <div className="space-y-4">
            {detailError !== null && (
              <section className="rounded-xl border border-rose-800 bg-rose-950/40 px-4 py-3 text-sm text-rose-300">
                Could not load the run: {detailError}
              </section>
            )}

            {detail === null && detailError === null && (
              <p className="text-sm text-slate-400">Loading run…</p>
            )}

            {detail !== null && (
              <>
                <section className="rounded-xl border border-slate-800 bg-slate-900/40 p-4">
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge label={detail.status} status={detail.status} />
                    <span className="font-mono text-xs text-slate-400">
                      {shortSha(detail.commit_sha)}
                    </span>
                    <span className="text-xs text-slate-500">
                      started {formatTime(detail.started_at)} · finished{' '}
                      {formatTime(detail.completed_at)} · {durationLabel(detail.duration_s)}
                    </span>
                  </div>
                  <div className="mt-3 flex flex-wrap gap-2">
                    {Object.entries(detail.totals).map(([key, value]) => (
                      <span
                        key={key}
                        className="rounded-lg border border-slate-700 bg-slate-800/50 px-2 py-1 text-xs text-slate-300"
                      >
                        {key}: {value}
                      </span>
                    ))}
                  </div>
                </section>

                <section>
                  <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-slate-500">
                    Test results ({detail.results.length})
                  </h3>
                  {detail.results.length === 0 ? (
                    <p className="text-sm text-slate-500">No results recorded.</p>
                  ) : (
                    <ul className="space-y-3">
                      {detail.results.map((result, index) => (
                        <li
                          key={result.id}
                          className="rounded-xl border border-slate-800 bg-slate-900/40 p-4"
                        >
                          <div className="flex flex-wrap items-center gap-2">
                            <span className="font-mono text-xs text-slate-500">{index + 1}</span>
                            <span className="font-mono text-xs text-slate-400">
                              {result.test_case_id ?? 'no test case'}
                            </span>
                            <Badge label={result.status} status={result.status} />
                            <span className="text-xs text-slate-500">
                              {durationLabel(result.duration)}
                            </span>
                            {result.artifacts.length > 0 && (
                              <span className="text-xs text-slate-500">
                                · {result.artifacts.length} artifact
                                {result.artifacts.length > 1 ? 's' : ''}
                              </span>
                            )}
                          </div>

                          {result.failure !== null && (
                            <div className="mt-3 rounded-lg border border-rose-900/60 bg-rose-950/20 p-3">
                              <div className="flex flex-wrap items-center gap-2">
                                <span className="text-[11px] font-semibold uppercase tracking-wide text-rose-300">
                                  Failure · {result.failure.category}
                                </span>
                                {result.failure.confidence !== null && (
                                  <span className="text-xs text-rose-300/80">
                                    confidence {(result.failure.confidence * 100).toFixed(0)}%
                                  </span>
                                )}
                                {result.failure.needs_human_approval && (
                                  <span className="text-xs text-rose-300/80">
                                    needs human approval
                                  </span>
                                )}
                              </div>
                              {result.failure.root_cause && (
                                <p className="mt-2 text-xs text-rose-200/90">
                                  {result.failure.root_cause}
                                </p>
                              )}
                              {result.failure.suggested_fix && (
                                <p className="mt-1 text-xs text-rose-200/70">
                                  Suggested fix: {result.failure.suggested_fix}
                                </p>
                              )}
                              {result.failure.evidence.length > 0 && (
                                <ul className="mt-2 list-inside list-disc space-y-0.5 text-xs text-rose-200/60">
                                  {result.failure.evidence.map((evidence, i) => (
                                    <li key={i}>{evidence}</li>
                                  ))}
                                </ul>
                              )}
                            </div>
                          )}
                        </li>
                      ))}
                    </ul>
                  )}
                </section>

                <section>
                  <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-slate-500">
                    Artifacts ({detail.artifacts.length})
                  </h3>
                  {detail.artifacts.length === 0 ? (
                    <p className="text-sm text-slate-500">No artifacts.</p>
                  ) : (
                    <ul className="space-y-2">
                      {detail.artifacts.map((artifact) => (
                        <li
                          key={artifact.id}
                          className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-slate-800 bg-slate-900/40 px-4 py-3"
                        >
                          <div className="flex items-center gap-2">
                            <Badge label={artifact.type} />
                            <span className="font-mono text-xs text-slate-400">{artifact.uri}</span>
                          </div>
                          {artifact.download_url !== null && (
                            <div className="flex items-center gap-2">
                              <button
                                type="button"
                                onClick={() => handlePreview(artifact)}
                                className="rounded-lg border border-slate-700 px-3 py-1.5 text-xs text-slate-300 transition hover:border-slate-500 hover:text-slate-100"
                              >
                                Preview
                              </button>
                              <button
                                type="button"
                                onClick={() => handleDownload(artifact)}
                                className="rounded-lg border border-slate-700 px-3 py-1.5 text-xs text-slate-300 transition hover:border-slate-500 hover:text-slate-100"
                              >
                                Download
                              </button>
                            </div>
                          )}
                        </li>
                      ))}
                    </ul>
                  )}

                  {previewError !== null && (
                    <p className="mt-3 text-sm text-rose-300">Artifact: {previewError}</p>
                  )}

                  {preview !== null && (
                    <div className="mt-3 rounded-xl border border-slate-800 bg-slate-900/40 p-4">
                      <div className="mb-3 flex items-center justify-between gap-2">
                        <span className="font-mono text-xs text-slate-400">{preview.filename}</span>
                        <button
                          type="button"
                          onClick={() => setPreview(null)}
                          className="rounded-lg border border-slate-700 px-3 py-1.5 text-xs text-slate-300 transition hover:border-slate-500 hover:text-slate-100"
                        >
                          Close
                        </button>
                      </div>
                      {preview.isImage ? (
                        <img
                          src={preview.url}
                          alt={preview.filename}
                          className="max-h-96 w-auto rounded-lg border border-slate-800"
                        />
                      ) : (
                        <p className="text-sm text-slate-400">
                          Non-image artifact ({preview.filename}). Use Download to save it.
                        </p>
                      )}
                    </div>
                  )}
                </section>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
