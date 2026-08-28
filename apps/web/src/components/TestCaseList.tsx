import type { TestCaseOut } from '../lib/api';

const TONES: Record<string, string> = {
  high: 'border-orange-700 bg-orange-500/15 text-orange-300',
  medium: 'border-amber-700 bg-amber-500/15 text-amber-300',
  low: 'border-slate-600 bg-slate-500/15 text-slate-300',
  neutral: 'border-indigo-700 bg-indigo-500/15 text-indigo-300',
};

function Badge({ label, tone = 'neutral' }: { label: string; tone?: string }) {
  return (
    <span
      className={`rounded-full border px-2 py-0.5 text-[11px] font-medium ${TONES[tone] ?? TONES.neutral}`}
    >
      {label}
    </span>
  );
}

/**
 * The S1.3 read-back: the persisted test cases of the completed
 * `test_case_generation` job (`GET /api/v1/requirements/{id}`, §10/§12).
 */
export function TestCaseList({ cases }: { cases: TestCaseOut[] }) {
  return (
    <ul className="space-y-3" aria-label="Test cases">
      {cases.map((tc, index) => (
        <li key={tc.id} className="rounded-xl border border-slate-800 bg-slate-900/40 p-4">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-mono text-xs text-slate-500">{index + 1}</span>
            <h4 className="text-sm font-semibold text-slate-100">{tc.title}</h4>
            <Badge label={tc.type} />
            <Badge label={`priority: ${tc.priority}`} tone={tc.priority} />
            <Badge label={`risk: ${tc.risk}`} tone={tc.risk} />
          </div>
          {tc.preconditions.length > 0 && (
            <div className="mt-3">
              <h5 className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">
                Preconditions
              </h5>
              <ul className="mt-1 list-inside list-disc space-y-0.5 text-xs text-slate-300">
                {tc.preconditions.map((item, i) => (
                  <li key={i}>{item}</li>
                ))}
              </ul>
            </div>
          )}
          <div className="mt-3">
            <h5 className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">
              Steps
            </h5>
            <ol className="mt-1 list-inside list-decimal space-y-0.5 text-xs text-slate-300">
              {tc.steps.map((step, i) => (
                <li key={i}>{step}</li>
              ))}
            </ol>
          </div>
          <div className="mt-3">
            <h5 className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">
              Expected results
            </h5>
            <ul className="mt-1 list-inside list-disc space-y-0.5 text-xs text-emerald-300/90">
              {tc.expected_results.map((result, i) => (
                <li key={i}>{result}</li>
              ))}
            </ul>
          </div>
        </li>
      ))}
    </ul>
  );
}
