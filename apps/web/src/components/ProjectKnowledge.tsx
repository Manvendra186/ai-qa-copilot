import { useEffect, useState } from 'react';
import { useJobEvents } from '../hooks/useJobEvents';
import {
  getProjectKnowledgeStatus,
  indexProjectKnowledge,
  listProjectKnowledgeDocuments,
  searchProjectKnowledge,
  type KnowledgeDocumentOut,
  type KnowledgeSearchResult,
  type KnowledgeStatus,
} from '../lib/api';

function messageOf(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

function formatWhen(iso: string | null): string {
  if (!iso) return 'never';
  const then = new Date(iso);
  if (Number.isNaN(then.getTime())) return iso;
  return then.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });
}

interface Props {
  projectId: string;
}

/**
 * "Project knowledge" — the S5.3 project-scoped knowledge base
 * (build bible §7, §14, §19 Phase 5).
 *
 * Index the project's corpus (repository files, when a path is given, plus the
 * persisted requirements / test cases / run history) as a **job** (202 + job_id,
 * build bible §11); search it with lexical BM25 (top-k ≤ 5, §14); browse the
 * stored documents.
 */
export function ProjectKnowledge({ projectId }: Props) {
  const job = useJobEvents();
  const indexing = job.jobId !== null && job.outcome === 'running';

  const [status, setStatus] = useState<KnowledgeStatus | null>(null);
  const [docs, setDocs] = useState<KnowledgeDocumentOut[]>([]);
  const [openDoc, setOpenDoc] = useState<string | null>(null);
  const [listError, setListError] = useState<string | null>(null);

  const [query, setQuery] = useState('');
  const [topK, setTopK] = useState(5);
  const [search, setSearch] = useState<KnowledgeSearchResult | null>(null);
  const [searchError, setSearchError] = useState<string | null>(null);

  const [repoPath, setRepoPath] = useState('');
  const [indexError, setIndexError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  // Initial load, and re-load each time an index job completes (reloadKey bump).
  useEffect(() => {
    let cancelled = false;
    setStatus(null);
    setDocs([]);
    setListError(null);
    Promise.all([
      getProjectKnowledgeStatus(projectId),
      listProjectKnowledgeDocuments(projectId, 200, 0),
    ])
      .then(([s, d]) => {
        if (!cancelled) {
          setStatus(s);
          setDocs(d);
        }
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setStatus(null);
        setDocs([]);
        setListError(messageOf(err));
      });
    return () => {
      cancelled = true;
    };
  }, [projectId, reloadKey]);

  // The index job reached a terminal completed state → refresh the corpus.
  useEffect(() => {
    if (job.outcome === 'completed') setReloadKey((k) => k + 1);
  }, [job.outcome]);

  const onIndex = async () => {
    setIndexError(null);
    try {
      const { job_id } = await indexProjectKnowledge(
        projectId,
        repoPath.trim() ? repoPath.trim() : undefined,
      );
      job.start(job_id);
    } catch (err: unknown) {
      setIndexError(messageOf(err));
    }
  };

  const onSearch = async () => {
    setSearchError(null);
    const q = query.trim();
    if (!q) return;
    try {
      setSearch(await searchProjectKnowledge(projectId, q, topK));
    } catch (err: unknown) {
      setSearchError(messageOf(err));
    }
  };

  const indexErrorMsg = indexError ?? (job.outcome === 'failed' ? job.error : null) ?? null;

  return (
    <div className="space-y-6">
      <section>
        <h2 className="text-2xl font-semibold tracking-tight">Project knowledge</h2>
        <p className="mt-1 max-w-2xl text-sm text-slate-400">
          A project-scoped corpus the agents can search (build bible §7, §14): the project's
          requirements, test cases and run history, plus repository files when a path is given.
          Indexing runs as a job (202 + job_id); search is lexical with top-k capped at 5.
        </p>
      </section>

      {/* --- Index action (S5.3 job) --- */}
      <section className="rounded-xl border border-slate-800 bg-slate-900/40 p-5">
        <div className="flex flex-wrap items-end gap-3">
          <label className="flex flex-1 flex-col gap-1 text-xs text-slate-400">
            Repository path (optional — the project's QA data is always indexed)
            <input
              type="text"
              value={repoPath}
              onChange={(e) => setRepoPath(e.target.value)}
              placeholder="e.g. /path/to/repo"
              disabled={indexing}
              className="rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100 placeholder:text-slate-600 focus:border-slate-500 focus:outline-none disabled:opacity-50"
            />
          </label>
          <button
            type="button"
            onClick={() => {
              void onIndex();
            }}
            disabled={indexing}
            className="rounded-lg border border-slate-700 px-4 py-2 text-sm text-slate-100 transition hover:border-slate-500 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {indexing ? 'Indexing…' : 'Index knowledge'}
          </button>
        </div>
        {indexing && (
          <p className="mt-3 font-mono text-xs text-slate-500">
            job {job.jobId} running — the corpus refreshes when it completes
          </p>
        )}
        {job.outcome === 'completed' && (
          <p className="mt-3 text-xs text-emerald-300">Knowledge indexed and refreshed.</p>
        )}
        {indexErrorMsg !== null && (
          <section className="mt-3 rounded-lg border border-rose-800 bg-rose-950/40 px-3 py-2 text-sm text-rose-300">
            {indexErrorMsg}
          </section>
        )}
      </section>

      {/* --- Status: what is indexed --- */}
      <section className="rounded-xl border border-slate-800 bg-slate-900/40 p-5">
        <h3 className="text-sm font-semibold text-slate-200">What's indexed</h3>
        {status === null ? (
          <p className="mt-2 text-sm text-slate-500">Loading…</p>
        ) : status.document_count === 0 ? (
          <p className="mt-2 text-sm text-slate-500">
            Nothing indexed yet — run "Index knowledge" to build the corpus.
          </p>
        ) : (
          <div className="mt-3 flex flex-wrap items-center gap-x-6 gap-y-2 text-sm">
            <span>
              <span className="text-slate-500">documents </span>
              <span className="font-semibold text-slate-100">{status.document_count}</span>
            </span>
            <span>
              <span className="text-slate-500">sources </span>
              <span className="text-slate-200">
                {status.source_types.length
                  ? status.source_types
                      .map((t) => `${t} (${status.by_source_type[t] ?? 0})`)
                      .join(', ')
                  : '—'}
              </span>
            </span>
            <span>
              <span className="text-slate-500">last indexed </span>
              <span className="text-slate-200">{formatWhen(status.last_indexed_at)}</span>
            </span>
          </div>
        )}
        {listError !== null && <p className="mt-3 text-sm text-rose-300">{listError}</p>}
      </section>

      {/* --- Search the corpus --- */}
      <section className="rounded-xl border border-slate-800 bg-slate-900/40 p-5">
        <h3 className="text-sm font-semibold text-slate-200">Search the corpus</h3>
        <div className="mt-3 flex flex-wrap items-end gap-3">
          <label className="flex flex-1 flex-col gap-1 text-xs text-slate-400">
            Query
            <input
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') void onSearch();
              }}
              placeholder="e.g. login error handling"
              className="rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100 placeholder:text-slate-600 focus:border-slate-500 focus:outline-none"
            />
          </label>
          <label className="flex flex-col gap-1 text-xs text-slate-400">
            Top-k (1–5)
            <select
              value={topK}
              onChange={(e) => setTopK(Number(e.target.value))}
              className="rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100 focus:border-slate-500 focus:outline-none"
            >
              {[1, 2, 3, 4, 5].map((n) => (
                <option key={n} value={n}>
                  {n}
                </option>
              ))}
            </select>
          </label>
          <button
            type="button"
            onClick={() => {
              void onSearch();
            }}
            disabled={!query.trim()}
            className="rounded-lg border border-slate-700 px-4 py-2 text-sm text-slate-100 transition hover:border-slate-500 disabled:cursor-not-allowed disabled:opacity-50"
          >
            Search
          </button>
        </div>

        {searchError !== null && <p className="mt-3 text-sm text-rose-300">{searchError}</p>}

        {search !== null && (
          <div className="mt-4 space-y-3">
            <p className="text-xs text-slate-500">
              {search.hits.length} hit{search.hits.length === 1 ? '' : 's'} ·{' '}
              {search.total_candidates} candidate{search.total_candidates === 1 ? '' : 's'}
              {search.truncated ? ' · truncated' : ''}
            </p>
            {search.hits.length === 0 ? (
              <p className="text-sm text-slate-500">No chunks matched.</p>
            ) : (
              search.hits.map((hit, i) => (
                <div
                  key={`${hit.document_ref}-${hit.chunk_index}-${i}`}
                  className="rounded-lg border border-slate-800 bg-slate-950/60 p-3"
                >
                  <div className="flex flex-wrap items-center gap-2 text-xs">
                    <span className="rounded-full border border-indigo-700 bg-indigo-500/15 px-2 py-0.5 font-medium text-indigo-300">
                      {hit.source_type}
                    </span>
                    <span className="font-medium text-slate-200">{hit.title}</span>
                    <span className="ml-auto font-mono text-slate-500">
                      score {hit.score.toFixed(3)}
                    </span>
                  </div>
                  <p className="mt-2 whitespace-pre-wrap text-sm text-slate-300">{hit.content}</p>
                  {hit.matched_terms.length > 0 && (
                    <p className="mt-2 text-[11px] text-slate-500">
                      matched: {hit.matched_terms.join(', ')}
                    </p>
                  )}
                </div>
              ))
            )}
          </div>
        )}
      </section>

      {/* --- Stored documents --- */}
      <section className="rounded-xl border border-slate-800 bg-slate-900/40 p-5">
        <h3 className="text-sm font-semibold text-slate-200">
          Stored documents{' '}
          {docs.length > 0 && <span className="font-normal text-slate-500">({docs.length})</span>}
        </h3>
        {docs.length === 0 ? (
          <p className="mt-2 text-sm text-slate-500">No documents stored yet.</p>
        ) : (
          <ul className="mt-3 space-y-2">
            {docs.map((doc) => (
              <li
                key={doc.id}
                className="overflow-hidden rounded-lg border border-slate-800 bg-slate-950/60"
              >
                <button
                  type="button"
                  onClick={() => setOpenDoc(openDoc === doc.id ? null : doc.id)}
                  className="flex w-full flex-wrap items-center gap-2 px-3 py-2 text-left"
                >
                  <span className="rounded-full border border-slate-600 bg-slate-500/15 px-2 py-0.5 text-[11px] font-medium text-slate-300">
                    {doc.source_type}
                  </span>
                  <span className="text-sm text-slate-200">{doc.title}</span>
                  <span className="ml-auto text-[11px] text-slate-500">
                    {formatWhen(doc.created_at)}
                  </span>
                  <span className="text-slate-500">{openDoc === doc.id ? '▾' : '▸'}</span>
                </button>
                {openDoc === doc.id && (
                  <div className="border-t border-slate-800 px-3 py-3">
                    <p className="text-[11px] text-slate-500">{doc.source_ref}</p>
                    <p className="mt-2 whitespace-pre-wrap text-xs text-slate-400">{doc.content}</p>
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
