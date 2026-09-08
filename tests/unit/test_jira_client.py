"""S7.4 Jira client unit tests (build bible §19 S7.4, §17).

The client is exercised against an in-process ``httpx`` mock transport
(no network, no model): API-token wiring (``Authorization: Bearer``), the
``create_issue`` / ``update_issue`` / ``fetch_issue`` call shape, typed error
mapping (401/403 → auth, 404 → not found, other → http), and the token-
redaction contract (the token never survives into an error message, §17).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from qa_copilot_integrations.jira.client import (
    REDACTED,
    JiraAuthError,
    JiraClient,
    JiraError,
    JiraHTTPError,
    JiraIssue,
    JiraNotFoundError,
    redact_secrets,
    validate_issue_key,
    validate_project_key,
)

TOKEN = "ATATT7xS74UnitTestToken1234567890"
BASE = "http://jira.test"

Handler = Callable[[httpx.Request], httpx.Response]

ISSUE_BODY = {
    "key": "QA-123",
    "id": "10001",
    "self": "https://jira.example/rest/api/2/issue/QA-123",
    "fields": {
        "summary": "[QA] regression",
        "status": {"name": "To Do"},
    },
}


def _run(coro: Any) -> Any:
    """Drive one client coroutine from a sync test (no pytest-asyncio dep)."""
    return asyncio.run(coro)


def _client(handler: Handler, *, token: str = TOKEN, base: str = BASE) -> JiraClient:
    return JiraClient(base_url=base, token=token, transport=httpx.MockTransport(handler))


# --- redaction ------------------------------------------------------------------


def test_redact_secrets_covers_atatt_bearer_and_query() -> None:
    body = f"crash while sending Bearer {TOKEN} and ?token=supersecret123"
    clean, count = redact_secrets(body)
    assert TOKEN not in clean
    assert "supersecret123" not in clean
    assert REDACTED in clean
    assert count >= 2
    # idempotent: redacting the redacted text changes nothing
    again, count2 = redact_secrets(clean)
    assert again == clean
    assert count2 == 0


def test_redact_secrets_leaves_clean_text_alone() -> None:
    clean, count = redact_secrets("plain error message, no secrets")
    assert clean == "plain error message, no secrets"
    assert count == 0


# --- key validation --------------------------------------------------------------


def test_validate_issue_key_accepts_and_rejects() -> None:
    assert validate_issue_key("QA-123") == "QA-123"
    assert validate_issue_key("ABC-1") == "ABC-1"
    for bad in ("", "123", "QA", "QA-abc", "QA-1 2", None, 5):
        with pytest.raises(ValueError, match="issue key"):
            validate_issue_key(bad)  # type: ignore[arg-type]


def test_validate_project_key_accepts_and_rejects() -> None:
    assert validate_project_key("QA") == "QA"
    assert validate_project_key("Acme_Corp") == "Acme_Corp"
    for bad in ("", "a-b", "a b", "1abc", None, 5):
        with pytest.raises(ValueError, match="project key"):
            validate_project_key(bad)  # type: ignore[arg-type]


def test_client_requires_token() -> None:
    with pytest.raises(ValueError, match="token"):
        JiraClient(base_url=BASE, token="")


# --- create_issue ----------------------------------------------------------------


def test_create_issue_posts_fields_and_sends_bearer() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert str(request.url.path) == "/rest/api/2/issue"
        assert request.headers["authorization"] == f"Bearer {TOKEN}"
        assert request.headers["accept"] == "application/json"
        fields = request.read().decode("utf-8")
        assert "project" in fields
        return httpx.Response(200, json=dict(ISSUE_BODY))

    issue = _run(_client(handler).create_issue({"project": {"key": "QA"}}))
    assert issue == JiraIssue(
        key="QA-123",
        id="10001",
        summary="[QA] regression",
        status="To Do",
        url="https://jira.example/rest/api/2/issue/QA-123",
    )


def test_create_issue_401_maps_to_auth_error() -> None:
    client = _client(lambda r: httpx.Response(401, json={"message": "Bad credentials"}))
    with pytest.raises(JiraAuthError) as excinfo:
        _run(client.create_issue({"project": {"key": "QA"}}))
    assert excinfo.value.status == 401
    assert "Bad credentials" in str(excinfo.value)


def test_create_issue_400_maps_to_http_error_with_redacted_body() -> None:
    client = _client(
        lambda r: httpx.Response(400, json={"errorMessages": ["bad field"], "message": "x"})
    )
    with pytest.raises(JiraHTTPError) as excinfo:
        _run(client.create_issue({"project": {"key": "QA"}}))
    assert excinfo.value.status == 400


def test_token_never_survives_error_message() -> None:
    client = _client(
        lambda r: httpx.Response(500, json={"message": f"crash while forwarding {TOKEN}"})
    )
    with pytest.raises(JiraError) as excinfo:
        _run(client.create_issue({"project": {"key": "QA"}}))
    message = str(excinfo.value)
    assert TOKEN not in message  # §17: the token never survives into the error
    assert REDACTED in message


# --- update_issue ----------------------------------------------------------------


def test_update_issue_puts_to_keyed_path() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "PUT"
        assert str(request.url.path) == "/rest/api/2/issue/QA-123"
        assert request.headers["authorization"] == f"Bearer {TOKEN}"
        return httpx.Response(200, json=dict(ISSUE_BODY))

    issue = _run(_client(handler).update_issue("QA-123", {"summary": "new"}))
    assert issue.key == "QA-123"


def test_update_issue_404_maps_to_not_found() -> None:
    client = _client(lambda r: httpx.Response(404, json={"message": "Not Found"}))
    with pytest.raises(JiraNotFoundError) as excinfo:
        _run(client.update_issue("QA-999", {"summary": "x"}))
    assert excinfo.value.status == 404


def test_update_issue_validates_key() -> None:
    client = _client(lambda r: httpx.Response(200, json=dict(ISSUE_BODY)))
    with pytest.raises(ValueError, match="issue key"):
        _run(client.update_issue("not a key", {"summary": "x"}))


# --- fetch_issue -----------------------------------------------------------------


def test_fetch_issue_gets_keyed_path() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert str(request.url.path) == "/rest/api/2/issue/QA-123"
        assert request.headers["authorization"] == f"Bearer {TOKEN}"
        return httpx.Response(200, json=dict(ISSUE_BODY))

    issue = _run(_client(handler).fetch_issue("QA-123"))
    assert issue.key == "QA-123"
    assert issue.status == "To Do"


def test_fetch_issue_404_maps_to_not_found() -> None:
    client = _client(lambda r: httpx.Response(404, json={"message": "Not Found"}))
    with pytest.raises(JiraNotFoundError) as excinfo:
        _run(client.fetch_issue("QA-999"))
    assert excinfo.value.status == 404


# --- transport / body edge cases ---------------------------------------------------


def test_transport_failure_is_typed_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    client = _client(handler)
    with pytest.raises(JiraError, match="unreachable"):
        _run(client.create_issue({"project": {"key": "QA"}}))


def test_non_json_body_is_typed_error() -> None:
    client = _client(lambda r: httpx.Response(200, text="<html>oops</html>"))
    with pytest.raises(JiraError, match="non-JSON"):
        _run(client.create_issue({"project": {"key": "QA"}}))


def test_missing_required_key_is_typed_error() -> None:
    client = _client(lambda r: httpx.Response(200, json={"id": "1"}))
    with pytest.raises(JiraError, match="key"):
        _run(client.create_issue({"project": {"key": "QA"}}))
