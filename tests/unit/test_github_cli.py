"""S7.1 GitHub CLI tests (build bible §19 S7.1, §17).

The CLI contract: JSON on stdout (machine-parseable), the human summary on
stderr, stable exit codes (0 ok / 1 API or gate failure / 2 usage or golden
load error), and the PAT never appears in either stream (§17).

``repo`` / ``pr-files`` run against the runner's loopback fake GitHub
server (deterministic, offline); ``golden`` replays the canonical set.
"""

from __future__ import annotations

import json
from io import StringIO
from pathlib import Path

import pytest
from qa_copilot_integrations.github import cli
from qa_copilot_integrations.github.golden import (
    FixtureCall,
    FixtureResponse,
    GitHubFixture,
)
from qa_copilot_integrations.github.runner import FakeGitHubServer

PAT = "ghp_S71CliSecretToken0123456"

REPO_BODY = {
    "full_name": "acme/web",
    "html_url": "https://github.com/acme/web",
    "clone_url": "https://github.com/acme/web.git",
    "default_branch": "main",
}
PR_BODY = {
    "number": 9,
    "title": "Fix cart",
    "state": "open",
    "html_url": "https://github.com/acme/web/pull/9",
    "head": {"sha": "h" * 40, "ref": "fix/cart"},
    "base": {"sha": "b" * 40, "ref": "main"},
}


def _repo_fixture(status: int = 200, body: dict[str, object] | None = None) -> GitHubFixture:
    return GitHubFixture(
        id="cli-repo",
        title="cli repo",
        call=FixtureCall(kind="resolve_repository", owner="acme", repo="web"),
        responses=(
            FixtureResponse(path="/repos/acme/web", status=status, body=body or dict(REPO_BODY)),
        ),
        expect={},
    )


def _pr_fixture() -> GitHubFixture:
    return GitHubFixture(
        id="cli-pr",
        title="cli pr",
        call=FixtureCall(kind="fetch_pull_request", owner="acme", repo="web", number=9),
        responses=(
            FixtureResponse(path="/repos/acme/web/pulls/9", status=200, body=dict(PR_BODY)),
            FixtureResponse(
                path="/repos/acme/web/pulls/9/files",
                status=200,
                body=[{"filename": "src/cart.ts"}, {"filename": "src/cart.ts"}],
            ),
        ),
        expect={},
    )


def _run_cli(
    argv: list[str],
    *,
    base_url: str | None = None,
    token: str | None = PAT,
) -> tuple[int, str, str]:
    full_argv = list(argv)
    if base_url is not None:
        full_argv = ["--base-url", base_url, *full_argv]
    if token is not None:
        full_argv = ["--token", token, *full_argv]
    out, err = StringIO(), StringIO()
    code = cli.main(full_argv, stdout=out, stderr=err)
    return code, out.getvalue(), err.getvalue()


# --- repo -----------------------------------------------------------------------


def test_repo_success_json_stdout_pat_hidden(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    server = FakeGitHubServer(_repo_fixture())
    server.start()
    try:
        code, out, err = _run_cli(["repo", "acme", "web"], base_url=server.base_url)
    finally:
        server.stop()
    assert code == 0
    payload = json.loads(out)
    assert payload["full_name"] == "acme/web"
    assert payload["url"] == "https://github.com/acme/web.git"
    assert payload["default_branch"] == "main"
    assert "token" not in json.dumps(payload)  # no token material in the JSON
    assert "github repo ok" in err  # human summary on stderr
    assert PAT not in out and PAT not in err


def test_repo_404_error_json_exit_1(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    server = FakeGitHubServer(_repo_fixture(status=404, body={"message": "Not Found"}))
    server.start()
    try:
        code, out, err = _run_cli(["repo", "acme", "web"], base_url=server.base_url)
    finally:
        server.stop()
    assert code == 1
    payload = json.loads(out)
    assert payload["error"] == "not_found"
    assert payload["status"] == 404
    assert PAT not in out and PAT not in err


def test_repo_401_maps_to_auth_error_exit_1(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    server = FakeGitHubServer(_repo_fixture(status=401, body={"message": "Bad credentials"}))
    server.start()
    try:
        code, out, err = _run_cli(["repo", "acme", "web"], base_url=server.base_url)
    finally:
        server.stop()
    assert code == 1
    payload = json.loads(out)
    assert payload["error"] == "auth"
    assert payload["status"] == 401


# --- pr-files --------------------------------------------------------------------


def test_pr_files_success_files_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    server = FakeGitHubServer(_pr_fixture())
    server.start()
    try:
        code, out, err = _run_cli(["pr-files", "acme", "web", "9"], base_url=server.base_url)
    finally:
        server.stop()
    assert code == 0
    payload = json.loads(out)
    assert payload["number"] == 9
    assert payload["head_sha"] == "h" * 40
    assert payload["base_sha"] == "b" * 40
    assert payload["files"] == ["src/cart.ts"]  # de-duplicated, S6.1 shape
    assert PAT not in out and PAT not in err


# --- golden subcommand -----------------------------------------------------------


def test_golden_subcommand_passes_and_writes_report(tmp_path: Path) -> None:
    report_path = tmp_path / "report.json"
    out, err = StringIO(), StringIO()
    code = cli.main(["golden", "--report", str(report_path)], stdout=out, stderr=err)
    assert code == 0
    payload = json.loads(out.getvalue())
    assert payload["golden_name"] == "github_client"
    assert payload["fixtures"] == 10
    assert payload["gate_passed"] is True
    assert "PASSED" in err.getvalue()  # human summary on stderr
    assert PAT not in out.getvalue() and PAT not in err.getvalue()
    # --report writes the same JSON document to disk
    written = report_path.read_text(encoding="utf-8")
    assert json.loads(written) == payload


def test_golden_missing_file_exit_2() -> None:
    out, err = StringIO(), StringIO()
    code = cli.main(
        ["golden", "--golden", str(Path.cwd() / "definitely_missing_golden.json")],
        stdout=out,
        stderr=err,
    )
    assert code == 2
    assert "golden load error" in err.getvalue()
    assert out.getvalue() == ""  # no JSON on a load failure


# --- usage errors -----------------------------------------------------------------


def test_repo_missing_required_arg_exits_2() -> None:
    out, err = StringIO(), StringIO()
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["repo", "acme"], stdout=out, stderr=err)
    assert excinfo.value.code == 2
    assert out.getvalue() == ""

