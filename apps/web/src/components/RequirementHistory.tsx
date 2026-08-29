import { useEffect, useRef, useState } from 'react';
import {
  getRequirement,
  listRequirements,
  type RequirementOut,
  type RequirementSummary,
} from '../lib/api';
import { TestCaseList } from './TestCaseList';

function messageOf(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

function formatWhen(iso: string | null): string {
  if (!iso) return '';
  const then = new Date(iso);
  if (Number.isNaN(then.getTime())) return '';
  return then.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });
}

// Same tone scale as TestCaseList's badges — one vocabulary for the whole shell.
const RISK_TONES: Record<string, string> = {
  high: 'border-orange-700 bg-orange-500/15 text-orange-300',
  medium: 'border-amber-700 bg-amber-500/15 text-amber-300',
  low: 'border-slate-600 bg-slate-500/15 text-slate-300',
  neutral: 'border-indigo-700 bg-indigo-500/15 text-indigo-300',
};

function RiskBadge({ risk }: { risk: string }) {
  return (
    <span
      className={`rounded-full border px-2 py-0.5 text-[11px] font-medium ${RISK_TONES[risk] ?? RISK_TONES.neutral}`}
    >
      risk: {risk}
    </span>
  );
}

interface Props {
  projectId: string;
  /**
   * Change this (e.g. to the just-completed job's output requirement id) to
   * re-load the list, so a finished design run shows up in the history.
   */
  refreshKey?: string | null;
}

/**
 * "Past requirements" — the project's persisted requirements (S1.3 read-back,
 * `GET /api/v1/projects/{id}/requirements`), newest first. Opening a row
 * fetches the full suite (`GET /api/v1/requirements/{id}`) and renders it
 * with the same `TestCaseList` the fresh-run output uses.
 */
export function RequirementHistory({ projectId, refreshKey = null }: Props) {
  const [items, setItems] = useState<RequirementSummary[] | null>(null);
  const [listError, setListError] = useState<string | null>(null);
  const [openId, setOpenId] = useState<string | null>(null);
  const [openDetail, setOpenDetail] = useState<RequirementOut | null>(null);
  const [openError, setOpenError] = useState<string | null>(null);
  // Guards stale responses: only the row the user *currently* opened may
  // replace the detail pane (quick expand/collapse must not race).
  const requestedRef = useRef<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    requestedRef.current = null;
    setItems(null);
    setListError(null);
    setOpenId(null);
    setOpenDetail(null);
    setOpenError(null);
    listRequirements(projectId)
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
  }, [projectId, refreshKey]);

  const toggle = (id: string) => {
    if (openId === id) {
      requestedRef.current = null;
      setOpenId(null);
      setOpenDetail(null);
      setOpenError(null);
      return;
    }
    requestedRef.current = id;
    setOpenId(id);
    setOpenDetail(null);
    setOpenError(null);
    getRequirement(id)
      .then((req) => {
        if (requestedRef.current === id) setOpenDetail(req);
      })
      .catch((err: unknown) => {
        if (requestedRef.current === id) setOpenError(messageOf(err));
      });
  };

  return (
    <section
      aria-label="Past requirements"
      className="rounded-xl border border-slate-800 bg-slate-900/40"
    >
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-800 px-4 py-3">
        <h3 className="text-sm font-semibold text-slate-100">
          Past requirements
          {items !== null && (
            <span className="ml-2 text-xs font-normal text-slate-500">{items.length}</span>
          )}
        </h3>
        <p className="text-xs text-slate-500">Newest first — click one to review its test cases</p>
      </div>

      {listError !== null && (
        <p className="px-4 py-3 text-sm text-rose-300">Could not load the history: {listError}</p>
      )}

      {items === null && listError === null && (
        <p className="px-4 py-3 text-sm text-slate-400">Loading…</p>
      )}

      {items !== null && items.length === 0 && (
        <p className="px-4 py-3 text-sm text-slate-400">
          No requirements yet — the suites you design above will show up here.
        </p>
      )}

      {items !== null && items.length > 0 && (
        <ul className="divide-y divide-slate-800">
          {items.map((item) => {
            const isOpen = openId === item.id;
            return (
              <li key={item.id}>
                <button
                  type="button"
                  onClick={() => toggle(item.id)}
                  aria-expanded={isOpen}
                  className="flex w-full flex-wrap items-center gap-x-3 gap-y-1 px-4 py-3 text-left transition hover:bg-slate-800/40"
                >
                  <span className="min-w-0 flex-1 truncate text-sm font-medium text-slate-100">
                    {item.title}
                  </span>
                  <RiskBadge risk={item.risk} />
                  <span className="text-xs text-slate-400">
                    {item.test_case_count} test case
                    {item.test_case_count === 1 ? '' : 's'}
                  </span>
                  <span className="text-xs text-slate-500">{formatWhen(item.created_at)}</span>
                  <span className="w-12 text-right text-xs text-slate-500">
                    {isOpen ? 'hide' : 'view'}
                  </span>
                </button>

                {isOpen && (
                  <div className="border-t border-slate-800 bg-slate-950/40 px-4 py-4">
                    {openError !== null && (
                      <p className="text-sm text-rose-300">
                        Could not open this requirement: {openError}
                      </p>
                    )}

                    {openDetail === null && openError === null && (
                      <p className="text-sm text-slate-400">Loading suite…</p>
                    )}

                    {openDetail !== null && (
                      <div className="space-y-4">
                        <p className="text-sm text-slate-300">{openDetail.content}</p>
                        {openDetail.acceptance_criteria.length > 0 && (
                          <ul className="list-inside list-disc space-y-0.5 text-xs text-slate-400">
                            {openDetail.acceptance_criteria.map((criterion, i) => (
                              <li key={i}>{criterion}</li>
                            ))}
                          </ul>
                        )}
                        <TestCaseList cases={openDetail.test_cases} />
                      </div>
                    )}
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
