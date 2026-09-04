"""S7.1 golden set + runner tests (build bible §22, §31.7, §17).

Covers: the canonical golden loads and gates 100% through the real client
on the fake server; the PAT-redaction expectation genuinely fires; the
loader fails loud on every malformed shape (never skips a fixture); and a
deliberately broken fixture fails the §31.7 gate.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Callable
from pathlib import Path

import pytest
from qa_copilot_integrations.github.client import REDACTED
from qa_copilot_integrations.github.golden import (
    FixtureCall,
    FixtureResponse,
    GitHubFixture,
    GitHubGoldenSet,
    GitHubGoldenSetError,
    default_golden_path,
    load_github_golden_set,
)
from qa_copilot_integrations.github.runner import (
    run_github_eval,
)

GOLDEN = default_golden_path()
REPO_BODY = {
    "full_name": "acme/web",
    "html_url": "https://github.com/acme/web",
    "default_branch": "trunk",
}


def test_canonical_golden_loads_and_gates_pass() -> None:
    golden = load_github_golden_set(GOLDEN)
    assert golden.name == "github_client"
    assert len(golden.fixtures) == 10
    assert len({f.id for f in golden.fixtures}) == 10  # unique ids
    assert golden.targets["pass_min"] == 1.0  # §31.7: 100% contract gate
    report = run_github_eval(golden)
    assert report.fixtures == 10
    assert report.passed == 10
    assert report.pass_fraction == 1.0
    assert report.gate_passed
    assert all(case.passed and case.error is None for case in report.cases)


def test_redaction_expectation_is_enforced() -> None:
    """The golden must pin the PAT-redaction contract, and it must hold."""
    golden = load_github_golden_set(GOLDEN)
    redaction_fixtures = [f for f in golden.fixtures if "message_not_contains" in f.expect]
    assert redaction_fixtures, "golden must pin the PAT-redaction contract (§17)"
    for fixture in redaction_fixtures:
        forbidden = fixture.expect["message_not_contains"]
        assert any(
            isinstance(token, str) and token[:4] in ("ghp_", "gho_") for token in forbidden
        ), "redaction expectation must pin a PAT-shaped secret"
    report = run_github_eval(golden)
    for fixture in redaction_fixtures:
        case = next(c for c in report.cases if c.fixture_id == fixture.id)
        actual = case.actual if isinstance(case.actual, str) else ""
        for token in fixture.expect["message_not_contains"]:
            assert token not in actual  # the PAT never survives into the error
        assert REDACTED in actual


# --- loader strictness (fail loud, never skip) -----------------------------------


def _valid_golden_dict() -> dict[str, object]:
    return {
        "name": "t",
        "version": "v1",
        "description": "d",
        "source": {"spec": "test"},
        "targets": {"pass_min": 1.0},
        "fixtures": [
            {
                "id": "f1",
                "title": "t",
                "call": {"kind": "resolve_repository", "owner": "acme", "repo": "web"},
                "responses": [
                    {
                        "path": "/repos/acme/web",
                        "status": 200,
                        "body": {
                            "full_name": "acme/web",
                            "html_url": "https://gh.example/acme/web",
                            "default_branch": "main",
                        },
                    }
                ],
                "expect": {"kind": "ok", "default_branch": "main"},
            }
        ],
    }


def _write(tmp_path: Path, doc: object) -> Path:
    path = tmp_path / "golden.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.pop("name"),  # missing name
        lambda d: d["targets"].pop("pass_min"),  # missing pass_min (§31.7)
        lambda d: d["targets"].update(pass_min=2.0),  # target out of [0, 1]
        lambda d: d["fixtures"].append(copy.deepcopy(d["fixtures"][0])),  # duplicate id
        lambda d: d["fixtures"][0]["call"].update(kind="nope"),  # unknown call kind
        lambda d: (  # fetch_pull_request without number
            d["fixtures"][0]["call"].update(kind="fetch_pull_request"),
            d["fixtures"][0]["call"].pop("number", None),
        ),
        lambda d: d["fixtures"][0]["expect"].update(bogus=1),  # unknown expect key
        lambda d: d["fixtures"][0]["expect"].update(kind="error", error="bogus"),  # bad error kind
        lambda d: d["fixtures"][0]["responses"][0].update(status="200"),  # non-int status
        lambda d: d["fixtures"].clear(),  # empty fixtures
    ],
    ids=[
        "missing name",
        "missing pass_min",
        "target out of range",
        "duplicate fixture id",
        "unknown call kind",
        "fetch_pull_request without number",
        "unknown expect key",
        "bad error kind",
        "non-int status",
        "empty fixtures",
    ],
)
def test_loader_rejects_malformed_golden(
    tmp_path: Path, mutate: Callable[[object], object]
) -> None:
    doc = _valid_golden_dict()
    mutate(doc)
    with pytest.raises(GitHubGoldenSetError):
        load_github_golden_set(_write(tmp_path, doc))


def test_loader_missing_file_is_oserror(tmp_path: Path) -> None:
    with pytest.raises(OSError):
        load_github_golden_set(tmp_path / "nope.json")


def test_loader_non_json_is_value_error(tmp_path: Path) -> None:
    path = tmp_path / "golden.json"
    path.write_text("not json", encoding="utf-8")
    with pytest.raises(ValueError):
        load_github_golden_set(path)


# --- gate scoring (in-memory sets, real client + fake server) ----------------------


def _set(fixtures: tuple[GitHubFixture, ...], *, pass_min: float = 1.0) -> GitHubGoldenSet:
    return GitHubGoldenSet(
        name="t",
        version="v1",
        description="d",
        source={"spec": "test"},
        targets={"pass_min": pass_min},
        fixtures=fixtures,
    )


def _resolve_fixture(
    *,
    expect_extra: dict[str, object] | None = None,
    body: dict[str, object] | None = None,
    expect_auth: str | None = None,
) -> GitHubFixture:
    return GitHubFixture(
        id="f1",
        title="t",
        call=FixtureCall(kind="resolve_repository", owner="acme", repo="web"),
        responses=(
            FixtureResponse(path="/repos/acme/web", status=200, body=body or dict(REPO_BODY)),
        ),
        expect={"kind": "ok", **(expect_extra or {})},
        expect_auth=expect_auth,
    )


def test_failing_fixture_fails_gate() -> None:
    # fixture expects default_branch=main, the scripted server says trunk
    report = run_github_eval(_set((_resolve_fixture(expect_extra={"default_branch": "main"}),)))
    assert report.fixtures == 1
    assert report.passed == 0
    assert report.pass_fraction == 0.0
    assert report.gate_passed is False
    assert report.cases[0].passed is False
    assert report.cases[0].error is not None


def test_expect_auth_mismatch_fails() -> None:
    report = run_github_eval(_set((_resolve_fixture(expect_auth="Bearer wrong-token"),)))
    case = report.cases[0]
    assert case.passed is False
    assert case.error is not None and "Authorization" in case.error


def test_passing_fixture_passes_gate() -> None:
    report = run_github_eval(_set((_resolve_fixture(expect_extra={"default_branch": "trunk"}),)))
    assert report.passed == 1
    assert report.gate_passed is True


def test_gate_respects_pass_min_below_one() -> None:
    # one failing + one passing fixture: fraction 0.5 passes a 0.5 gate, fails a 1.0 gate
    fixtures = (
        _resolve_fixture(expect_extra={"default_branch": "main"}),  # fails
        GitHubFixture(
            id="f2",  # passes
            title="t",
            call=FixtureCall(kind="resolve_repository", owner="acme", repo="web"),
            responses=(
                FixtureResponse(path="/repos/acme/web", status=200, body=dict(REPO_BODY)),
            ),
            expect={"kind": "ok", "default_branch": "trunk"},
        ),
    )
    assert run_github_eval(_set(fixtures, pass_min=0.5)).gate_passed is True
    assert run_github_eval(_set(fixtures, pass_min=1.0)).gate_passed is False

