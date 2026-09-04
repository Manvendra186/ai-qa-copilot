"""Typed GitHub REST client — S7.1 core (build bible §19 S7.1).

Deterministic, **LLM-free** (the S2.1/S3.3/S5.1/S6.1 pattern): this module
makes plain HTTP calls against the GitHub REST v3 API and maps the
responses onto flat, typed results. Nothing here imports
``qa_copilot_ai`` — the §31.1 gateway is *off the path* (S7.1 exit
criterion: "no LLM call is present in the path").

Secret hygiene (build bible §17; S7.1 exit "PAT never appears in logs or
audit output"):

- the PAT is sent only in the ``Authorization: Bearer`` header;
- every non-2xx response body is passed through :func:`redact_secrets`
  before it is put into an exception message, so a PAT echoed back by a
  proxy/error page can never leak into logs, audit rows, or CLI stdout.

Wire contract: :meth:`GitHubClient.fetch_pull_request` returns
``changed_files`` as a de-duplicated, sorted list of repo-relative path
strings — the **exact S6.1 ``files[]`` shape** (``RegressionAnalysisRequest.files``).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import httpx

#: GitHub REST v3 base (overridable for GHES / fake servers / tests).
DEFAULT_BASE_URL = "https://api.github.com"
#: Pinned GitHub API version header (stable, non-preview endpoint set only).
API_VERSION = "2022-11-28"
#: Pagination cap: 100 files/page → 1,000 files. A PR beyond this fails
#: loud (V1) rather than silently truncating the impact set.
MAX_PAGES = 10
_PAGE_SIZE = 100

#: Redaction sentinel — same value as ``qa_copilot_ai.redaction.REDACTED``
#: (kept local: integrations must stay independent of the AI package).
REDACTED = "***REDACTED***"

# (pattern, replacement) pairs applied in order. Conservative set: GitHub
# personal/access tokens, ``Bearer`` credentials, and ``token=`` query
# material. None of the replacements re-match (redaction is idempotent).
_SECRET_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bBearer\s+[A-Za-z0-9\-_\.+/=]+"), f"Bearer {REDACTED}"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{16,}"), REDACTED),
    (
        re.compile(r"([?&]token=)([A-Za-z0-9\-_\.+/=]{8,})", re.IGNORECASE),
        r"\1" + REDACTED,
    ),
)

_GITHUB_NAME = re.compile(r"[A-Za-z0-9_.-]+")


def redact_secrets(text: str) -> tuple[str, int]:
    """Replace secret-looking material with ``***REDACTED***``.

    Returns the redacted text plus how many replacements were made.
    Idempotent: redacting twice changes nothing.
    """
    count = 0
    for pattern, replacement in _SECRET_PATTERNS:
        text, replaced = pattern.subn(replacement, text)
        count += replaced
    return text, count


def _validate_ref(value: object, field: str) -> None:
    """Owner/repo names: GitHub allows ``[A-Za-z0-9_.-]+`` (fail loud)."""
    if not isinstance(value, str) or not _GITHUB_NAME.fullmatch(value):
        raise ValueError(f"{field} must be a GitHub name ([A-Za-z0-9_.-]+), got {value!r}")


def _require_str(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise GitHubError(f"GitHub API response is missing required field {key!r}")
    return value


def _next_link(link_header: str) -> str | None:
    """Extract the ``rel="next"`` URL from a GitHub ``Link`` header (or None)."""
    for part in link_header.split(","):
        segments = [segment.strip() for segment in part.split(";")]
        if len(segments) >= 2 and segments[0].startswith("<") and segments[0].endswith(">"):
            rel = segments[1].removeprefix("rel=").strip().strip('"')
            if rel == "next":
                return segments[0][1:-1]
    return None


class GitHubError(Exception):
    """Base error for the GitHub client.

    ``status`` is the HTTP status code (``None`` for transport-level
    failures). ``str(exc)`` is always redacted — safe for logs, audit
    rows, and CLI output (§17).
    """

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class GitHubAuthError(GitHubError):
    """401/403 — missing/invalid PAT (or rate-limited; see ``status``)."""


class GitHubNotFoundError(GitHubError):
    """404 — repository or pull request does not exist (or the PAT lacks access)."""


class GitHubHTTPError(GitHubError):
    """Any other non-2xx (5xx, 422, 429, ...) — the message is redacted (§17)."""




@dataclass(frozen=True, slots=True)
class RepositoryInfo:
    """A GitHub repository, mapped onto the §10 ``repositories`` fields.

    ``url`` and ``default_branch`` are exactly the ``repositories`` column
    values S7.1 requires (``resolve_repository`` → ``repositories``-
    compatible fields).
    """

    owner: str
    name: str
    full_name: str
    html_url: str
    url: str
    default_branch: str


@dataclass(frozen=True, slots=True)
class PullRequestInfo:
    """A GitHub pull request + its changed files in the S6.1 ``files[]`` shape.

    ``changed_files`` is a de-duplicated, sorted list of repo-relative
    path strings — exactly the shape of ``RegressionAnalysisRequest.files``
    (S6.1), so S7.2 can feed it straight into the impact core.
    """

    number: int
    title: str
    state: str
    html_url: str
    head_sha: str
    head_ref: str
    base_sha: str
    base_ref: str
    changed_files: tuple[str, ...]


class GitHubClient:
    """Thin async client for the GitHub REST v3 API (V1: PAT auth only).

    Deterministic + LLM-free: no gateway, no prompts, no model calls.
    Inject ``transport`` for in-process fakes; point ``base_url`` at a
    GHES instance or a fake server for the rest.
    """

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_BASE_URL,
        token: str | None = None,
        timeout_s: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            headers=self._headers(),
            timeout=httpx.Timeout(timeout_s, connect=min(5.0, timeout_s)),
            transport=transport,
        )

    # -- lifecycle --------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": API_VERSION,
            "User-Agent": "ai-qa-copilot/0.1",
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> GitHubClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    # -- transport ----------------------------------------------------------------

    @staticmethod
    def _error_message(status: int, body: str) -> str:
        """Redacted, bounded one-liner for a non-2xx response (§17)."""
        clean, _ = redact_secrets(body.strip())
        detail = f" — {clean[:500]}" if clean else ""
        return f"GitHub API error {status}{detail}"

    def _raise_for_status(self, response: httpx.Response) -> None:
        if 200 <= response.status_code < 300:
            return
        message = self._error_message(response.status_code, response.text)
        if response.status_code in (401, 403):
            raise GitHubAuthError(message, status=response.status_code)
        if response.status_code == 404:
            raise GitHubNotFoundError(message, status=response.status_code)
        raise GitHubHTTPError(message, status=response.status_code)

    async def _get(self, url: str, *, params: dict[str, str | int] | None = None) -> httpx.Response:
        try:
            response = await self._client.get(url, params=params)
        except httpx.HTTPError as exc:
            raise GitHubError(f"GitHub API unreachable: {type(exc).__name__}") from exc
        self._raise_for_status(response)
        return response

    async def _get_json(self, url: str, *, params: dict[str, str | int] | None = None) -> object:
        response = await self._get(url, params=params)
        try:
            return response.json()
        except ValueError as exc:
            raise GitHubError(f"GitHub API returned a non-JSON body for {url}") from exc

    async def _get_paged(
        self, url: str, *, params: dict[str, str | int] | None = None
    ) -> list[dict[str, Any]]:
        """GET a list endpoint, following ``Link: rel="next"`` (capped)."""
        items: list[dict[str, Any]] = []
        query: dict[str, str | int] | None = params
        for _ in range(MAX_PAGES):
            response = await self._get(url, params=query)
            body = response.json()
            if not isinstance(body, list):
                raise GitHubError(f"GitHub API list endpoint {url} returned a non-array body")
            items.extend(body)
            next_url = _next_link(response.headers.get("Link", ""))
            query = None
            if next_url is None:
                break
            url = next_url
        else:
            raise GitHubError(f"GitHub API list {url} exceeded {MAX_PAGES} pages (1000 items)")
        return items

    # -- S7.1 API -------------------------------------------------------------------

    async def resolve_repository(self, owner: str, repo: str) -> RepositoryInfo:
        """``GET /repos/{owner}/{repo}`` → §10 ``repositories``-compatible fields."""
        _validate_ref(owner, "owner")
        _validate_ref(repo, "repo")
        payload = await self._get_json(f"/repos/{owner}/{repo}")
        if not isinstance(payload, dict):
            raise GitHubError("GitHub API /repos returned a non-object body")
        full_name = _require_str(payload, "full_name")
        html_url = _require_str(payload, "html_url")
        clone_url = payload.get("clone_url")
        if not isinstance(clone_url, str) or not clone_url:
            clone_url = f"{html_url}.git"
        owner_name, _, repo_name = full_name.partition("/")
        return RepositoryInfo(
            owner=owner_name or owner,
            name=repo_name or repo,
            full_name=full_name,
            html_url=html_url,
            url=clone_url,  # repositories.url
            default_branch=_require_str(payload, "default_branch"),  # repositories.default_branch
        )

    async def fetch_pull_request(
        self, owner: str, repo: str, number: int
    ) -> PullRequestInfo:
        """``GET /repos/{owner}/{repo}/pulls/{number}`` (+ paged ``/files``).

        Returns head/base SHAs + the changed files in the exact S6.1
        ``files[]`` shape (de-duplicated, sorted repo-relative paths).
        """
        _validate_ref(owner, "owner")
        _validate_ref(repo, "repo")
        if isinstance(number, bool) or not isinstance(number, int) or number < 1:
            raise ValueError("pull request number must be a positive integer")
        payload = await self._get_json(f"/repos/{owner}/{repo}/pulls/{number}")
        if not isinstance(payload, dict):
            raise GitHubError("GitHub API /pulls returned a non-object body")
        head = payload.get("head")
        base = payload.get("base")
        if not isinstance(head, dict) or not isinstance(base, dict):
            raise GitHubError("GitHub API /pulls response is missing head/base objects")

        files_url = f"/repos/{owner}/{repo}/pulls/{number}/files"
        files_payload = await self._get_paged(files_url, params={"per_page": _PAGE_SIZE})
        changed: set[str] = set()
        for entry in files_payload:
            if isinstance(entry, dict):
                filename = entry.get("filename")
                if isinstance(filename, str) and filename:
                    changed.add(filename)

        return PullRequestInfo(
            number=number,
            title=_require_str(payload, "title"),
            state=_require_str(payload, "state"),
            html_url=_require_str(payload, "html_url"),
            head_sha=_require_str(head, "sha"),
            head_ref=_require_str(head, "ref"),
            base_sha=_require_str(base, "sha"),
            base_ref=_require_str(base, "ref"),
            changed_files=tuple(sorted(changed)),
        )


__all__ = [
    "API_VERSION",
    "DEFAULT_BASE_URL",
    "GitHubAuthError",
    "GitHubClient",
    "GitHubError",
    "GitHubHTTPError",
    "GitHubNotFoundError",
    "MAX_PAGES",
    "PullRequestInfo",
    "REDACTED",
    "RepositoryInfo",
    "redact_secrets",
]
