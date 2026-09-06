"""Golden-set loader for the S7.4 Jira integration (build bible §22).

Two golden artifacts, one gate (``targets.pass_min`` = 1.0):

* ``mappings`` — deterministic failure → issue ``fields`` pins. The S7.4
  exit criterion is "failure → issue mapping matches golden 100%": each pin
  fixes the exact ``build_issue_payload(failure, project_key)`` body the
  client will POST (summary / ADF description / labels / issuetype).
* ``fixtures`` — fake-server client replays, one per client call
  (``create_issue`` / ``update_issue`` / ``fetch_issue``) against scripted
  Jira REST v2 responses and the exact typed result — or typed error — the
  client must produce, including the token-redaction contract (§17).

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

from qa_copilot_integrations.jira.client import validate_issue_key, validate_project_key

#: ``packages/integrations`` — this file lives in
#: ``packages/integrations/src/qa_copilot_integrations/jira/``.
_PKG_ROOT = Path(__file__).resolve().parents[3]

CALL_KINDS = ("create_issue", "update_issue", "fetch_issue")
ERROR_KINDS = ("auth", "not_found", "http")
OK_EXPECT_KEYS = ("key", "id", "summary", "status", "url")
ERROR_EXPECT_KEYS = ("error", "status", "message_contains", "message_not_contains")
#: Exact key set of a Jira issue ``fields`` payload (build_issue_payload).
PAYLOAD_KEYS = ("project", "issuetype", "summary", "description", "labels")


class JiraGoldenSetError(ValueError):
    """A malformed golden set (fail loud — never skip a fixture)."""


@dataclass(frozen=True, slots=True)
class MappingPin:
    """One deterministic failure → issue payload pin (S7.4: 100% match)."""

    id: str
    project_key: str
    failure: dict[str, Any]
    expect_payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class FixtureCall:
    """One client call to replay.

    ``create_issue`` / ``update_issue`` POST the payload of the referenced
    mapping pin (the deterministic body, pinned 100%); ``update_issue`` /
    ``fetch_issue`` also carry the issue ``key``.
    """

    kind: str
    key: str | None = None
    mapping: str | None = None


@dataclass(frozen=True, slots=True)
class FixtureResponse:
    """One scripted Jira REST v2 response (matched by path, consumed in order)."""

    path: str
    status: int
    body: Any


@dataclass(frozen=True, slots=True)
class JiraFixture:
    """One golden fixture: scripted responses + the exact expected outcome."""

    id: str
    title: str
    call: FixtureCall
    responses: tuple[FixtureResponse, ...]
    expect: dict[str, Any]
    expect_auth: str | None = None


@dataclass(frozen=True, slots=True)
class JiraGoldenSet:
    """The S7.4 Jira golden set (§22): mapping pins + client fixtures."""

    name: str
    version: str
    description: str
    source: dict[str, str]
    targets: dict[str, float]
    mappings: tuple[MappingPin, ...]
    fixtures: tuple[JiraFixture, ...]


def default_golden_path() -> Path:
    """Canonical golden location: ``packages/integrations/golden/jira_v1.json``."""
    return _PKG_ROOT / "golden" / "jira_v1.json"


def _expect_str(raw: Mapping[str, Any], key: str, case_id: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value:
        raise JiraGoldenSetError(f"case {case_id}: missing required {key!r}")
    return value


def _load_mapping(raw: Any, mapping_id: str) -> MappingPin:
    if not isinstance(raw, Mapping):
        raise JiraGoldenSetError(f"case {mapping_id}: `mapping` must be an object")
    project_key = raw.get("project_key")
    try:
        validate_project_key(project_key)  # type: ignore[arg-type]
    except ValueError as exc:
        raise JiraGoldenSetError(f"case {mapping_id}: {exc}") from exc
    failure = raw.get("failure")
    if not isinstance(failure, Mapping):
        raise JiraGoldenSetError(f"case {mapping_id}: `failure` must be an object")
    expect_payload = raw.get("expect_payload")
    if not isinstance(expect_payload, Mapping):
        raise JiraGoldenSetError(f"case {mapping_id}: `expect_payload` must be an object")
    if set(expect_payload) != set(PAYLOAD_KEYS):
        raise JiraGoldenSetError(
            f"case {mapping_id}: `expect_payload` keys must be exactly {PAYLOAD_KEYS}"
        )
    return MappingPin(
        id=mapping_id,
        project_key=str(project_key),
        failure=dict(failure),
        expect_payload=dict(expect_payload),
    )


def _load_call(raw: Any, fixture_id: str, mapping_ids: frozenset[str]) -> FixtureCall:
    if not isinstance(raw, Mapping):
        raise JiraGoldenSetError(f"fixture {fixture_id}: `call` must be an object")
    kind = raw.get("kind")
    if kind not in CALL_KINDS:
        raise JiraGoldenSetError(f"fixture {fixture_id}: `call.kind` must be one of {CALL_KINDS}")
    key = raw.get("key")
    if key is not None:
        try:
            validate_issue_key(key)
        except ValueError as exc:
            raise JiraGoldenSetError(f"fixture {fixture_id}: {exc}") from exc
    if kind in ("update_issue", "fetch_issue") and key is None:
        raise JiraGoldenSetError(f"fixture {fixture_id}: {kind} requires `call.key`")
    mapping = raw.get("mapping")
    if kind in ("create_issue", "update_issue") and mapping is None:
        raise JiraGoldenSetError(f"fixture {fixture_id}: {kind} requires `call.mapping`")
    if mapping is not None:
        if not isinstance(mapping, str) or mapping not in mapping_ids:
            raise JiraGoldenSetError(
                f"fixture {fixture_id}: `call.mapping` must reference a golden mapping id"
            )
    unknown = set(raw) - {"kind", "key", "mapping"}
    if unknown:
        raise JiraGoldenSetError(f"fixture {fixture_id}: unknown call keys {sorted(unknown)!r}")
    return FixtureCall(kind=str(kind), key=key, mapping=mapping)


def _load_responses(raw: Any, fixture_id: str) -> tuple[FixtureResponse, ...]:
    if not isinstance(raw, list) or not raw:
        raise JiraGoldenSetError(f"fixture {fixture_id}: `responses` must be a non-empty array")
    out: list[FixtureResponse] = []
    for entry in raw:
        if not isinstance(entry, Mapping):
            raise JiraGoldenSetError(f"fixture {fixture_id}: response entry must be an object")
        path = entry.get("path")
        if not isinstance(path, str) or not path.startswith("/"):
            raise JiraGoldenSetError(f"fixture {fixture_id}: response `path` must start with '/'")
        status = entry.get("status")
        if isinstance(status, bool) or not isinstance(status, int) or not 100 <= status <= 599:
            raise JiraGoldenSetError(f"fixture {fixture_id}: response `status` must be an int")
        if "body" not in entry:
            raise JiraGoldenSetError(f"fixture {fixture_id}: response missing `body`")
        out.append(FixtureResponse(path=path, status=status, body=entry["body"]))
    return tuple(out)


def _load_expect(raw: Any, fixture_id: str) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise JiraGoldenSetError(f"fixture {fixture_id}: `expect` must be an object")
    kind = raw.get("kind")
    allowed: tuple[str, ...]
    if kind == "ok":
        allowed = ("kind", *OK_EXPECT_KEYS)
    elif kind == "error":
        if raw.get("error") not in ERROR_KINDS:
            raise JiraGoldenSetError(
                f"fixture {fixture_id}: `expect.error` must be one of {ERROR_KINDS}"
            )
        allowed = ("kind", *ERROR_EXPECT_KEYS)
    else:
        raise JiraGoldenSetError(f"fixture {fixture_id}: `expect.kind` must be ok|error")
    for key in raw:
        if key not in allowed:
            raise JiraGoldenSetError(f"fixture {fixture_id}: unknown expect key {key!r}")
    return dict(raw)


def load_jira_golden_set(path: Path) -> JiraGoldenSet:
    """Load + strictly validate a Jira golden set (fail loud)."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, Mapping):
        raise JiraGoldenSetError("golden root must be an object")
    name = _expect_str(raw, "name", "<set>")
    version = _expect_str(raw, "version", "<set>")
    description = _expect_str(raw, "description", "<set>")
    source_raw = raw.get("source")
    if not isinstance(source_raw, Mapping) or any(
        not isinstance(k, str) or not isinstance(v, str) for k, v in source_raw.items()
    ):
        raise JiraGoldenSetError("golden `source` must be a string→string object")
    targets_raw = raw.get("targets")
    if not isinstance(targets_raw, Mapping):
        raise JiraGoldenSetError("golden `targets` must be an object")
    targets: dict[str, float] = {}
    for key, value in targets_raw.items():
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not 0 <= float(value) <= 1
        ):
            raise JiraGoldenSetError(f"golden target {key!r} must be a fraction in [0, 1]")
        targets[str(key)] = float(value)
    if "pass_min" not in targets:
        raise JiraGoldenSetError("golden `targets` must define `pass_min` (§31.7)")

    mappings_raw = raw.get("mappings")
    if not isinstance(mappings_raw, list) or not mappings_raw:
        raise JiraGoldenSetError("golden `mappings` must be a non-empty array")
    seen_ids: set[str] = set()
    mappings: list[MappingPin] = []
    for entry in mappings_raw:
        if not isinstance(entry, Mapping):
            raise JiraGoldenSetError("mapping entry must be an object")
        mid = _expect_str(entry, "id", "<mapping>")
        if mid in seen_ids:
            raise JiraGoldenSetError(f"duplicate case id {mid!r}")
        seen_ids.add(mid)
        mappings.append(_load_mapping(entry, mid))
    mapping_ids = frozenset(pin.id for pin in mappings)

    fixtures_raw = raw.get("fixtures")
    if not isinstance(fixtures_raw, list) or not fixtures_raw:
        raise JiraGoldenSetError("golden `fixtures` must be a non-empty array")
    fixtures: list[JiraFixture] = []
    for entry in fixtures_raw:
        if not isinstance(entry, Mapping):
            raise JiraGoldenSetError("fixture entry must be an object")
        fid = _expect_str(entry, "id", "<fixture>")
        if fid in seen_ids:
            raise JiraGoldenSetError(f"duplicate case id {fid!r}")
        seen_ids.add(fid)
        fixtures.append(
            JiraFixture(
                id=fid,
                title=_expect_str(entry, "title", fid),
                call=_load_call(entry.get("call"), fid, mapping_ids),
                responses=_load_responses(entry.get("responses"), fid),
                expect=_load_expect(entry.get("expect"), fid),
                expect_auth=entry.get("expect_auth"),
            )
        )
    return JiraGoldenSet(
        name=name,
        version=version,
        description=description,
        source=dict(source_raw),
        targets=targets,
        mappings=tuple(mappings),
        fixtures=tuple(fixtures),
    )


__all__ = [
    "CALL_KINDS",
    "ERROR_KINDS",
    "FixtureCall",
    "FixtureResponse",
    "JiraFixture",
    "JiraGoldenSet",
    "JiraGoldenSetError",
    "MappingPin",
    "OK_EXPECT_KEYS",
    "PAYLOAD_KEYS",
    "default_golden_path",
    "load_jira_golden_set",
]
