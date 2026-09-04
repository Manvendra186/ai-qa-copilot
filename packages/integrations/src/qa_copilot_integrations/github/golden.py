"""Golden-set loader for the S7.1 GitHub client (build bible §22).

The golden (``packages/integrations/golden/github_v1.json``) is a set of
*fake-server fixtures*: each fixture pins one client call
(``resolve_repository`` / ``fetch_pull_request``) against a scripted
sequence of GitHub API responses and the exact typed result — or typed
error — the client must produce, including the PAT-redaction contract
(§17; S7.1 exit: "PR changed-files contract matches golden 100%").

Same shape family as the other goldens (``schema_version`` / ``name`` /
``version`` / ``description`` / ``source`` / ``targets`` / ``fixtures``);
the §31.7 gate is ``targets.pass_min`` (1.0 = 100% field match).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: ``packages/integrations`` — this file lives in
#: ``packages/integrations/src/qa_copilot_integrations/github/``.
_PKG_ROOT = Path(__file__).resolve().parents[3]

CALL_KINDS = ("resolve_repository", "fetch_pull_request")
ERROR_KINDS = ("auth", "not_found", "http")
OK_EXPECT_KEYS = (
    "url",
    "default_branch",
    "full_name",
    "html_url",
    "head_sha",
    "head_ref",
    "base_sha",
    "base_ref",
    "number",
    "state",
    "files",
)
ERROR_EXPECT_KEYS = ("error", "status", "message_contains", "message_not_contains")


class GitHubGoldenSetError(ValueError):
    """A malformed golden set (fail loud — never skip a fixture)."""


@dataclass(frozen=True, slots=True)
class FixtureCall:
    """One client call to replay: kind + coordinates (PR number when set)."""

    kind: str
    owner: str
    repo: str
    number: int | None = None


@dataclass(frozen=True, slots=True)
class FixtureResponse:
    """One scripted GitHub API response (matched by path, consumed in order).

    ``link`` is an optional raw ``Link`` header; the ``{{base}}`` placeholder
    is replaced with the fake server's real base URL at serve time.
    """

    path: str
    status: int
    body: Any
    link: str | None = None


@dataclass(frozen=True, slots=True)
class GitHubFixture:
    """One golden fixture: scripted responses + the exact expected outcome."""

    id: str
    title: str
    call: FixtureCall
    responses: tuple[FixtureResponse, ...]
    expect: dict[str, Any]
    expect_auth: str | None = None


@dataclass(frozen=True, slots=True)
class GitHubGoldenSet:
    """The S7.1 GitHub client golden set (§22)."""

    name: str
    version: str
    description: str
    source: dict[str, str]
    targets: dict[str, float]
    fixtures: tuple[GitHubFixture, ...]


def default_golden_path() -> Path:
    """Canonical golden location: ``packages/integrations/golden/github_v1.json``."""
    return _PKG_ROOT / "golden" / "github_v1.json"


def _expect_str(raw: Mapping[str, Any], key: str, fixture_id: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value:
        raise GitHubGoldenSetError(f"fixture {fixture_id}: missing required {key!r}")
    return value


def _load_call(raw: Any, fixture_id: str) -> FixtureCall:
    if not isinstance(raw, Mapping):
        raise GitHubGoldenSetError(f"fixture {fixture_id}: `call` must be an object")
    kind = raw.get("kind")
    if kind not in CALL_KINDS:
        raise GitHubGoldenSetError(
            f"fixture {fixture_id}: `call.kind` must be one of {CALL_KINDS}, got {kind!r}"
        )
    owner = _expect_str(raw, "owner", fixture_id)
    repo = _expect_str(raw, "repo", fixture_id)
    number: int | None = None
    if kind == "fetch_pull_request":
        number = raw.get("number")
        if isinstance(number, bool) or not isinstance(number, int) or number < 1:
            raise GitHubGoldenSetError(
                f"fixture {fixture_id}: `call.number` must be a positive int"
            )
    return FixtureCall(kind=kind, owner=owner, repo=repo, number=number)


def _load_responses(raw: Any, fixture_id: str) -> tuple[FixtureResponse, ...]:
    if not isinstance(raw, list):
        raise GitHubGoldenSetError(f"fixture {fixture_id}: `responses` must be an array")
    out: list[FixtureResponse] = []
    for entry in raw:
        if not isinstance(entry, Mapping):
            raise GitHubGoldenSetError(f"fixture {fixture_id}: response entry must be an object")
        path = _expect_str(entry, "path", fixture_id)
        status = entry.get("status")
        if isinstance(status, bool) or not isinstance(status, int) or not 100 <= status < 600:
            raise GitHubGoldenSetError(
                f"fixture {fixture_id}: response status must be an HTTP code"
            )
        link = entry.get("link")
        if link is not None and not isinstance(link, str):
            raise GitHubGoldenSetError(f"fixture {fixture_id}: response `link` must be a string")
        out.append(FixtureResponse(path=path, status=status, body=entry.get("body"), link=link))
    if not out:
        raise GitHubGoldenSetError(f"fixture {fixture_id}: at least one scripted response required")
    return tuple(out)


def _load_expect(raw: Any, fixture_id: str) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise GitHubGoldenSetError(f"fixture {fixture_id}: `expect` must be an object")
    kind = raw.get("kind")
    allowed: tuple[str, ...]
    if kind == "ok":
        allowed = ("kind", *OK_EXPECT_KEYS)
    elif kind == "error":
        if raw.get("error") not in ERROR_KINDS:
            raise GitHubGoldenSetError(
                f"fixture {fixture_id}: `expect.error` must be one of {ERROR_KINDS}"
            )
        allowed = ("kind", *ERROR_EXPECT_KEYS)
    else:
        raise GitHubGoldenSetError(f"fixture {fixture_id}: `expect.kind` must be ok|error")
    for key in raw:
        if key not in allowed:
            raise GitHubGoldenSetError(f"fixture {fixture_id}: unknown expect key {key!r}")
    return dict(raw)


def load_github_golden_set(path: Path) -> GitHubGoldenSet:
    """Load + strictly validate a GitHub client golden set (fail loud)."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, Mapping):
        raise GitHubGoldenSetError("golden root must be an object")
    name = _expect_str(raw, "name", "<set>")
    version = _expect_str(raw, "version", "<set>")
    description = _expect_str(raw, "description", "<set>")
    source_raw = raw.get("source")
    if not isinstance(source_raw, Mapping) or any(
        not isinstance(k, str) or not isinstance(v, str) for k, v in source_raw.items()
    ):
        raise GitHubGoldenSetError("golden `source` must be a string→string object")
    targets_raw = raw.get("targets")
    if not isinstance(targets_raw, Mapping):
        raise GitHubGoldenSetError("golden `targets` must be an object")
    targets: dict[str, float] = {}
    for key, value in targets_raw.items():
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not 0 <= float(value) <= 1
        ):
            raise GitHubGoldenSetError(f"golden target {key!r} must be a fraction in [0, 1]")
        targets[str(key)] = float(value)
    if "pass_min" not in targets:
        raise GitHubGoldenSetError("golden `targets` must define `pass_min` (§31.7)")

    fixtures_raw = raw.get("fixtures")
    if not isinstance(fixtures_raw, list) or not fixtures_raw:
        raise GitHubGoldenSetError("golden `fixtures` must be a non-empty array")

    seen_ids: set[str] = set()
    fixtures: list[GitHubFixture] = []
    for entry in fixtures_raw:
        if not isinstance(entry, Mapping):
            raise GitHubGoldenSetError("fixture entry must be an object")
        fid = _expect_str(entry, "id", "<fixture>")
        if fid in seen_ids:
            raise GitHubGoldenSetError(f"duplicate fixture id {fid!r}")
        seen_ids.add(fid)
        fixtures.append(
            GitHubFixture(
                id=fid,
                title=_expect_str(entry, "title", fid),
                call=_load_call(entry.get("call"), fid),
                responses=_load_responses(entry.get("responses"), fid),
                expect=_load_expect(entry.get("expect"), fid),
                expect_auth=entry.get("expect_auth"),
            )
        )
    return GitHubGoldenSet(
        name=name,
        version=version,
        description=description,
        source=dict(source_raw),
        targets=targets,
        fixtures=tuple(fixtures),
    )


__all__ = [
    "CALL_KINDS",
    "ERROR_KINDS",
    "FixtureCall",
    "FixtureResponse",
    "GitHubFixture",
    "GitHubGoldenSet",
    "GitHubGoldenSetError",
    "default_golden_path",
    "load_github_golden_set",
]
