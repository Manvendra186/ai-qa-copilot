/**
 * API client for the jobs API (build bible §11, §31.3; S1.3 shell wiring).
 *
 * All calls hit the same-origin `/api/v1` — in dev the Vite proxy forwards
 * them to FastAPI (see `vite.config.ts`). The Bearer token is kept in
 * `localStorage` (dev-mode auth; SSO is Phase 8, §31.3).
 *
 * The SSE feed (`GET /api/v1/events?job_id=...`) requires an
 * `Authorization` header, which `EventSource` cannot send — so the stream
 * is read with `fetch` + a streaming reader, parsing standard SSE frames.
 * The server closes the stream after the terminal event
 * (`job.completed` / `job.failed`), which ends the read loop.
 */

const BASE = '/api/v1';
const TOKEN_KEY = 'qa-copilot.token';

export class ApiError extends Error {
  readonly status: number;
  constructor(status: number, message: string) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

// --- wire types (mirror `qa_copilot_api.schemas`) -----------------------------

export interface UserOut {
  id: string;
  email: string;
  role: string;
}

export interface ProjectRef {
  id: string;
  name: string;
  role: string;
}

export interface MeResponse {
  user: UserOut;
  projects: ProjectRef[];
}

export interface LoginResponse extends MeResponse {
  token: string;
  token_type: string;
  expires_in: number;
}

export interface JobCreated {
  job_id: string;
  status: string;
}

export interface TestCaseOut {
  id: string;
  title: string;
  type: string;
  priority: string;
  preconditions: string[];
  steps: string[];
  expected_results: string[];
  risk: string;
  created_at: string | null;
}

export interface RequirementOut {
  id: string;
  project_id: string;
  title: string;
  content: string;
  acceptance_criteria: string[];
  risk: string;
  created_at: string | null;
  test_cases: TestCaseOut[];
}

export interface RequirementSummary {
  id: string;
  title: string;
  risk: string;
  created_at: string | null;
  test_case_count: number;
}

export interface DesignRequest {
  project_id: string;
  title: string;
  content: string;
  acceptance_criteria: string[];
}

// --- token storage (dev-mode) ---------------------------------------------------

export function getToken(): string | null {
  if (typeof localStorage === 'undefined') return null;
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string | null): void {
  if (typeof localStorage === 'undefined') return;
  if (token) localStorage.setItem(TOKEN_KEY, token);
  else localStorage.removeItem(TOKEN_KEY);
}

// --- request plumbing -----------------------------------------------------------

function headers(extra: Record<string, string> = {}): Record<string, string> {
  const token = getToken();
  return {
    'Content-Type': 'application/json',
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
    ...extra,
  };
}

async function errorBody(res: Response): Promise<string> {
  try {
    const body = (await res.json()) as { detail?: unknown };
    if (typeof body.detail === 'string') return body.detail;
    if (body && typeof body === 'object') return JSON.stringify(body);
  } catch {
    // non-JSON body — fall through
  }
  return res.statusText || `HTTP ${res.status}`;
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: headers(init.headers as Record<string, string> | undefined),
  });
  if (!res.ok) throw new ApiError(res.status, await errorBody(res));
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

// --- endpoints -------------------------------------------------------------------

/** `POST /auth/login` — dev-mode email + password (§31.3). */
export function login(email: string, password: string): Promise<LoginResponse> {
  return request<LoginResponse>('/auth/login', {
    method: 'POST',
    body: JSON.stringify({ email, password }),
  });
}

/** `GET /auth/me` — restore a stored session (401 → invalid/expired token). */
export function me(): Promise<MeResponse> {
  return request<MeResponse>('/auth/me');
}

/** `POST /requirements/test-cases` → 202 + `{job_id}` (§11). */
export function createTestCaseJob(body: DesignRequest): Promise<JobCreated> {
  return request<JobCreated>('/requirements/test-cases', {
    method: 'POST',
    body: JSON.stringify(body),
  });
}

/** `GET /requirements/{id}` — persisted requirement + its test cases (S1.3). */
export function getRequirement(id: string): Promise<RequirementOut> {
  return request<RequirementOut>(`/requirements/${encodeURIComponent(id)}`);
}

/** `GET /projects/{id}/requirements` — the project's past requirements, newest first. */
export function listRequirements(projectId: string): Promise<RequirementSummary[]> {
  return request<RequirementSummary[]>(`/projects/${encodeURIComponent(projectId)}/requirements`);
}

// --- S2.4: generated-test review queue (§10, §19 S2.4) -----------------------

export interface GeneratedTestOut {
  id: string;
  project_id: string;
  job_id: string | null;
  test_case_id: string | null;
  file_path: string | null;
  file_path_pattern: string | null;
  language: string | null;
  framework: string | null;
  content: string | null;
  notes: string[];
  repository_path: string | null;
  /** `pending` | `approved` | `rejected` | `applied`. */
  status: string;
  reviewed_by: string | null;
  reviewed_at: string | null;
  review_note: string | null;
  created_at: string | null;
  updated_at: string | null;
}

/** `GET /projects/{id}/generated-tests` — the review queue, newest first (S2.4). */
export function listGeneratedTests(projectId: string): Promise<GeneratedTestOut[]> {
  return request<GeneratedTestOut[]>(`/projects/${encodeURIComponent(projectId)}/generated-tests`);
}

/** `GET /generated-tests/{id}` — one review row, full content included (S2.4). */
export function getGeneratedTest(id: string): Promise<GeneratedTestOut> {
  return request<GeneratedTestOut>(`/generated-tests/${encodeURIComponent(id)}`);
}

/**
 * `POST /generated-tests/{id}/{action}` — a human review decision (S2.4,
 * `member` or above; all accept an optional audit `note`).
 *
 * - `approve` — `pending → approved`
 * - `reject`  — terminal; re-generating creates a new row
 * - `apply`   — `pending | approved → applied` **and writes the file** into
 *               `<repository_path>/<file_path>` (an existing target file is a
 *               409 — V1 policy: no silent overwrite)
 *
 * The UI's "Approve & write" button calls `apply` directly: it is the legal
 * one-step path from `pending`, and it is what actually writes the Playwright
 * file into the target repository.
 */
export function reviewGeneratedTest(
  id: string,
  action: 'approve' | 'reject' | 'apply',
  note?: string,
): Promise<GeneratedTestOut> {
  return request<GeneratedTestOut>(`/generated-tests/${encodeURIComponent(id)}/${action}`, {
    method: 'POST',
    body: JSON.stringify(note ? { note } : {}),
  });
}

// --- S3.2: run history, results, artifacts (§10, §15) -------------------------

export interface FailureOut {
  id: string;
  category: string;
  root_cause: string | null;
  confidence: number | null;
  evidence: string[];
  suggested_fix: string | null;
  needs_human_approval: boolean;
}

export interface ArtifactOut {
  id: string;
  test_result_id: string;
  type: string;
  uri: string;
  metadata: Record<string, unknown>;
  created_at: string;
  /** API path that streams the file bytes (`/api/v1/runs/{id}/artifacts/{id}/content`). */
  download_url: string | null;
}

export interface TestResultOut {
  id: string;
  run_id: string;
  test_case_id: string | null;
  status: string;
  duration: number | null;
  failure: FailureOut | null;
  artifacts: ArtifactOut[];
}

export interface RunListItem {
  id: string;
  project_id: string;
  commit_sha: string | null;
  status: string;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
}

export interface RunDetail {
  id: string;
  project_id: string;
  commit_sha: string | null;
  status: string;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
  duration_s: number | null;
  totals: Record<string, number>;
  results: TestResultOut[];
  artifacts: ArtifactOut[];
}

/** `GET /projects/{id}/runs` — a project's runs, newest first (S3.2). */
export function listRuns(projectId: string): Promise<RunListItem[]> {
  return request<RunListItem[]>(`/projects/${encodeURIComponent(projectId)}/runs`);
}

/** `GET /runs/{id}` — run + results + artifacts (S3.2). */
export function getRun(runId: string): Promise<RunDetail> {
  return request<RunDetail>(`/runs/${encodeURIComponent(runId)}`);
}

/** `GET /runs/{id}/results` — per-test outcomes + diagnosis (S3.2). */
export function listResults(runId: string): Promise<TestResultOut[]> {
  return request<TestResultOut[]>(`/runs/${encodeURIComponent(runId)}/results`);
}

/** `GET /runs/{id}/artifacts` — artifact rows (S3.2). */
export function listArtifacts(runId: string): Promise<ArtifactOut[]> {
  return request<ArtifactOut[]>(`/runs/${encodeURIComponent(runId)}/artifacts`);
}

/**
 * Fetch an artifact's file bytes through its Bearer-authenticated `download_url`.
 *
 * A plain `<a href>` / `<img src>` cannot send the `Authorization` header
 * (dev-mode auth is a Bearer token, not a cookie), so the bytes are fetched
 * with the shared `headers()` and the caller turns the blob into an object URL
 * (inline preview) or a download.
 */
export async function fetchArtifactBlob(artifact: ArtifactOut): Promise<Blob> {
  if (!artifact.download_url) throw new ApiError(400, 'this artifact has no download_url');
  const res = await fetch(artifact.download_url, { headers: headers() });
  if (!res.ok) throw new ApiError(res.status, await errorBody(res));
  return res.blob();
}

// --- SSE ------------------------------------------------------------------------

/**
 * Stream one job's SSE events until the terminal event (the server closes
 * the stream after `job.completed` / `job.failed`). `onEvent` receives the
 * `event:` name + the parsed `data:` JSON. Aborts with the passed signal.
 */
export async function streamJobEvents(
  jobId: string,
  signal: AbortSignal,
  onEvent: (event: string, data: Record<string, unknown>) => void,
): Promise<void> {
  const res = await fetch(`${BASE}/events?job_id=${encodeURIComponent(jobId)}`, {
    headers: headers(),
    signal,
  });
  if (!res.ok) throw new ApiError(res.status, await errorBody(res));
  const reader = res.body?.getReader();
  if (!reader) throw new ApiError(0, 'response body is not readable');

  const decoder = new TextDecoder();
  let buffer = '';
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let sep: number;
    while ((sep = buffer.indexOf('\n\n')) !== -1) {
      const frame = buffer.slice(0, sep);
      buffer = buffer.slice(sep + 2);
      const parsed = parseSseFrame(frame);
      if (parsed) onEvent(parsed.event, parsed.data);
    }
  }
}

/** Parse one SSE frame (`event:` + `data:` lines); comment frames → null. */
function parseSseFrame(frame: string): { event: string; data: Record<string, unknown> } | null {
  let event = 'message';
  const dataLines: string[] = [];
  for (const line of frame.split('\n')) {
    if (!line || line.startsWith(':')) continue; // blank or comment (keepalive)
    const colon = line.indexOf(':');
    const key = colon === -1 ? line : line.slice(0, colon);
    const value = colon === -1 ? '' : line.slice(colon + 1).replace(/^ /, '');
    if (key === 'event') event = value;
    else if (key === 'data') dataLines.push(value);
  }
  const dataText = dataLines.join('\n');
  if (!dataText) return null;
  try {
    return { event, data: JSON.parse(dataText) as Record<string, unknown> };
  } catch {
    return null; // non-JSON payload — nothing to reduce
  }
}

// --- S5.3: project knowledge (build bible §7, §14, §19 Phase 5) --------------

export interface KnowledgeIndexRequest {
  repository_path?: string;
}

export interface KnowledgeHit {
  score: number;
  document_ref: string;
  source_type: string;
  title: string;
  chunk_index: number;
  content: string;
  metadata: Record<string, unknown>;
  matched_terms: string[];
}

export interface KnowledgeSearchResult {
  query: string;
  total_candidates: number;
  truncated: boolean;
  hits: KnowledgeHit[];
}

export interface KnowledgeStatus {
  document_count: number;
  by_source_type: Record<string, number>;
  source_types: string[];
  last_indexed_at: string | null;
}

export interface KnowledgeDocumentOut {
  id: string;
  source_type: string;
  title: string;
  source_ref: string;
  content: string;
  metadata: Record<string, unknown>;
  created_at: string | null;
}

// --- S5.5: project-knowledge Ask (build bible §7, §19 Phase 5) ----------------

export interface KnowledgeCitation {
  document_ref: string;
  source_type: string;
  title: string;
  score: number;
}

/** The grounded answer delivered over the `knowledge.answer` SSE event. */
export interface KnowledgeAnswer {
  in_scope: boolean;
  answer: string | null;
  citations: KnowledgeCitation[];
  confidence: number;
}

/** `POST /projects/{id}/knowledge/index` → 202 + `{job_id}` (S5.3). */
export function indexProjectKnowledge(
  projectId: string,
  repositoryPath?: string,
): Promise<JobCreated> {
  return request<JobCreated>(`/projects/${encodeURIComponent(projectId)}/knowledge/index`, {
    method: 'POST',
    body: JSON.stringify(repositoryPath ? { repository_path: repositoryPath } : {}),
  });
}

/** `GET /projects/{id}/knowledge/status` — what is indexed (S5.3). */
export function getProjectKnowledgeStatus(projectId: string): Promise<KnowledgeStatus> {
  return request<KnowledgeStatus>(`/projects/${encodeURIComponent(projectId)}/knowledge/status`);
}

/** `GET /projects/{id}/knowledge?q=...&top_k=...` — project-specific chunks (S5.3). */
export function searchProjectKnowledge(
  projectId: string,
  query: string,
  topK = 5,
): Promise<KnowledgeSearchResult> {
  const params = new URLSearchParams({ q: query, top_k: String(topK) });
  return request<KnowledgeSearchResult>(
    `/projects/${encodeURIComponent(projectId)}/knowledge?${params.toString()}`,
  );
}

/** `GET /projects/{id}/knowledge/documents` — stored documents, newest first (S5.3). */
export function listProjectKnowledgeDocuments(
  projectId: string,
  limit = 100,
  offset = 0,
): Promise<KnowledgeDocumentOut[]> {
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  return request<KnowledgeDocumentOut[]>(
    `/projects/${encodeURIComponent(projectId)}/knowledge/documents?${params.toString()}`,
  );
}

/** `GET /projects/{id}/knowledge/documents/{id}` — one stored document (S5.3). */
export function getKnowledgeDocument(
  projectId: string,
  documentId: string,
): Promise<KnowledgeDocumentOut> {
  return request<KnowledgeDocumentOut>(
    `/projects/${encodeURIComponent(projectId)}/knowledge/documents/${encodeURIComponent(documentId)}`,
  );
}

/**
 * `POST /projects/{id}/knowledge/ask` → 202 + `{job_id}` (S5.5).
 *
 * The grounded answer (with citations) is **not** in this response — it rides
 * the `knowledge.answer` SSE event on the job's `/events` stream (read via
 * `streamJobEvents`). The job's terminal `output_ref` is the stable
 * `knowledge-ask://{projectId}` reference.
 */
export function askKnowledge(projectId: string, question: string): Promise<JobCreated> {
  return request<JobCreated>(`/projects/${encodeURIComponent(projectId)}/knowledge/ask`, {
    method: 'POST',
    body: JSON.stringify({ question }),
  });
}

// --- S6.4: regression analysis + "Run this set" (build bible §7, §19 Phase 6) --

export interface RegressionAnalysisRequest {
  /** Server-local path to the repository checkout (for S6.1 impact). */
  repository_path: string;
  /** Exactly one source: changed files (the diff)… */
  files?: string[];
  /** …or a git `base_ref`/`head_ref` pair (diff computed server-side)… */
  base_ref?: string;
  head_ref?: string;
  /** …or a GitHub pull request (S7.2, resolved via the project's
   * GitHub integration; requires `repository_path` for S6.1 impact). */
  pull_request?: PullRequestRef;
  /** Top-N recommendation size (1..500, default 10). */
  top_n?: number;
}

/** A GitHub pull request reference (S7.2). */
export interface PullRequestRef {
  owner: string;
  repo: string;
  number: number;
}

/** S7.2 — request body for `POST /projects/{id}/regression/pr-comment`. */
export interface RegressionPrCommentRequest {
  pull_request: PullRequestRef;
  /** Server-local repo checkout path (S6.1 impact). */
  repository_path: string;
  top_n?: number;
}

/** S7.2 — the `regression.comment` SSE payload (idempotent upsert outcome). */
export interface RegressionPrCommentResult {
  action: 'created' | 'updated' | 'unchanged';
  comment_id: number | null;
  html_url: string | null;
  owner: string;
  repo: string;
  number: number;
}

export interface RunRequest {
  /** Server-local path to the repository checkout (Playwright target dir). */
  repository_path: string;
  /** Repo-relative Playwright test files to run (from the regression set). */
  tests: string[];
  /** Playwright run timeout in seconds (default 600, max 3600). */
  timeout_s?: number;
}

/** S6.5 advisor brief (degrades safely to the stub when no LLM is configured). */
export interface RegressionAdvice {
  source: string;
  summary: string;
}

/** S6.2 — deterministic per-test history statistics. */
export interface TestHistoryStats {
  test_key: string;
  executions: number;
  passed: number;
  failed: number;
  flaky: number;
  skipped: number;
  flakiness_rate: number;
  failure_rate: number;
  recent_failure_rate: number;
  is_flaky: boolean;
  is_failing: boolean;
  insufficient_samples: boolean;
}

/** S6.2 — one ranked test in the risk set. */
export interface TestRisk {
  test_key: string;
  risk_score: number;
  signals: string[];
  stats: TestHistoryStats;
  impact_kind: string | null;
  requirement_risk: string | null;
  test_case_priority: string | null;
}

export interface RiskRanking {
  project_id: string;
  ranked: TestRisk[];
  min_sample: number;
  recent_window: number;
  flaky_threshold: number;
  failing_threshold: number;
}

/** S6.3 — one ranked regression recommendation. */
export interface RecommenderItem {
  test_key: string;
  stats: TestHistoryStats;
  rank: number;
  risk_score: number;
  impact_kind: string | null;
  changed_files: string[];
  requirement_risk: string | null;
  test_case_priority: string | null;
  rationale: string[];
}

export interface RecommendationSet {
  project_id: string;
  changed: string[];
  recommendations: RecommenderItem[];
  top_n: number;
}

/** S6.1 — one impacted test file, with why. */
export interface ImpactedTest {
  path: string;
  kinds: string[];
  changed_files: string[];
  test_case_ids: string[];
  requirement_ids: string[];
  signals: string[];
}

export interface ImpactSet {
  changed: string[];
  impacted: ImpactedTest[];
  test_files_scanned: number;
  notes: string[];
}

/** The regression payload delivered over the `regression.set` SSE event. */
export interface RegressionSet {
  recommendation: RecommendationSet;
  impact: ImpactSet;
  ranking: RiskRanking;
  advice: RegressionAdvice | null;
}

export interface RunTotals {
  total: number;
  passed: number;
  failed: number;
  flaky: number;
  skipped: number;
}

/** The run payload delivered over the `run.result` SSE event. */
export interface RunResult {
  /** The persisted run id (the S3.2 `GET /runs/{id}` read path). */
  run_id: string;
  status: string;
  totals: RunTotals;
}

/**
 * `POST /projects/{id}/regression/analyze` → 202 + `{job_id}` (S6.4).
 *
 * The deterministic S6.1 impact set, S6.2 risk ranking, S6.3 top-N
 * recommendation and the optional S6.5 advisor brief are **not** in this
 * response — they ride the `regression.set` SSE event on the job's `/events`
 * stream (read via `streamJobEvents`). The job's terminal `output_ref` is the
 * stable `regression://{projectId}` reference.
 */
export function runRegressionAnalysis(
  projectId: string,
  body: RegressionAnalysisRequest,
): Promise<JobCreated> {
  return request<JobCreated>(
    `/projects/${encodeURIComponent(projectId)}/regression/analyze`,
    { method: 'POST', body: JSON.stringify(body) },
  );
}

/**
 * `POST /projects/{id}/runs` — "Run this set" (S6.4) → 202 + `{job_id}`.
 *
 * The selected tests run through the existing S3 execution path
 * (`run_playwright` + `persist_run`); the `run.result` SSE event carries the
 * persisted run id and totals, and the job's terminal `output_ref` is the
 * persisted run id (served by the S3.2 `GET /runs/{id}` read path).
 */
export function runRegressionSet(
  projectId: string,
  body: RunRequest,
): Promise<JobCreated> {
  return request<JobCreated>(
    `/projects/${encodeURIComponent(projectId)}/runs`,
    { method: 'POST', body: JSON.stringify(body) },
  );
}

/**
 * S7.2 — `POST /projects/{id}/regression/pr-comment` → 202 + `{job_id}`.
 *
 * The job resolves the PR through the project's S7.1 GitHub integration,
 * computes the deterministic S6.1/S6.2/S6.3 set from `repository_path`, and
 * upserts the idempotent marker comment (first post creates, re-posts update,
 * identical re-posts are a no-op). The `regression.comment` SSE event carries
 * the upsert outcome (`action` / `comment_id` / `html_url`). Requires an
 * `owner`-role token (403 otherwise); 409 when the GitHub integration is
 * missing.
 */
export function postRegressionPrComment(
  projectId: string,
  body: RegressionPrCommentRequest,
): Promise<JobCreated> {
  return request<JobCreated>(
    `/projects/${encodeURIComponent(projectId)}/regression/pr-comment`,
    { method: 'POST', body: JSON.stringify(body) },
  );
}
