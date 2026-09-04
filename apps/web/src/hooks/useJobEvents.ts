import { useCallback, useEffect, useReducer, useState } from 'react';
import { streamJobEvents } from '../lib/api';
import {
  PIPELINE_STAGES,
  STAGE_LABELS,
  type EventLogEntry,
  type SsePayload,
  type StageId,
  type StageState,
} from '../lib/pipeline';

export type ConnectionStatus = 'connecting' | 'open' | 'error' | 'closed';
export type JobOutcome = 'running' | 'completed' | 'failed';

export interface JobState {
  jobId: string | null;
  connection: ConnectionStatus;
  outcome: JobOutcome;
  /** Terminal `output_ref` (for `test_case_generation` jobs: requirement id). */
  outputRef: string | null;
  /** Terminal or stream error message, if any. */
  error: string | null;
  /** The `knowledge.answer` payload, when the job emitted one (S5.5 Ask). */
  lastAnswer: Record<string, unknown> | null;
  /** The `regression.set` payload (recommendation/impact/ranking/advice, S6.4). */
  lastRegressionSet: Record<string, unknown> | null;
  /** The `regression.comment` payload (PR comment upsert outcome, S7.2). */
  lastRegressionComment: Record<string, unknown> | null;
  /** The `run.result` payload (run_id/status/totals, S6.4 "Run this set"). */
  lastRunResult: Record<string, unknown> | null;
  stages: StageState[];
  log: EventLogEntry[];
}

export interface JobEvents extends JobState {
  /** Open the SSE stream for the given job (resets the run state). */
  start: (jobId: string) => void;
  /** Abort the in-flight stream (if any) and clear the run state. */
  reset: () => void;
}

type Action =
  | { type: 'run.start'; jobId: string }
  | { type: 'run.reset' }
  | { type: 'connection'; status: ConnectionStatus }
  | { type: 'job.started' }
  | { type: 'stage.started'; stage: StageId }
  | { type: 'progress'; stage: StageId; value: number }
  | { type: 'stage.completed'; stage: StageId }
  | { type: 'knowledge.answer'; payload: Record<string, unknown> }
  | { type: 'regression.set'; payload: Record<string, unknown> }
  | { type: 'regression.comment'; payload: Record<string, unknown> }
  | { type: 'run.result'; payload: Record<string, unknown> }
  | { type: 'job.completed'; outputRef: string | null }
  | { type: 'job.failed'; error: string }
  | { type: 'stream.error'; message: string }
  | { type: 'stream.done' };

const MAX_LOG_ENTRIES = 40;
const STAGE_IDS: readonly string[] = PIPELINE_STAGES;

function initialState(): JobState {
  return {
    jobId: null,
    connection: 'connecting',
    outcome: 'running',
    outputRef: null,
    error: null,
    lastAnswer: null,
    lastRegressionSet: null,
    lastRegressionComment: null,
    lastRunResult: null,
    stages: PIPELINE_STAGES.map((id) => ({ id, status: 'pending', progress: 0 })),
    log: [],
  };
}

function now(): string {
  return new Date().toLocaleTimeString([], { hour12: false });
}

function withLog(state: JobState, event: string, detail: string): EventLogEntry[] {
  return [...state.log.slice(-(MAX_LOG_ENTRIES - 1)), { event, detail, time: now() }];
}

function isStage(value: unknown): value is StageId {
  return typeof value === 'string' && STAGE_IDS.includes(value);
}

function withStage(
  stages: StageState[],
  id: StageId,
  update: (stage: StageState) => StageState,
): StageState[] {
  return stages.map((stage) => (stage.id === id ? update(stage) : stage));
}

function reducer(state: JobState, action: Action): JobState {
  switch (action.type) {
    case 'run.start':
      return { ...initialState(), jobId: action.jobId, connection: 'connecting' };
    case 'run.reset':
      return initialState();
    case 'connection':
      if (state.connection === action.status) return state;
      return { ...state, connection: action.status };
    case 'job.started':
      return { ...state, log: withLog(state, 'job.started', `job ${state.jobId ?? ''}`) };
    case 'stage.started':
      return {
        ...state,
        stages: withStage(state.stages, action.stage, (s) => ({
          ...s,
          status: 'active',
          progress: 0,
        })),
        log: withLog(state, 'stage.started', STAGE_LABELS[action.stage]),
      };
    case 'progress': {
      const clamped = Math.min(1, Math.max(0, action.value));
      return {
        ...state,
        stages: withStage(state.stages, action.stage, (s) => ({
          ...s,
          status: 'active',
          progress: clamped,
        })),
        log: withLog(
          state,
          'progress',
          `${STAGE_LABELS[action.stage]} ${Math.round(clamped * 100)}%`,
        ),
      };
    }
    case 'stage.completed':
      return {
        ...state,
        stages: withStage(state.stages, action.stage, (s) => ({
          ...s,
          status: 'done',
          progress: 1,
        })),
        log: withLog(state, 'stage.completed', STAGE_LABELS[action.stage]),
      };
    case 'knowledge.answer':
      return {
        ...state,
        lastAnswer: action.payload,
        log: withLog(state, 'knowledge.answer', 'answer received'),
      };
    case 'regression.set':
      return {
        ...state,
        lastRegressionSet: action.payload,
        log: withLog(state, 'regression.set', 'regression set received'),
      };
    case 'regression.comment':
      return {
        ...state,
        lastRegressionComment: action.payload,
        log: withLog(state, 'regression.comment', 'PR comment posted'),
      };
    case 'run.result':
      return {
        ...state,
        lastRunResult: action.payload,
        log: withLog(state, 'run.result', 'run result received'),
      };
    case 'job.completed':
      return {
        ...state,
        outcome: 'completed',
        connection: 'closed',
        outputRef: action.outputRef,
        stages: state.stages.map((s) => ({ ...s, status: 'done', progress: 1 })),
        log: withLog(
          state,
          'job.completed',
          action.outputRef ? `requirement ${action.outputRef}` : 'pipeline finished',
        ),
      };
    case 'job.failed':
      return {
        ...state,
        outcome: 'failed',
        connection: 'error',
        error: action.error,
        log: withLog(state, 'job.failed', action.error),
      };
    case 'stream.error':
      return {
        ...state,
        outcome: 'failed',
        connection: 'error',
        error: action.message,
        log: withLog(state, 'stream.error', action.message),
      };
    case 'stream.done':
      // Server closed without a terminal event (defensive — shouldn't happen).
      if (state.outcome !== 'running') return state;
      return {
        ...state,
        connection: 'closed',
        log: withLog(state, 'stream.closed', 'stream ended without a terminal event'),
      };
  }
}

/**
 * Live job feed (build bible §11): `start(jobId)` opens
 * `GET /api/v1/events?job_id=...` (fetch + streaming reader with the Bearer
 * header — see `lib/api.ts`) and reduces the event stream into pipeline
 * stage state, an event log, and the terminal `output_ref`
 * (the persisted requirement id for `test_case_generation` jobs).
 */
export function useJobEvents(): JobEvents {
  const [state, dispatch] = useReducer(reducer, undefined, initialState);
  const [active, setActive] = useState<{ jobId: string; run: number } | null>(null);

  const start = useCallback((jobId: string) => {
    // `run` counter: re-starting the *same* job id still re-runs the effect.
    setActive((prev) => ({ jobId, run: (prev?.run ?? 0) + 1 }));
  }, []);

  const reset = useCallback(() => {
    // setActive(null) → the effect's cleanup aborts the in-flight stream.
    setActive(null);
    dispatch({ type: 'run.reset' });
  }, []);

  useEffect(() => {
    if (active === null) return;
    const controller = new AbortController();
    dispatch({ type: 'run.start', jobId: active.jobId });

    void (async () => {
      try {
        await streamJobEvents(active.jobId, controller.signal, (event, data) => {
          dispatch({ type: 'connection', status: 'open' });
          const payload = data as unknown as SsePayload;
          switch (event) {
            case 'job.started':
              dispatch({ type: 'job.started' });
              break;
            case 'stage.started':
              if (isStage(payload.stage)) dispatch({ type: 'stage.started', stage: payload.stage });
              break;
            case 'progress':
              if (isStage(payload.stage) && typeof payload.value === 'number') {
                dispatch({ type: 'progress', stage: payload.stage, value: payload.value });
              }
              break;
            case 'stage.completed':
              if (isStage(payload.stage))
                dispatch({ type: 'stage.completed', stage: payload.stage });
              break;
            case 'knowledge.answer':
              dispatch({ type: 'knowledge.answer', payload: data });
              break;
            case 'regression.set':
              dispatch({ type: 'regression.set', payload: data });
              break;
            case 'regression.comment':
              dispatch({ type: 'regression.comment', payload: data });
              break;
            case 'run.result':
              dispatch({ type: 'run.result', payload: data });
              break;
            case 'job.completed':
              dispatch({
                type: 'job.completed',
                outputRef: typeof payload.output_ref === 'string' ? payload.output_ref : null,
              });
              break;
            case 'job.failed':
              dispatch({ type: 'job.failed', error: payload.error ?? 'job failed' });
              break;
            default:
              break;
          }
        });
        dispatch({ type: 'stream.done' });
      } catch (err) {
        if (err instanceof DOMException && err.name === 'AbortError') return;
        const message = err instanceof Error ? err.message : String(err);
        dispatch({ type: 'stream.error', message });
      }
    })();

    return () => controller.abort();
  }, [active]);

  return { ...state, start, reset };
}
