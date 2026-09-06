"""Typed async client for the Jira REST v2 issue API (build bible §19 S7.4).

S7.4 is **LLM-free**: the client only talks to Jira's ``/rest/api/2/issue``
surface — ``create_issue`` (``POST``), ``update_issue`` (``PUT``) and
``fetch_issue`` (``GET``) — over :mod:`httpx`. It is the Jira mirror of the
S7.1 :class:`~qa_copilot_integrations.github.client.GitHubClient`: same shape
(base URL + token, injectable transport for tests/fake servers), same error
mapping (typed exceptions, ``status`` attribute), and the same §17
guarantee — the API token is **never** stored on the client, never appears
in any exception message, and non-2xx response bodies are redacted before
they are surfaced (exact-token redaction plus pattern redaction, see
:func:`qa_copilot_integrations.secrets.redact_secrets`).

Deterministic, dependency-light: no LLM, no framework imports (the API job
agent in ``qa_copilot_api.jobs`` wraps this for the S7.4 ``jira_link`` job).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import httpx

from qa_copilot_integrations.secrets import REDACTED, redact_secrets

#: Jira site base URL (``https://<org>.atlassian.net`` for Cloud). Unlike
#: GitHub there is no universal default — the S7.4 integration config row
#: stores the project's ``base_url`` and the API route 409s when it is
#: missing. The CLI requires ``--base-url`` (or ``JIRA_BASE_URL``) for the
#: same reason.
DEFAULT_BASE_URL = "https://atlassian.net"
#: Jira REST v2 issue endpoints (stable API).
CREATE_PATH = "/rest/api/2/issue"
ISSUE_PATH = "/rest/api/2/issue/{key}"
#: Jira issue keys: ``PROJECT-123`` (project key is alphanumeric + ``_``).
ISSUE_KEY = re.compile(r"^[A-Za-z][A-Za-z0-9_]*-\d+$")


def _require_str(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise JiraError(f"Jira API response is missing required field {key!r}")
    return value


class JiraError(Exception):
    """Base error for the Jira client.

    ``status`` is the HTTP status code (``None`` for transport-level
    failures). ``str(exc)`` is always redacted — safe for logs, audit
    rows, and CLI output (§17).
    """

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class JiraAuthError(JiraError):
    """401/403 — the API token is missing, expired, or lacks scope."""


class JiraNotFoundError(JiraError):
    """404 — the issue (or project) does not exist in this Jira instance."""


class JiraHTTPError(JiraError):
    """Any other non-2xx response (4xx/5xx) from the Jira API."""


@dataclass(frozen=True)
class JiraIssue:
    """A Jira issue (the subset S7.4 relies on, from the REST v2 shape).

    ``key`` is the human issue key (``QA-123``) — the stable identity the
    S7.4 job persists in ``failures.jira_issue_key`` for create-or-update
    idempotency; ``url`` is the instance issue page (Jira returns it when
    the instance supports it).
    """

    key: str
    id: str | None = None
    summary: str | None = None
    status: str | None = None
    url: str | None = None


def validate_issue_key(key: str) -> str:
    """Validate a Jira issue key (``PROJECT-123``); returns it unchanged."""
    if not isinstance(key, str) or not ISSUE_KEY.fullmatch(key):
        raise ValueError(f"not a valid Jira issue key (PROJECT-123): {key!r}")
    return key


def validate_project_key(key: str) -> str:
    """Validate a Jira project key (``[A-Za-z][A-Za-z0-9_]*``); return it."""
    if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", key):
        raise ValueError(f"not a valid Jira project key: {key!r}")
    return key


class JiraClient:
    """Async Jira REST v2 issue client (LLM-free, §19 S7.4).

    Deterministic + LLM-free: no gateway, no prompts, no model calls.
    Inject ``transport`` for in-process fakes; point ``base_url`` at a
    Jira Cloud site or a fake server for the rest.

    §17: the token is sent only as ``Authorization: Bearer <token>`` — it
    is never persisted anywhere by this client and never appears in any
    error message (exact-value redaction before the body is surfaced).
    """

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_BASE_URL,
        token: str,
        timeout_s: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not token:
            raise ValueError("Jira token is required (§19 S7.4: base_url + token)")
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            headers=self._headers(),
            timeout=httpx.Timeout(timeout_s, connect=min(5.0, timeout_s)),
            transport=transport,
        )

    # -- lifecycle ----------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/json",
            "Authorization": f"Bearer {self._token}",
            "User-Agent": "ai-qa-copilot/0.1",
        }

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> JiraClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    # -- transport ------------------------------------------------------------

    def _error_message(self, status: int, body: str) -> str:
        """Redacted, bounded one-liner for a non-2xx response (§17)."""
        clean, _ = redact_secrets(body.strip(), self._token)
        detail = f" — {clean[:500]}" if clean else ""
        return f"Jira API error {status}{detail}"

    def _raise_for_status(self, response: httpx.Response) -> None:
        if 200 <= response.status_code < 300:
            return
        message = self._error_message(response.status_code, response.text)
        if response.status_code in (401, 403):
            raise JiraAuthError(message, status=response.status_code)
        if response.status_code == 404:
            raise JiraNotFoundError(message, status=response.status_code)
        raise JiraHTTPError(message, status=response.status_code)

    async def _request(self, method: str, path: str, *, json: Any = None) -> httpx.Response:
        try:
            response = await self._client.request(method, path, json=json)
        except httpx.HTTPError as exc:
            raise JiraError(f"Jira API unreachable: {type(exc).__name__}") from exc
        self._raise_for_status(response)
        return response

    @staticmethod
    def _json(response: httpx.Response) -> object:
        try:
            return response.json()
        except ValueError as exc:
            raise JiraError("Jira API returned a non-JSON body") from exc

    def _issue(self, payload: object) -> JiraIssue:
        if not isinstance(payload, dict):
            raise JiraError("Jira API returned a non-object issue payload")
        key = _require_str(payload, "key")
        raw_id = payload.get("id")
        raw_fields = payload.get("fields")
        fields: dict[str, Any] = raw_fields if isinstance(raw_fields, dict) else {}
        status_obj = fields.get("status")
        status = status_obj.get("name") if isinstance(status_obj, dict) else None
        summary = fields.get("summary")
        return JiraIssue(
            key=key,
            id=str(raw_id) if raw_id is not None else None,
            summary=summary if isinstance(summary, str) else None,
            status=status if isinstance(status, str) else None,
            url=payload.get("self") if isinstance(payload.get("self"), str) else None,
        )

    # -- public API ---------------------------------------------------------

    async def create_issue(self, fields: Mapping[str, Any]) -> JiraIssue:
        """Create an issue (``POST /rest/api/2/issue``) and return it.

        ``fields`` is the standard Jira issue field map (``project``,
        ``summary``, ``description`` as ADF, ``labels``, ...). 401/403 →
        :class:`JiraAuthError`; 404 → :class:`JiraNotFoundError`; other
        non-2xx → :class:`JiraHTTPError` (message redacted, §17).
        """
        response = await self._request("POST", CREATE_PATH, json=dict(fields))
        return self._issue(self._json(response))

    async def update_issue(self, key: str, fields: Mapping[str, Any]) -> JiraIssue:
        """Update an existing issue (``PUT /rest/api/2/issue/{key}``).

        S7.4 re-link idempotency: the same failure re-linked is *updated*
        in place — never duplicated — because the job persists the issue
        key and calls this instead of ``create_issue``.
        """
        validate_issue_key(key)
        response = await self._request("PUT", ISSUE_PATH.format(key=key), json=dict(fields))
        return self._issue(self._json(response))

    async def fetch_issue(self, key: str) -> JiraIssue:
        """Fetch an issue (``GET /rest/api/2/issue/{key}``) and return it."""
        validate_issue_key(key)
        response = await self._request("GET", ISSUE_PATH.format(key=key))
        return self._issue(self._json(response))


__all__ = [
    "CREATE_PATH",
    "DEFAULT_BASE_URL",
    "ISSUE_KEY",
    "ISSUE_PATH",
    "REDACTED",
    "JiraAuthError",
    "JiraClient",
    "JiraError",
    "JiraHTTPError",
    "JiraIssue",
    "JiraNotFoundError",
    "redact_secrets",
    "validate_issue_key",
    "validate_project_key",
]
