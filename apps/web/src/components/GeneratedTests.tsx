import { useEffect, useState } from 'react';
import { listGeneratedTests, reviewGeneratedTest, type GeneratedTestOut } from '../lib/api';

function messageOf(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

function formatWhen(iso: string | null): string {
  if (!iso) return '';
  const then = new Date(iso);
  if (Number.isNaN(then.getTime())) return iso;
  return then.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });
}

// Same tone scale as RunsView/RequirementHistory — one vocabulary for the app.
const STATUS_TONES: Record<string, string> = {
  pending: 'border-amber-700 bg-amber-500/15 text-amber-300',
  approved: 'border-indigo-700 bg-indigo-500/15 text-indigo-300',
  applied: 'border-emerald-700 bg-emerald-500/15 text-emerald-300',
  rejected: 'border-rose-700 bg-rose-500/15 text-rose-300',
};

function StatusBadge({ status }: { status: string }) {
  const tone = STATUS_TONES[status] ?? 'border-slate-600 bg-slate-500/15 text-slate-300';
  return (
    <span className={`rounded-full border px-2 py-0.5 text-[11px] font-medium ${tone}`}>
      {status}
    </span>
  );
}

/** Per-row outcome of the user's approve/reject click (optimistic feedback). */
interface RowAction {
  state: 'working' | 'done' | 'error';
  message: string;
}

interface Props {
  projectId: string;
}

/**
 * "Generated tests" — the S2.4 review queue
 * (`GET /api/v1/projects/{id}/generated-tests`), newest first.
 *
 * **Approve & write** → `POST /generated-tests/{id}/apply` (legal from
 * `pending` and `approved`): transitions to `applied` *and* writes the file
 * into `<repository_path>/<file_path>`. **Reject** → `/reject` (terminal;
 * re-generating a test case creates a new row). Both are audited (§31.1).
 */
export function GeneratedTests({ projectId }: Props) {
  const [items, setItems] = useState<GeneratedTestOut[] | null>(null);
  const [listError, setListError] = useState<string | null>(null);
  const [openId, setOpenId] = useState<string | null>(null);
  const [actions, setActions] = useState<Record<string, RowAction>>({});
  const [confirmRejectId, setConfirmRejectId] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setItems(null);
    setListError(null);
    setActions({});
    setOpenId(null);
    setConfirmRejectId(null);
    listGeneratedTests(projectId)
      .then((rows) => {
        if (!cancelled) setItems(rows);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setItems([]);
        setListError(messageOf(err));
      });
    return () => {
      cancelled = true;
    };
  }, [projectId, reloadKey]);

  const setAction = (id: string, action: RowAction) =>
    setActions((prev) => ({ ...prev, [id]: action }));

  // Approve = approve the row AND write the Playwright file (one click):
  // `apply` is the state-machine path that does both (S2.4).
  const approveAndWrite = async (row: GeneratedTestOut) => {
    setConfirmRejectId(null);
    setAction(row.id, { state: 'working', message: 'Approving…' });
    try {
      const updated = await reviewGeneratedTest(row.id, 'apply');
      setItems((prev) => (prev ?? []).map((item) => (item.id === row.id ? updated : item)));
      setAction(row.id, {
        state: 'done',
        message: `Approved — ${row.file_path} written to the target repository`,
      });
    } catch (err: unknown) {
      setAction(row.id, { state: 'error', message: `Approve failed: ${messageOf(err)}` });
    }
  };

  const reject = async (row: GeneratedTestOut) => {
    setAction(row.id, { state: 'working', message: 'Rejecting…' });
    try {
      const updated = await reviewGeneratedTest(row.id, 'reject');
      setItems((prev) => (prev ?? []).map((item) => (item.id === row.id ? updated : item)));
      setConfirmRejectId(null);
      setAction(row.id, {
        state: 'done',
        message: 'Rejected — re-generate the test case if you want a new attempt',
      });
    } catch (err: unknown) {
      setConfirmRejectId(null);
      setAction(row.id, { state: 'error', message: `Reject failed: ${messageOf(err)}` });
    }
  };

  return (
    <div className="space-y-6">
      <section className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h2 className="text-2xl font-semibold tracking-tight">Generated tests</h2>
          <p className="mt-1 max-w-2xl text-sm text-slate-400">
            AI-generated Playwright tests awaiting your decision (S2.4). Approving writes the file
            into the target repository; rejecting discards it.
          </p>
        </div>
        <button
          type="button"
          onClick={() => setReloadKey((key) => key + 1)}
          className="rounded-lg border border-slate-700 px-3 py-1.5 text-xs text-slate-300 transition hover:border-slate-500 hover:text-slate-100"
        >
          Refresh
        </button>
      </section>

      <section className="rounded-xl border border-slate-800 bg-slate-900/40">
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-800 px-4 py-3">
          <h3 className="text-sm font-semibold text-slate-100">
            Review queue
            {items !== null && (
              <span className="ml-2 text-xs font-normal text-slate-500">{items.length}</span>
            )}
          </h3>
          <p className="text-xs text-slate-500">Newest first — open a row to read the code</p>
        </div>

        {listError !== null && (
          <p className="px-4 py-3 text-sm text-rose-300">Could not load the queue: {listError}</p>
        )}

        {items === null && listError === null && (
          <p className="px-4 py-3 text-sm text-slate-400">Loading…</p>
        )}

        {items !== null && items.length === 0 && (
          <div className="space-y-1 px-4 py-6 text-sm text-slate-400">
            <p>No generated tests in the queue yet.</p>
            <p className="text-xs text-slate-500">
              Generate one via <span className="font-mono">POST /api/v1/automation/generate</span>{' '}
              with <span className="font-mono">project_id</span>,{' '}
              <span className="font-mono">test_case_id</span> and{' '}
              <span className="font-mono">repository_path</span> — it will appear here for review.
            </p>
          </div>
        )}

        {items !== null && items.length > 0 && (
          <ul className="divide-y divide-slate-800">
            {items.map((row) => {
              const isOpen = openId === row.id;
              const action = actions[row.id];
              const actionable = row.status === 'pending' || row.status === 'approved';
              const confirming = confirmRejectId === row.id;
              const busy = action?.state === 'working';
              return (
                <li key={row.id}>
                  <button
                    type="button"
                    onClick={() => setOpenId(isOpen ? null : row.id)}
                    aria-expanded={isOpen}
                    className="flex w-full flex-wrap items-center gap-x-3 gap-y-1 px-4 py-3 text-left transition hover:bg-slate-800/40"
                  >
                    <StatusBadge status={row.status} />
                    <span className="min-w-0 flex-1 truncate font-mono text-xs text-slate-200">
                      {row.file_path}
                    </span>
                    <span className="text-xs text-slate-500">
                      {row.language} · {row.framework}
                    </span>
                    <span className="text-xs text-slate-500">{formatWhen(row.created_at)}</span>
                    <span className="w-12 text-right text-xs text-slate-500">
                      {isOpen ? 'hide' : 'view'}
                    </span>
                  </button>

                  {isOpen && (
                    <div className="space-y-4 border-t border-slate-800 bg-slate-950/40 px-4 py-4">
                      <div>
                        <p className="mb-2 text-xs text-slate-500">
                          {row.framework} test —{' '}
                          {row.status === 'applied' ? 'written to' : 'will be written to'}{' '}
                          <span className="font-mono text-slate-400">
                            {row.repository_path}/{row.file_path}
                          </span>
                        </p>
                        <pre className="max-h-96 overflow-auto rounded-lg border border-slate-800 bg-slate-950 p-3 text-xs leading-relaxed text-slate-300">
                          <code>{row.content}</code>
                        </pre>
                      </div>

                      {row.notes.length > 0 && (
                        <ul className="list-inside list-disc space-y-0.5 text-xs text-slate-400">
                          {row.notes.map((note, i) => (
                            <li key={i}>{note}</li>
                          ))}
                        </ul>
                      )}

                      {row.status === 'applied' && (
                        <p className="text-xs text-emerald-300">
                          ✓ Written to{' '}
                          <span className="font-mono">
                            {row.repository_path}/{row.file_path}
                          </span>
                          {row.reviewed_at ? ` on ${formatWhen(row.reviewed_at)}` : ''}
                        </p>
                      )}
                      {row.status === 'rejected' && (
                        <p className="text-xs text-rose-300">
                          Rejected{row.review_note ? `: ${row.review_note}` : ''}
                          {row.reviewed_at ? ` on ${formatWhen(row.reviewed_at)}` : ''}
                        </p>
                      )}

                      {actionable && (
                        <div className="flex flex-wrap items-center gap-3">
                          <button
                            type="button"
                            onClick={() => void approveAndWrite(row)}
                            disabled={busy}
                            className="rounded-lg border border-emerald-700 bg-emerald-500/15 px-4 py-1.5 text-sm font-medium text-emerald-200 transition hover:bg-emerald-500/25 disabled:cursor-not-allowed disabled:opacity-60"
                          >
                            {busy ? 'Working…' : '✓ Approve & write test'}
                          </button>
                          {confirming ? (
                            <>
                              <button
                                type="button"
                                onClick={() => void reject(row)}
                                disabled={busy}
                                className="rounded-lg border border-rose-600 bg-rose-500/20 px-4 py-1.5 text-sm font-medium text-rose-200 transition hover:bg-rose-500/30 disabled:cursor-not-allowed disabled:opacity-60"
                              >
                                Confirm reject?
                              </button>
                              <button
                                type="button"
                                onClick={() => setConfirmRejectId(null)}
                                className="rounded-lg border border-slate-700 px-3 py-1.5 text-xs text-slate-300 transition hover:border-slate-500 hover:text-slate-100"
                              >
                                Keep
                              </button>
                            </>
                          ) : (
                            <button
                              type="button"
                              onClick={() => {
                                setActions((prev) => {
                                  const next = { ...prev };
                                  delete next[row.id];
                                  return next;
                                });
                                setConfirmRejectId(row.id);
                              }}
                              disabled={busy}
                              className="rounded-lg border border-rose-800 px-4 py-1.5 text-sm text-rose-300 transition hover:border-rose-600 hover:text-rose-200 disabled:cursor-not-allowed disabled:opacity-60"
                            >
                              ✗ Reject
                            </button>
                          )}
                        </div>
                      )}

                      {action !== undefined && (
                        <p
                          className={
                            action.state === 'error'
                              ? 'text-sm text-rose-300'
                              : action.state === 'done'
                                ? 'text-sm text-emerald-300'
                                : 'text-sm text-slate-400'
                          }
                        >
                          {action.message}
                        </p>
                      )}
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </section>
    </div>
  );
}
