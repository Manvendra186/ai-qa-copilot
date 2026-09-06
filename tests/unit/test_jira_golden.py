"""S7.4 golden set + runner tests (build bible §22, §31.7, §17).

Covers: the canonical golden loads and gates 100% through the real client
on the fake server; the mapping pins match ``build_issue_payload`` 100%
(S7.4 exit criterion); the token-redaction expectation genuinely fires; the
loader fails loud on malformed shapes (never skips a fixture); and a
deliberately broken fixture fails the §31.7 gate.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from qa_copilot_integrations.jira.client import REDACTED
from qa_copilot_integrations.jira.golden import (
    FixtureCall,
    FixtureResponse,
    JiraFixture,
    JiraGoldenSet,
    JiraGoldenSetError,
    default_golden_path,
    load_jira_golden_set,
)
from qa_copilot_integrations.jira.issue import build_issue_payload
from qa_copilot_integrations.jira.runner import REPLAY_TOKEN, run_jira_eval

GOLDEN = default_golden_path()


def test_canonical_golden_loads_and_gates_pass() -> None:
    golden = load_jira_golden_set(GOLDEN)
    assert golden.name == "jira_client"
    assert len(golden.mappings) == 1
    assert len(golden.fixtures) == 7
    assert golden.targets["pass_min"] == 1.0  # §31.7: 100% contract gate
    report = run_jira_eval(golden)
    assert report.mappings == 1
    assert report.fixtures == 7
    assert report.passed == 8
    assert report.score == 1.0
    assert report.gate_met
    assert all(case.passed and case.error is None for case in report.cases)


def test_mapping_pins_match_builder_exactly() -> None:
    """The S7.4 exit criterion: failure → issue mapping matches golden 100%.

    Each mapping pin's ``expect_payload`` must be byte-for-byte what
    ``build_issue_payload`` produces for the same input (deterministic).
    """
    golden = load_jira_golden_set(GOLDEN)
    for pin in golden.mappings:
        assert build_issue_payload(pin.failure, pin.project_key) == pin.expect_payload


def test_redaction_expectation_is_enforced() -> None:
    """The golden must pin the token-redaction contract, and it must hold."""
    golden = load_jira_golden_set(GOLDEN)
    redaction_fixtures = [f for f in golden.fixtures if "message_not_contains" in f.expect]
    assert redaction_fixtures, "golden must pin the redaction contract (§17)"
    for fixture in redaction_fixtures:
        forbidden = fixture.expect["message_not_contains"]
        assert any(isinstance(token, str) and token[:5] == "ATATT" for token in forbidden), (
            "redaction expectation must pin an ATATT-shaped token"
        )
    report = run_jira_eval(golden)
    for fixture in redaction_fixtures:
        case = next(c for c in report.cases if c.case_id == fixture.id)
        message = case.actual["message"] if isinstance(case.actual, dict) else ""
        for token in fixture.expect["message_not_contains"]:
            assert token not in message  # the token never survives into the error
        assert REDACTED in message


# --- loader strictness (fail loud, never skip) -----------------------------------


def _valid_golden_dict() -> dict[str, object]:
    failure = {"category": "regression", "root_cause": "tax before coupon"}
    return {
        "name": "t",
        "version": "v1",
        "description": "d",
        "source": {"spec": "test"},
        "targets": {"pass_min": 1.0},
        "mappings": [
            {
                "id": "m1",
                "project_key": "QA",
                "failure": dict(failure),
                "expect_payload": build_issue_payload(dict(failure), "QA"),
            }
        ],
        "fixtures": [
            {
                "id": "f1",
                "title": "t",
                "call": {"kind": "create_issue", "mapping": "m1"},
                "responses": [
                    {
                        "path": "/rest/api/2/issue",
                        "status": 200,
                        "body": {"key": "QA-1", "id": "1", "fields": {"summary": "s"}},
                    }
                ],
                "expect": {"kind": "ok", "key": "QA-1"},
            }
        ],
    }


def _write(tmp_path: Path, doc: object) -> Path:
    import json

    path = tmp_path / "golden.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    "mutate",
    [
        lambda doc: doc.__setitem__("name", None),
        lambda doc: doc.__setitem__("targets", {"pass_min": 2.0}),
        lambda doc: doc.__setitem__("targets", {}),
        lambda doc: doc.__setitem__("mappings", []),
        lambda doc: doc["mappings"][0].__setitem__("project_key", "bad key"),
        lambda doc: doc["mappings"][0].__setitem__("expect_payload", {"project": {"key": "QA"}}),
        lambda doc: doc["mappings"].append(dict(doc["mappings"][0])),
        lambda doc: doc["fixtures"][0]["call"].__setitem__("kind", "bogus"),
        lambda doc: doc["fixtures"][0]["call"].__setitem__("mapping", None),
        lambda doc: doc["fixtures"][0]["call"].__setitem__("kind", "update_issue"),
        lambda doc: doc["fixtures"][0]["expect"].__setitem__("bogus_key", True),
        lambda doc: doc["fixtures"][0].__setitem__("expect", {"kind": "error", "error": "bogus"}),
        lambda doc: doc.__setitem__("fixtures", []),
    ],
    ids=[
        "missing name",
        "pass_min out of range",
        "missing pass_min",
        "empty mappings",
        "bad project key",
        "expect_payload wrong keys",
        "duplicate mapping ids",
        "unknown call kind",
        "create without mapping",
        "update without key",
        "unknown expect key",
        "bad error kind",
        "empty fixtures",
    ],
)
def test_loader_rejects_malformed_golden(
    tmp_path: Path, mutate: Callable[[object], object]
) -> None:
    doc = _valid_golden_dict()
    mutate(doc)
    with pytest.raises(JiraGoldenSetError):
        load_jira_golden_set(_write(tmp_path, doc))


def test_loader_missing_file_is_oserror(tmp_path: Path) -> None:
    with pytest.raises(OSError):
        load_jira_golden_set(tmp_path / "nope.json")


def test_loader_non_json_is_value_error(tmp_path: Path) -> None:
    path = tmp_path / "golden.json"
    path.write_text("not json", encoding="utf-8")
    with pytest.raises(ValueError):
        load_jira_golden_set(path)


# --- gate scoring (in-memory sets, real client + fake server) ----------------------


def _fetch_fixture(
    *,
    expect: dict[str, object] | None = None,
    expect_auth: str | None = None,
    key: str = "QA-1",
) -> JiraFixture:
    return JiraFixture(
        id="f1",
        title="t",
        call=FixtureCall(kind="fetch_issue", key=key),
        responses=(
            FixtureResponse(
                path=f"/rest/api/2/issue/{key}",
                status=200,
                body={"key": key, "id": "1", "fields": {"summary": "s"}},
            ),
        ),
        expect=expect or {"kind": "ok", "key": key},
        expect_auth=expect_auth,
    )


def _set(fixtures: tuple[JiraFixture, ...], *, pass_min: float = 1.0) -> JiraGoldenSet:
    # ``fetch_issue`` fixtures need no mapping pin, so ``mappings`` is empty and
    # the report score reflects *only* the client fixtures under test.
    return JiraGoldenSet(
        name="t",
        version="v1",
        description="d",
        source={"spec": "test"},
        targets={"pass_min": pass_min},
        mappings=(),
        fixtures=fixtures,
    )


def test_passing_fixture_passes_gate() -> None:
    report = run_jira_eval(_set((_fetch_fixture(),)))
    assert report.fixtures == 1
    assert report.passed == 1
    assert report.score == 1.0
    assert report.gate_met


def test_failing_fixture_fails_gate() -> None:
    # fixture expects key QA-999, the scripted server returns QA-1
    report = run_jira_eval(_set((_fetch_fixture(expect={"kind": "ok", "key": "QA-999"}),)))
    assert report.passed == 0
    assert report.score == 0.0
    assert report.gate_met is False
    assert report.cases[0].passed is False
    assert report.cases[0].error is not None


def test_expect_auth_mismatch_fails() -> None:
    report = run_jira_eval(_set((_fetch_fixture(expect_auth="Bearer wrong-token"),)))
    case = report.cases[0]
    assert case.passed is False
    assert case.error is not None and "Authorization" in case.error


def test_gate_respects_pass_min_below_one() -> None:
    # one failing (wrong key) + one passing fixture: 0.5 passes a 0.5 gate
    fixtures = (
        _fetch_fixture(expect={"kind": "ok", "key": "QA-999"}),  # fails
        _fetch_fixture(),  # passes
    )
    assert run_jira_eval(_set(fixtures, pass_min=0.5)).gate_met is True
    assert run_jira_eval(_set(fixtures, pass_min=1.0)).gate_met is False


def test_replay_token_is_the_pinned_bearer_token() -> None:
    # the runner's replay token is the ATATT token the golden expect_auth pins
    assert REPLAY_TOKEN[:5] == "ATATT"
