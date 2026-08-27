"""API-level E2E for S1.3: login → job creation → SSE → persisted read-back.

Run with the backend up on :8000 and a seeded dev account (``scripts/seed.py``):

    .venv\\Scripts\\python scripts\\e2e_s13.py

Exits 0 when the whole chain works, non-zero otherwise.
"""

from __future__ import annotations

import json
import sys

import httpx

BASE = "http://127.0.0.1:8000/api/v1"
EMAIL = "dev@local.dev"
PASSWORD = "dev-password"


def main() -> int:
    client = httpx.Client(base_url=BASE, timeout=30)

    # 1. Login (dev-mode auth, §31.3)
    res = client.post("/auth/login", json={"email": EMAIL, "password": PASSWORD})
    res.raise_for_status()
    login = res.json()
    if not login.get("projects"):
        print("ERROR: logged in but no project membership — run scripts/seed.py")
        return 1
    token = login["token"]
    project = login["projects"][0]
    print(f"login OK: {login['user']['email']} role={project['role']} project={project['name']}")
    headers = {"Authorization": f"Bearer {token}"}

    # 2. Create the test_case_generation job (202 + job_id, §11)
    body = {
        "project_id": project["id"],
        "title": "Order history",
        "content": (
            "Users can view their order history in newest-first order; "
            "each order shows its status and total amount."
        ),
        "acceptance_criteria": ["Orders are listed newest first", "Each order shows status"],
    }
    res = client.post("/requirements/test-cases", headers=headers, json=body)
    if res.status_code != 202:
        print(f"ERROR: expected 202, got {res.status_code}: {res.text}")
        return 1
    job_id = res.json()["job_id"]
    print(f"job created: {job_id}")

    # 3. Stream the job's SSE events until the terminal frame
    terminal: tuple[str, dict] | None = None
    count = 0
    with httpx.Client(base_url=BASE, timeout=300) as stream_client:
        with stream_client.stream("GET", f"/events?job_id={job_id}", headers=headers) as stream:
            event: str | None = None
            data_lines: list[str] = []
            for line in stream.iter_lines():
                if line == "":
                    if event is not None:
                        payload = json.loads("\n".join(data_lines)) if data_lines else {}
                        count += 1
                        print(f"  sse {event}: {json.dumps(payload)}")
                        if event in ("job.completed", "job.failed", "job.cancelled"):
                            terminal = (event, payload)
                            break
                    event, data_lines = None, []
                elif line.startswith("event:"):
                    event = line.removeprefix("event:").strip()
                elif line.startswith("data:"):
                    data_lines.append(line.removeprefix("data:").strip())
    if terminal is None:
        print("ERROR: stream ended without a terminal event")
        return 1
    print(f"terminal event: {terminal[0]} ({count} events total)")
    if terminal[0] != "job.completed":
        print("ERROR: job did not complete")
        return 1
    output_ref = terminal[1].get("output_ref")
    if not output_ref:
        print("ERROR: job.completed has no output_ref")
        return 1
    print(f"output_ref (requirement id): {output_ref}")

    # 4. Read back the persisted requirement + its test cases (S1.3)
    res = client.get(f"/requirements/{output_ref}", headers=headers)
    res.raise_for_status()
    req = res.json()
    cases = req["test_cases"]
    print(f"read-back OK: '{req['title']}' with {len(cases)} test case(s)")
    for i, tc in enumerate(cases, 1):
        print(f"  {i}. [{tc['type']} / priority={tc['priority']}] {tc['title']}")
        print(f"     steps={len(tc['steps'])} expected={len(tc['expected_results'])}")
    if not cases:
        print("ERROR: no persisted test cases")
        return 1

    # 5. Sanity: job row says completed with the same output_ref
    res = client.get(f"/jobs/{job_id}", headers=headers)
    res.raise_for_status()
    job = res.json()
    print(f"job row: status={job['status']} output_ref={job['output_ref']}")
    if job["status"] != "completed" or job["output_ref"] != output_ref:
        print("ERROR: job row disagrees with the SSE terminal event")
        return 1

    print("E2E OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
