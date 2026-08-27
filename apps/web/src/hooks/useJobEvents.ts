import { useEffect, useReducer, useState } from 'react';
import {
  PIPELINE_STAGES,
  STAGE_LABELS,
  type EventLogEntry,
  type SsePayload,
  type StageId,
  type StageState,
} from '../lib/pipeline';

export type ConnectionStatus = 'connecting' | 'open' | 'error' | 'closed';

export interface JobState {
  jobId: string | null;
  connection: ConnectionStatus;
  done: boolean;
  stages: StageState[];
  log: EventLogEntry[];
}

export interface JobEvents extends JobState {
  replay: () => void;
}

type Action =
  | { type: 'connection'; status: ConnectionStatus }
  | { type: 'job.started'; payload: SsePayload }
  | { type: 'stage.started'; stage: StageId }
  | { type: 'progress'; stage: StageId; value: number }
  | { type: 'stage.completed'; stage: StageId }
  | { type: 'job.completed' }
  | { type: 'reset' };

const MAX_LOG_ENTRIES = 40;
const STAGE_IDS: readonly string[] = PIPELINE_STAGES;

function initialState(): JobState {
  return {
    jobId: null,
    connection: 'connecting',
    done: false,
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
    case 'connection':
      return { ...state, connection: action.status };
    case 'job.started':
      return {
        ...state,
        jobId: action.payload.job_id,
        done: false,
        log: withLog(state, 'job.started', `job ${action.payload.job_id}`),
      };
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
    case 'job.completed':
      return {
        ...state,
        done: true,
        connection: 'closed',
        stages: state.stages.map((s) => ({ ...s, status: 'done', progress: 1 })),
        log: withLog(state, 'job.completed', 'pipeline finished'),
      };
    case 'reset':
      return initialState();
  }
}

/**
 * Subscribes to an SSE endpoint (mocked in S0.7; the real jobs API from
 * S0.9) and reduces the stream into pipeline stage state + an event log.
 */
export function useJobEvents(url: string = '/mock/events'): JobEvents {
  const [state, dispatch] = useReducer(reducer, undefined, initialState);
  const [runId, setRunId] = useState(0);

  useEffect(() => {
    const source = new EventSource(url);
    const parse = (event: MessageEvent<string>): SsePayload => JSON.parse(event.data) as SsePayload;

    const on = (name: string, handler: (payload: SsePayload) => void) => {
      source.addEventListener(name, (event) => handler(parse(event as MessageEvent<string>)));
    };

    on('job.started', (p) => dispatch({ type: 'job.started', payload: p }));
    on('stage.started', (p) => {
      if (isStage(p.stage)) dispatch({ type: 'stage.started', stage: p.stage });
    });
    on('progress', (p) => {
      if (isStage(p.stage) && typeof p.value === 'number') {
        dispatch({ type: 'progress', stage: p.stage, value: p.value });
      }
    });
    on('stage.completed', (p) => {
      if (isStage(p.stage)) dispatch({ type: 'stage.completed', stage: p.stage });
    });
    on('job.completed', () => {
      dispatch({ type: 'job.completed' });
      source.close();
    });

    source.onopen = () => dispatch({ type: 'connection', status: 'open' });
    source.onerror = () => dispatch({ type: 'connection', status: 'error' });

    return () => source.close();
  }, [url, runId]);

  const replay = () => {
    dispatch({ type: 'reset' });
    setRunId((id) => id + 1);
  };

  return { ...state, replay };
}
