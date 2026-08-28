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
