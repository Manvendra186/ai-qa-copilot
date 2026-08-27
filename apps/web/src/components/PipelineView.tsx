import { STAGE_LABELS, type StageState, type StageStatus } from '../lib/pipeline';

const STATUS_STYLES: Record<
  StageStatus,
  { card: string; bar: string; label: string; hint: string }
> = {
  pending: {
    card: 'border-slate-800 bg-slate-900/40',
    bar: 'bg-slate-700',
    label: 'text-slate-500',
    hint: 'waiting',
  },
  active: {
    card: 'border-indigo-500/50 bg-indigo-500/10',
    bar: 'bg-indigo-400',
    label: 'text-indigo-300',
    hint: 'running',
  },
  done: {
    card: 'border-emerald-500/40 bg-emerald-500/10',
    bar: 'bg-emerald-400',
    label: 'text-emerald-300',
    hint: 'complete',
  },
};

export function PipelineView({ stages }: { stages: StageState[] }) {
  return (
    <ol
      className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3"
      aria-label="Pipeline stages"
    >
      {stages.map((stage, index) => {
        const style = STATUS_STYLES[stage.status];
        const percent = Math.round(stage.progress * 100);
        return (
          <li
            key={stage.id}
            className={`rounded-xl border p-4 transition-colors ${style.card}`}
            aria-current={stage.status === 'active' ? 'step' : undefined}
          >
            <div className="flex items-center justify-between gap-2">
              <span className={`text-xs font-semibold uppercase tracking-wide ${style.label}`}>
                {index + 1}. {STAGE_LABELS[stage.id]}
              </span>
              <span className="text-[11px] text-slate-500">{percent}%</span>
            </div>
            <div
              className="mt-3 h-1.5 overflow-hidden rounded-full bg-slate-800"
              role="progressbar"
              aria-valuenow={percent}
              aria-valuemin={0}
              aria-valuemax={100}
              aria-label={STAGE_LABELS[stage.id]}
            >
              <div
                className={`h-full rounded-full transition-all duration-300 ${style.bar}`}
                style={{ width: `${percent}%` }}
              />
            </div>
            <p className="mt-1.5 text-[11px] text-slate-500">{style.hint}</p>
          </li>
        );
      })}
    </ol>
  );
}
