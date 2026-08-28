import { useState, type FormEvent } from 'react';
import type { DesignRequest } from '../lib/api';

interface Props {
  projectId: string;
  disabled: boolean;
  /** Server-side submit error (403/422/500…), if any. */
  error: string | null;
  onSubmit: (body: DesignRequest) => void;
}

const INPUT_CLASS =
  'mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100 outline-none focus:border-indigo-500 disabled:opacity-50';

/**
 * The S1.3 input: the requirement the Test Design Agent builds a suite for
 * (`POST /api/v1/requirements/test-cases` → 202 + job_id, build bible §11).
 */
export function RequirementForm({ projectId, disabled, error, onSubmit }: Props) {
  const [title, setTitle] = useState('');
  const [content, setContent] = useState('');
  const [criteria, setCriteria] = useState('');
  const [localError, setLocalError] = useState<string | null>(null);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (disabled) return;
    if (!title.trim() || !content.trim()) {
      setLocalError('Title and description are required.');
      return;
    }
    setLocalError(null);
    onSubmit({
      project_id: projectId,
      title: title.trim(),
      content: content.trim(),
      acceptance_criteria: criteria
        .split('\n')
        .map((line) => line.trim())
        .filter(Boolean),
    });
  };

  const message = localError ?? error;
  return (
    <form
      onSubmit={submit}
      className="space-y-4 rounded-xl border border-slate-800 bg-slate-900/40 p-5"
    >
      <div>
        <h3 className="text-sm font-semibold text-slate-200">Requirement</h3>
        <p className="mt-1 text-xs text-slate-400">
          The Test Design Agent designs a test suite from this (build bible §19, S1.2/S1.3).
        </p>
      </div>
      <label className="block text-xs">
        <span className="text-slate-300">Title</span>
        <input
          value={title}
          onChange={(event) => setTitle(event.target.value)}
          disabled={disabled}
          placeholder="e.g. Order history"
          className={INPUT_CLASS}
        />
      </label>
      <label className="block text-xs">
        <span className="text-slate-300">Description</span>
        <textarea
          value={content}
          onChange={(event) => setContent(event.target.value)}
          disabled={disabled}
          rows={3}
          placeholder="What should the product do?"
          className={INPUT_CLASS}
        />
      </label>
      <label className="block text-xs">
        <span className="text-slate-300">
          Acceptance criteria <span className="text-slate-500">(one per line, optional)</span>
        </span>
        <textarea
          value={criteria}
          onChange={(event) => setCriteria(event.target.value)}
          disabled={disabled}
          rows={2}
          placeholder={'Orders are listed newest first\nEach order shows status'}
          className={INPUT_CLASS}
        />
      </label>
      {message && <p className="text-xs text-rose-400">{message}</p>}
      <button
        type="submit"
        disabled={disabled}
        className="rounded-lg bg-indigo-500 px-4 py-2 text-sm font-medium text-white transition hover:bg-indigo-400 disabled:opacity-50"
      >
        {disabled ? 'Running…' : 'Design test cases'}
      </button>
    </form>
  );
}
