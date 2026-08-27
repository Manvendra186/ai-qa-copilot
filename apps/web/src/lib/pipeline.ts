/**
 * Pipeline contract (build bible §4): the six stages every requirement
 * passes through. The mock SSE server (`vite.config.ts`) and the shell
 * agree on this list; S0.9 serves the same shape from the real API.
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

/** Payload shape of every SSE event (S0.7 mock and the future S0.9 API). */
export interface SsePayload {
  job_id: string;
  stage?: string;
  value?: number;
  stages?: string[];
}

export interface EventLogEntry {
  event: string;
  detail: string;
  time: string;
}
