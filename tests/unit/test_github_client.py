"""S7.1 GitHub client unit tests (build bible §19 S7.1, §17).

The client is exercised against an in-process ``httpx`` mock transport
(no network, no model): PAT wiring, repository/PR field mapping onto the
§10 ``repositories`` fields and the S6.1 ``files[]`` shape, ``Link:
rel="next"`` pagination with its hard cap, typed error mapping, and the
PAT-redaction contract (the token never survives in an error message).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from qa_copilot_integrations.github.client import (
    REDACTED,
    GitHubAuthError,
    GitHubClient,
    GitHubError,
    GitHubHTTPError,
    GitHubNotFoundError,
    _next_link,
    _validate_ref,
    redact_secrets,
)

PAT = "ghp_S71UnitTestToken0123456"
BASE = "http://github.test"

Handler = Callable[[httpx.Request], httpx.Response]


def _run(coro: Any) -> Any:
    """Drive one client coroutine from a sync test (no pytest-asyncio dep)."""
    return asyncio.run(coro)


def _client(handler: Handler, *, token: str | None = PAT, base: str = BASE) -> GitHubClient:
    return GitHubClient(base_url=base, token=token, transport=httpx.MockTransport(handler))


# --- redaction ------------------------------------------------------------------


def test_redact_secrets_covers_bearer_ghp_and_query() -> None:
    body = f"crash while sending Bearer {PAT} and ?token=supersecret123"
    clean, count = redact_secrets(body)
    assert PAT not in clean
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


# --- link header parsing ---------------------------------------------------------


def test_next_link_parses_rel_next_and_ignores_last() -> None:
    header = (
        '<https://api.github.com/repos/o/r/files?page=2>; rel="next", '
        '<https://api.github.com/repos/o/r/files?page=5>; rel="last"'
    )
    assert _next_link(header) == "https://api.github.com/repos/o/r/files?page=2"
    assert _next_link('<https://api.github.com/x?page=5>; rel="last"') is None
    assert _next_link("") is None


def test_validate_ref_accepts_github_names_and_rejects_garbage() -> None:
    _validate_ref("Acme_Corp-1", "owner")
    for bad in ("", "a/b", "a b", "a.b/c", None, 5):
        with pytest.raises(ValueError, match="GitHub name"):
            _validate_ref(bad, "owner")


# --- resolve_repository ------------------------------------------------------------


def test_resolve_repository_maps_fields_and_sends_pat() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert str(request.url.path) == "/repos/acme/web"
        assert request.headers["authorization"] == f"Bearer {PAT}"
        assert request.headers["accept"] == "application/vnd.github+json"
        return httpx.Response(
            200,
            json={
                "full_name": "acme/web",
                "html_url": "https://github.com/acme/web",
                "clone_url": "https://github.com/acme/web.git",
                "default_branch": "main",
            },
        )

    client = _client(handler)
    info = _run(client.resolve_repository("acme", "web"))
    assert (info.owner, info.name, info.full_name) == ("acme", "web", "acme/web")
    assert info.html_url == "https://github.com/acme/web"
    assert info.url == "https://github.com/acme/web.git"  # repositories.url
    assert info.default_branch == "main"  # repositories.default_branch


def test_resolve_repository_without_token_sends_no_auth_header() -> None:
    client = _client(
        lambda r: httpx.Response(200, json={
            "full_name": "acme/web",
            "html_url": "https://gh.example/acme/web",
            "default_branch": "trunk",
        }),
        token=None,
    )
    info = _run(client.resolve_repository("acme", "web"))
    # clone_url absent → derived from html_url
    assert info.url == "https://gh.example/acme/web.git"


def test_resolve_repository_rejects_bad_names_without_http() -> None:
    client = _client(lambda r: httpx.Response(200, json={}))
    with pytest.raises(ValueError, match="owner"):
        _run(client.resolve_repository("ac/me", "web"))
    with pytest.raises(ValueError, match="repo"):
        _run(client.resolve_repository("acme", "w eb"))


def test_resolve_repository_missing_required_field_is_typed() -> None:
    client = _client(lambda r: httpx.Response(200, json={"full_name": "acme/web"}))
    with pytest.raises(GitHubError, match="missing required field"):
        _run(client.resolve_repository("acme", "web"))


# --- fetch_pull_request ------------------------------------------------------------


def test_fetch_pull_request_maps_head_base_and_files_deduped_sorted() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        path = str(request.url.path)
        if path == "/repos/acme/web/pulls/42":
            return httpx.Response(200, json={
                "number": 42,
                "title": "Add checkout",
                "state": "open",
                "html_url": "https://github.com/acme/web/pull/42",
                "head": {"sha": "h" * 40, "ref": "feat/checkout"},
                "base": {"sha": "b" * 40, "ref": "main"},
            })
        if path == "/repos/acme/web/pulls/42/files":
            return httpx.Response(200, json=[
                {"filename": "src/cart.ts"},
                {"filename": "src/checkout.ts"},
                {"filename": "src/cart.ts"},  # duplicate entry (cross-page repeat)
                {"no_filename": True},  # malformed entry — skipped, not a crash
            ])
        return httpx.Response(404, json={"message": "not scripted"})

    client = _client(handler)
    info = _run(client.fetch_pull_request("acme", "web", 42))
    assert info.number == 42
    assert info.title == "Add checkout"
    assert info.head_sha == "h" * 40
    assert info.head_ref == "feat/checkout"
    assert info.base_sha == "b" * 40
    assert info.base_ref == "main"
    # S6.1 files[] shape: de-duplicated, sorted repo-relative paths
    assert info.changed_files == ("src/cart.ts", "src/checkout.ts")


def test_fetch_pull_request_follows_link_next() -> None:
    calls = {"files": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        path = str(request.url.path)
        if path == "/repos/acme/web/pulls/7/files":
            calls["files"] += 1
            body = [{"filename": "a.py"}] if calls["files"] == 1 else [{"filename": "b.py"}]
            headers = {}
            if calls["files"] == 1:
                headers["Link"] = f'<{BASE}/repos/acme/web/pulls/7/files?page=2>; rel="next"'
            return httpx.Response(200, json=body, headers=headers)
        return httpx.Response(200, json={
            "number": 7,
            "title": "t",
            "state": "open",
            "html_url": "https://github.com/acme/web/pull/7",
            "head": {"sha": "h" * 40, "ref": "f"},
            "base": {"sha": "b" * 40, "ref": "main"},
        })

    client = _client(handler)
    info = _run(client.fetch_pull_request("acme", "web", 7))
    assert calls["files"] == 2
    assert info.changed_files == ("a.py", "b.py")


def test_pagination_page_cap_fails_loud() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url.path) == "/repos/acme/web/pulls/1":
            return httpx.Response(200, json={
                "number": 1,
                "title": "t",
                "state": "open",
                "html_url": "https://github.com/acme/web/pull/1",
                "head": {"sha": "h" * 40, "ref": "f"},
                "base": {"sha": "b" * 40, "ref": "main"},
            })
        calls["n"] += 1
        return httpx.Response(
            200,
            json=[{"filename": f"f{calls['n']}.py"}],
            headers={
                "Link": f'<{BASE}/repos/acme/web/pulls/1/files?page={calls["n"] + 1}>; rel="next"'
            },
        )

    client = _client(handler)
    with pytest.raises(GitHubError, match="exceeded"):
        _run(client.fetch_pull_request("acme", "web", 1))
    assert calls["n"] >= 10  # walked the full cap before failing


def test_pull_request_number_must_be_positive_int() -> None:
    client = _client(lambda r: httpx.Response(200, json={}))
    for number in (0, -3, True):
        with pytest.raises(ValueError, match="positive integer"):
            _run(client.fetch_pull_request("acme", "web", number))


# --- error mapping ---------------------------------------------------------------


def test_401_maps_to_auth_error_with_status() -> None:
    client = _client(lambda r: httpx.Response(401, json={"message": "Bad credentials"}))
    with pytest.raises(GitHubAuthError) as excinfo:
        _run(client.resolve_repository("acme", "web"))
    assert excinfo.value.status == 401
    assert "Bad credentials" in str(excinfo.value)


def test_404_maps_to_not_found() -> None:
    client = _client(lambda r: httpx.Response(404, json={"message": "Not Found"}))
    with pytest.raises(GitHubNotFoundError) as excinfo:
        _run(client.resolve_repository("acme", "ghost"))
    assert excinfo.value.status == 404


def test_500_maps_to_http_error_with_redacted_body() -> None:
    client = _client(
        lambda r: httpx.Response(500, json={"message": f"crash while forwarding Bearer {PAT}"})
    )
    with pytest.raises(GitHubHTTPError) as excinfo:
        _run(client.resolve_repository("acme", "web"))
    message = str(excinfo.value)
    assert excinfo.value.status == 500
    assert PAT not in message  # §17: the PAT never survives into the error
    assert REDACTED in message


def test_transport_failure_is_typed_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    client = _client(handler)
    with pytest.raises(GitHubError, match="unreachable"):
        _run(client.resolve_repository("acme", "web"))


def test_non_json_body_is_typed_error() -> None:
    client = _client(lambda r: httpx.Response(200, text="<html>oops</html>"))
    with pytest.raises(GitHubError, match="non-JSON"):
        _run(client.resolve_repository("acme", "web"))

