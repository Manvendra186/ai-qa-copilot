/**
 * Pipeline contract (build bible §4): the six stages every requirement
 * passes through. The real jobs API (`qa_copilot_api.jobs`) emits the same
 * event names; the shell's `useJobEvents` reduces the stream into this shape.
 */
export const PIPELINE_STAGES = [
  'requirement',
  'test_design',
  'automation',
  'execution',
  'failure_analysis',
  'fix',
] as const;

export type StageId = (typeof PIPELINE_STAGES)[number];

export const STAGE_LABELS: Record<StageId, string> = {
  requirement: 'Requirement',
  test_design: 'Test design',
  automation: 'Automation',
  execution: 'Execution',
  failure_analysis: 'Failure analysis',
  fix: 'Fix',
};

export type StageStatus = 'pending' | 'active' | 'done';

export interface StageState {
  id: StageId;
  status: StageStatus;
  /** 0..1 progress within the stage. */
  progress: number;
}

/**
 * Payload shape of every SSE event (build bible §11; `qa_copilot_api.jobs`).
 * Terminal fields: `job.completed` carries `output_ref` (for
 * `test_case_generation` jobs: the persisted requirement id — S1.3; for
 * `run_execution` jobs: the persisted run id — S3); `job.failed` carries
 * `error`. Domain payloads (`knowledge.answer` S5.5, `regression.set` and
 * `run.result` S6.4) are typed in `api.ts` and read by `useJobEvents`.
 */
export interface SsePayload {
  job_id: string;
  project_id?: string;
  stage?: string;
  value?: number;
  stages?: string[];
  output_ref?: string;
  error?: string;
}

export interface EventLogEntry {
  event: string;
  detail: string;
  time: string;
}
