"""API-level live E2E for S5.5: Ask → 202 → job → grounded answer with citations.

Run with the backend up on :8000 (LLM configured in ``.env``, LM Studio
:8080) and a seeded dev account (``scripts/seed.py``):

    uv run python scripts/e2e_s55_ask.py

Flow (build bible §19 S5.5):

1. login → token;
2. confirm the project's knowledge base is indexed (S5.3 status);
3. ``POST /projects/{id}/knowledge/ask`` with an in-scope question
   → **202 + job_id** + ``Location``;
4. stream ``GET /events?job_id=...`` → a ``knowledge.answer`` event with
   ``in_scope=true``, a non-empty grounded answer and ≥ 1 citation carrying
   ``document_ref`` / ``source_type`` / ``title`` / ``score > 0``;
5. the job row lands ``completed`` with the stable
   ``knowledge-ask://<project>`` output_ref;
6. an out-of-scope question → a contract-valid refusal
   (``in_scope=false``, no answer, no citations).

Exits 0 when the whole chain works, non-zero otherwise.
"""

from __future__ import annotations

import json
import sys
import time

import httpx

BASE = "http://127.0.0.1:8000/api/v1"
EMAIL = "dev@local.dev"
PASSWORD = "dev-password"

# In-scope: grounded in the Demo App requirement "Order history" (seeded corpus:
# "Users can view their order history in newest-first order; each order shows
# its status and total amount.").
IN_SCOPE_QUESTION = "How should the order history list be displayed to users?"
OUT_OF_SCOPE_QUESTION = "What is the capital of France?"

TERMINAL_EVENTS = ("job.completed", "job.failed", "job.cancelled")


def _login(client: httpx.Client) -> tuple[dict[str, object], dict[str, str]]:
    """Login (dev-mode auth, §31.3) → (project, headers)."""
    res = client.post("/auth/login", json={"email": EMAIL, "password": PASSWORD})
    res.raise_for_status()
    login = res.json()
    if not login.get("projects"):
        raise RuntimeError("logged in but no project membership — run scripts/seed.py")
    token: str = login["token"]
    project = login["projects"][0]
    print(f"login OK: {login['user']['email']} role={project['role']} project={project['name']}")
    return project, {"Authorization": f"Bearer {token}"}


def _stream_job(
    headers: dict[str, str], job_id: str
) -> tuple[list[tuple[str, dict[str, object]]], tuple[str, dict[str, object]] | None]:
    """Consume the job's SSE feed until the terminal frame (replay-safe)."""
    events: list[tuple[str, dict[str, object]]] = []
    terminal: tuple[str, dict[str, object]] | None = None
    with httpx.Client(base_url=BASE, timeout=1800) as stream_client:
        with stream_client.stream("GET", f"/events?job_id={job_id}", headers=headers) as stream:
            event: str | None = None
            data_lines: list[str] = []
            for line in stream.iter_lines():
                if line == "":
                    if event is not None:
                        payload = json.loads("\n".join(data_lines)) if data_lines else {}
                        events.append((event, payload))
                        print(f"  sse {event}: {json.dumps(payload)[:200]}")
                        if event in TERMINAL_EVENTS:
                            terminal = (event, payload)
                            break
                    event, data_lines = None, []
                elif line.startswith("event:"):
                    event = line.removeprefix("event:").strip()
                elif line.startswith("data:"):
                    data_lines.append(line.removeprefix("data:").strip())
    return events, terminal


def _ask(
    client: httpx.Client, headers: dict[str, str], project_id: str, question: str
) -> tuple[str, list[tuple[str, dict[str, object]]], tuple[str, dict[str, object]] | None]:
    """Ask → expect 202 + job_id (+ Location) → stream to the terminal event."""
    res = client.post(
        f"/projects/{project_id}/knowledge/ask",
        headers=headers,
        json={"question": question},
        timeout=60,
    )
    if res.status_code != 202:
        raise RuntimeError(f"expected 202, got {res.status_code}: {res.text}")
    if res.headers.get("Location") != f"/api/v1/jobs/{res.json()['job_id']}":
        raise RuntimeError(f"bad Location header: {res.headers.get('Location')}")
    job_id = res.json()["job_id"]
    print(f"ask 202 OK: job_id={job_id} question={question!r}")
    events, terminal = _stream_job(headers, job_id)
    if terminal is None:
        raise RuntimeError("stream ended without a terminal event")
    if terminal[0] != "job.completed":
        raise RuntimeError(f"job ended with {terminal[0]}: {terminal[1].get('error')}")
    return job_id, events, terminal


def main() -> int:
    client = httpx.Client(base_url=BASE, timeout=60)

    project, headers = _login(client)
    project_id: str = project["id"]

    # 1. The project's knowledge base must be indexed (S5.3).
    res = client.get(f"/projects/{project_id}/knowledge/status", headers=headers)
    res.raise_for_status()
    status = res.json()
    docs = status.get("document_count") or 0
    print(f"knowledge status: {json.dumps(status)}")
    if docs == 0:
        print("ERROR: no knowledge documents indexed — run the index job first")
        return 1

    # 2. In-scope question → grounded answer with citations.
    print(f"\n--- in-scope: {IN_SCOPE_QUESTION!r}")
    job_id, events, _ = _ask(client, headers, project_id, IN_SCOPE_QUESTION)
    answer_events = [d for n, d in events if n == "knowledge.answer"]
    if len(answer_events) != 1:
        raise RuntimeError(f"expected exactly 1 knowledge.answer event, got {len(answer_events)}")
    answer = answer_events[0]
    if not answer.get("in_scope"):
        raise RuntimeError(f"in-scope question was refused: {answer}")
    if not str(answer.get("answer") or "").strip():
        raise RuntimeError("in-scope answer is empty")
    citations = answer.get("citations") or []
    if not citations:
        raise RuntimeError("in-scope answer has no citations")
    for cite in citations:
        for field in ("document_ref", "source_type", "title", "score"):
            if field not in cite:
                raise RuntimeError(f"citation missing {field}: {cite}")
        if float(cite["score"]) <= 0:
            raise RuntimeError(f"citation score not > 0: {cite}")
    print(f"ANSWER: {answer['answer']}")
    print(f"CITATIONS: {json.dumps(citations, indent=2)}")

    # 3. Job row: completed + stable knowledge-ask:// reference.
    res = client.get(f"/jobs/{job_id}", headers=headers)
    res.raise_for_status()
    job = res.json()
    expected_ref = f"knowledge-ask://{project_id}"
    print(f"job row: status={job['status']} output_ref={job['output_ref']}")
    if job["status"] != "completed" or job["output_ref"] != expected_ref:
        raise RuntimeError(f"job row wrong: {job}")

    # 4. Out-of-scope question → contract-valid refusal.
    print(f"\n--- out-of-scope: {OUT_OF_SCOPE_QUESTION!r}")
    time.sleep(1)  # let the model finish any trailing work between calls
    _job_id, events, _ = _ask(client, headers, project_id, OUT_OF_SCOPE_QUESTION)
    answer_events = [d for n, d in events if n == "knowledge.answer"]
    if len(answer_events) != 1:
        raise RuntimeError(f"expected exactly 1 knowledge.answer event, got {len(answer_events)}")
    refusal = answer_events[0]
    if refusal.get("in_scope"):
        raise RuntimeError(f"out-of-scope question was answered: {refusal}")
    if str(refusal.get("answer") or "").strip() or refusal.get("citations"):
        raise RuntimeError(f"refusal carried an answer/citations: {refusal}")
    print("REFUSAL: in_scope=false, no answer, no citations (contract held)")

    print("\nE2E OK (S5.5: ask → 202 → job → grounded answer with citations)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
