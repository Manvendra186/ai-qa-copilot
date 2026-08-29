"""Playwright execution worker (build bible §15, §31.11; S3.1).

Runs a Playwright suite inside the target repository (subprocess) and
captures the §15 artifact set — trace / screenshot / video / console /
network / dom / log — into an :class:`~qa_copilot_execution.store.ArtifactStore`
under the §31.11 layout ``runs/{run_id}/{test_id}/{name}``.

The worker is database-free: it produces a
:class:`~qa_copilot_execution.report.RunReport`;
:func:`qa_copilot_repository.runs.persist_run` maps that report onto the §10
``test_runs`` / ``test_results`` / ``artifacts`` rows.

Run status semantics (S3.1 enums, §31.2 state machine): a run is
``completed`` when Playwright produced a JSON report — even if tests failed
(test outcomes are per-test data); it is ``failed`` when the worker itself
could not get a report (spawn error, timeout, no JSON on stdout).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from qa_copilot_domain.enums import ArtifactType, RunStatus, TestResultStatus

from .report import ArtifactReport, RunReport, RunTotals, TestResultReport
from .store import ArtifactStore, ArtifactStoreError, check_segment

DEFAULT_TIMEOUT_S = 6000.0
#: Default worker command (kept for API compatibility; resolution now
#: happens in :func:`_resolve_command` so Windows picks the bin shim).
DEFAULT_COMMAND = ("playwright", "test", "--reporter=json")

#: Playwright spec/result status (JSON reporter, both schemas) → domain
#: vocabulary (S3.1 enums). Old reporters carried ``expected``/``unexpected``
#: on the spec; ≥1.5x (verified 1.62) carry ``passed``/``failed`` on results
#: plus an ``ok`` bool on the spec, so both vocabularies map here.
_SPEC_STATUS: dict[str, TestResultStatus] = {
    "expected": TestResultStatus.PASSED,
    "passed": TestResultStatus.PASSED,
    "unexpected": TestResultStatus.FAILED,
    "failed": TestResultStatus.FAILED,
    "flaky": TestResultStatus.FLAKY,
    "skipped": TestResultStatus.SKIPPED,
}

#: Playwright attachment names → artifact kind (build bible §15).
_NAME_TO_TYPE: dict[str, ArtifactType] = {
    "trace": ArtifactType.TRACE,
    "video": ArtifactType.VIDEO,
    "screenshot": ArtifactType.SCREENSHOT,
    "console.jsonl": ArtifactType.CONSOLE,
    "network.jsonl": ArtifactType.NETWORK,
    "error context": ArtifactType.DOM,
}

#: File extension → artifact kind (fallback when the name is non-canonical).
_EXT_TO_TYPE: dict[str, ArtifactType] = {
    ".zip": ArtifactType.TRACE,
    ".webm": ArtifactType.VIDEO,
    ".png": ArtifactType.SCREENSHOT,
    ".jpg": ArtifactType.SCREENSHOT,
    ".md": ArtifactType.DOM,
    ".log": ArtifactType.LOG,
    ".txt": ArtifactType.LOG,
}

#: Known files a target app may drop in the per-test output dir (§15 console/
#: network capture, §16 failure context) instead of attaching them.
_FALLBACK_FILES: dict[str, ArtifactType] = {
    "console.jsonl": ArtifactType.CONSOLE,
    "network.jsonl": ArtifactType.NETWORK,
    "error-context.md": ArtifactType.DOM,
}

_TAIL_CHARS = 2000


@dataclass(frozen=True, slots=True)
class PlaywrightConfig:
    """Worker settings — every field injectable so unit tests stay hermetic."""

    target_dir: Path
    store_root: Path | None = None
    """Default: ``<cwd>/data/artifacts`` (gitignored in the main repo)."""
    command: tuple[str, ...] | None = None
    """Default: ``playwright test --reporter=json`` in the target repo."""
    timeout_s: float = DEFAULT_TIMEOUT_S
    test_filter: str | None = None
    """Playwright test filter (passed as a positional argument)."""
    extra_env: dict[str, str] = field(default_factory=dict)
    """Extra environment (e.g. ``APP_UNDER_TEST``), merged over os.environ."""


def _slugify(value: str) -> str:
    """Playwright's output-dir slug (lowercase, non-alphanumeric → ``-``)."""
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def _now_iso(dt: datetime) -> str:
    return dt.isoformat(timespec="milliseconds")


def _decoded(value: bytes | str | None) -> str:
    if value is None:
        return ""
    return value if isinstance(value, str) else value.decode("utf-8", "replace")


def _tail(text: str) -> str | None:
    text = text.strip()
    return text[-_TAIL_CHARS:] if text else None


def _playwright_cli(target: Path) -> str:
    """Resolve the target repo's Playwright CLI executable.

    Prefer the workspace bin shim so the target's pinned Playwright runs
    (pinned browser, §31.11). On Windows the shim name must be explicit —
    a bare ``playwright`` is unreliable because ``CreateProcess`` searches
    extensions *first* across the whole PATH, so a ``playwright.exe`` from
    a Python install in a later PATH entry shadows the ``.cmd`` shim
    (observed in the S3.1 live run: ``error: unknown command 'test'``).
    """
    node_bin = target / "node_modules" / ".bin"
    names = ("playwright.cmd", "playwright.bat") if os.name == "nt" else ("playwright",)
    for name in names:
        shim = node_bin / name
        if shim.is_file():
            return str(shim)
    return "playwright"


def _resolve_command(config: PlaywrightConfig, target: Path) -> list[str]:
    """Build the argv. Explicit ``command`` wins; otherwise target's CLI."""
    if config.command is not None:
        command = list(config.command)
    else:
        command = [_playwright_cli(target), "test"]
    if config.test_filter is not None:
        command.append(config.test_filter)
    if config.command is None:
        command.append("--reporter=json")
    return command


def _extract_json(stdout: str) -> object | None:
    """Parse the JSON reporter output, tolerating leading log noise."""
    try:
        data: object = json.loads(stdout)
        return data
    except json.JSONDecodeError:
        pass
    lines = stdout.splitlines()
    for idx, line in enumerate(lines):
        if line.lstrip().startswith(("{", "[")):
            try:
                data = json.loads("\n".join(lines[idx:]))
                return data
            except json.JSONDecodeError:
                continue
    return None


def _as_dict(value: object) -> dict[str, object] | None:
    return value if isinstance(value, dict) else None


def _load_report_file(path: Path) -> object | None:
    """Read the Playwright JSON report file (``PLAYWRIGHT_JSON_OUTPUT_FILE``)."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    try:
        data: object = json.loads(text)
        return data
    except json.JSONDecodeError:
        return None


def _as_list(value: object) -> list[object]:
    return value if isinstance(value, list) else []


def _suites(report: object) -> list[dict[str, object]]:
    """Top-level suites, both JSON shapes: object (``suites``) and array."""
    if isinstance(report, list):
        return [s for s in report if isinstance(s, dict)]
    if isinstance(report, dict):
        return [s for s in _as_list(report.get("suites")) if isinstance(s, dict)]
    return []


def _iter_specs(
    suites: list[dict[str, object]],
) -> list[tuple[str, dict[str, object], list[str]]]:
    """Flatten nested suites into ``(file, spec, title_chain)`` triples.

    *title_chain* mirrors Playwright's per-test output-dir naming: the file
    stem followed by every ``describe`` title (verified against 1.62:
    ``demo`` + ``login + products`` + spec title →
    ``demo-login-products-signs-in-and-sees-the-product-catalog``).
    """
    entries: list[tuple[str, dict[str, object], list[str]]] = []

    def walk(suite: dict[str, object], file_path: str, chain: list[str]) -> None:
        file_name = str(suite.get("file") or file_path or suite.get("title") or "")
        title = str(suite.get("title") or "")
        if not file_path:
            # Playwright strips *every* extension for the output-dir name:
            # ``demo.spec.js`` → ``demo`` (verified against 1.62).
            stem = file_name.split(".")[0] or title
            chain = [stem] if stem else []
        elif title:
            chain = [*chain, title]
        for spec in _as_list(suite.get("specs")):
            if isinstance(spec, dict):
                entries.append((file_name, spec, list(chain)))
        for child in _as_list(suite.get("suites")):
            if isinstance(child, dict):
                walk(child, file_name, chain)

    for suite in suites:
        walk(suite, "", [])
    return entries


def _last_result(spec: dict[str, object]) -> tuple[dict[str, object] | None, int]:
    """Across all project tests: (last attempt result, summed duration ms)."""
    last: dict[str, object] | None = None
    total_ms = 0
    for test in _as_list(spec.get("tests")):
        test_dict = _as_dict(test)
        if test_dict is None:
            continue
        for result in _as_list(test_dict.get("results")):
            result_dict = _as_dict(result)
            if result_dict is None:
                continue
            raw = result_dict.get("duration")
            total_ms += int(raw) if isinstance(raw, (int, float)) else 0
            last = result_dict
    return last, total_ms


def _error_text(result: dict[str, object] | None) -> str | None:
    """Raw failure text (message + snippet per error), if any."""
    if result is None:
        return None
    parts: list[str] = []
    for err in _as_list(result.get("errors")):
        err_dict = _as_dict(err)
        if err_dict is None:
            continue
        message = str(err_dict.get("message") or "").strip()
        snippet = str(err_dict.get("snippet") or "").strip()
        parts.extend(p for p in (message, snippet) if p)
    text = "\n\n".join(parts).strip()
    return text or None


def _spec_status(spec: dict[str, object]) -> TestResultStatus:
    """Domain status for a spec, across Playwright JSON reporter schemas.

    Old schema: ``spec.status`` ∈ expected/unexpected/flaky/skipped.
    New schema (≥1.5x, verified 1.62): per-result ``status`` ∈
    passed/failed/skipped plus a spec-level ``ok`` bool. A flaky test is a
    retry sequence whose first attempt failed and last passed.
    """
    raw = spec.get("status")
    if isinstance(raw, str) and raw in _SPEC_STATUS:
        return _SPEC_STATUS[raw]

    statuses: list[str] = []
    for test in _as_list(spec.get("tests")):
        test_dict = _as_dict(test)
        if test_dict is None:
            continue
        for result in _as_list(test_dict.get("results")):
            result_dict = _as_dict(result)
            if result_dict is None:
                continue
            status = str(result_dict.get("status") or "")
            if status:
                statuses.append(status)

    if not statuses:
        ok = spec.get("ok")
        if isinstance(ok, bool):
            return TestResultStatus.PASSED if ok else TestResultStatus.FAILED
        return TestResultStatus.FAILED
    if (
        len(statuses) > 1
        and statuses[0] in ("unexpected", "failed")
        and statuses[-1] in ("expected", "passed")
    ):
        return TestResultStatus.FLAKY
    return _SPEC_STATUS.get(statuses[-1], TestResultStatus.FAILED)


def _classify(name: str, path: str) -> ArtifactType | None:
    """Map an attachment (name + path) to an artifact kind, or None."""
    key = name.strip().lower()
    if key in _NAME_TO_TYPE:
        return _NAME_TO_TYPE[key]
    suffix = (Path(path) if path else Path(name)).suffix.lower()
    if suffix == ".jsonl":
        if "console" in key:
            return ArtifactType.CONSOLE
        if "network" in key:
            return ArtifactType.NETWORK
        return ArtifactType.LOG
    return _EXT_TO_TYPE.get(suffix)


def _config_info(report: object) -> tuple[str | None, str | None, str]:
    """(base_url, browser, output_dir) from the JSON config (best effort)."""
    base_url: str | None = None
    browser: str | None = None
    output_dir = "test-results"
    if isinstance(report, dict):
        cfg = _as_dict(report.get("config"))
        if cfg is not None:
            raw_url = cfg.get("baseURL")
            if isinstance(raw_url, str):
                base_url = raw_url
            raw_out = cfg.get("outputDir")
            if isinstance(raw_out, str):
                output_dir = raw_out
            projects = _as_list(cfg.get("projects"))
            first = _as_dict(projects[0]) if projects else None
            use = _as_dict(first.get("use")) if first is not None else None
            raw_browser = use.get("browserName") if use is not None else None
            if isinstance(raw_browser, str):
                browser = raw_browser
    return base_url, browser, output_dir


def _commit_sha(target: Path) -> str | None:
    """The target repo's commit SHA (recorded on the run, build bible §15)."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(target), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    sha = (proc.stdout or "").strip()
    return sha or None


def _fallback_files(target: Path, output_dir: str, slug: str) -> list[tuple[ArtifactType, Path]]:
    """Known §15 files in the per-test output dir, attributed by slug.

    Playwright lays out ``{output_dir}/{test_slug}/…`` — the slug is the
    first path segment (containment kept as a safety net).
    """
    base = target / output_dir
    found: list[tuple[ArtifactType, Path]] = []
    if not base.is_dir():
        return found
    for path in sorted(base.rglob("*")):
        if not path.is_file():
            continue
        kind = _FALLBACK_FILES.get(path.name)
        if kind is None:
            continue
        rel = path.relative_to(base)
        if len(rel.parts) >= 2 and (rel.parts[0] == slug or slug in rel.parts[0]):
            found.append((kind, path))
    return found


def _totals(results: list[TestResultReport]) -> RunTotals:
    return RunTotals(
        total=len(results),
        passed=sum(r.status is TestResultStatus.PASSED for r in results),
        failed=sum(r.status is TestResultStatus.FAILED for r in results),
        flaky=sum(r.status is TestResultStatus.FLAKY for r in results),
        skipped=sum(r.status is TestResultStatus.SKIPPED for r in results),
    )


def _build_result(
    store: ArtifactStore,
    run_id: str,
    target: Path,
    output_dir: str,
    file_name: str,
    chain: list[str],
    spec: dict[str, object],
) -> TestResultReport:
    """One spec → TestResultReport, storing its §15 artifacts in *store*."""
    result_status = _spec_status(spec)
    last_result, duration_ms = _last_result(spec)
    error = _error_text(last_result)
    title = str(spec.get("title") or "untitled")
    # Reproduce Playwright's per-test output-dir slug (file stem + describe
    # chain + title) so the store path and the on-disk lookup agree.
    slug = _slugify(" ".join([*chain, title])) or "test"
    check_segment(slug, "test slug")

    used_names: set[str] = set()

    def unique_name(kind: ArtifactType) -> str:
        name = kind.value
        n = 2
        while name in used_names:
            name = f"{kind.value}-{n}"
            n += 1
        used_names.add(name)
        return name

    def store_file(kind: ArtifactType, source: Path) -> ArtifactReport | None:
        try:
            uri, size = store.store(run_id, slug, unique_name(kind), source)
        except ArtifactStoreError:
            return None
        return ArtifactReport(
            type=kind, uri=uri, metadata={"size_bytes": size, "source": source.name}
        )

    artifacts: list[ArtifactReport] = []
    attachments = last_result.get("attachments") if last_result is not None else None
    for att in _as_list(attachments):
        att_dict = _as_dict(att)
        if att_dict is None:
            continue
        kind = _classify(str(att_dict.get("name") or ""), str(att_dict.get("path") or ""))
        if kind is None:
            continue
        source = target / str(att_dict.get("path") or "")
        if not source.is_file():
            continue
        stored = store_file(kind, source)
        if stored is not None:
            artifacts.append(stored)

    collected = {a.type for a in artifacts}
    for kind, source in _fallback_files(target, output_dir, slug):
        if kind in collected:
            continue
        stored = store_file(kind, source)
        if stored is not None:
            artifacts.append(stored)
            collected.add(kind)

    if error is not None:
        raw_errors = last_result.get("errors") if last_result is not None else None
        content = json.dumps(raw_errors, indent=2, ensure_ascii=False) if raw_errors else error
        try:
            uri, size = store.store_text(run_id, slug, "log", content)
        except ArtifactStoreError:
            pass
        else:
            artifacts.append(
                ArtifactReport(type=ArtifactType.LOG, uri=uri, metadata={"size_bytes": size})
            )

    return TestResultReport(
        title=title,
        file=file_name or None,
        status=result_status,
        duration_ms=duration_ms,
        error=error,
        slug=slug,
        artifacts=artifacts,
    )


def run_playwright(config: PlaywrightConfig, run_id: str) -> RunReport:
    """Run the target's Playwright suite, capture artifacts, return a report."""
    check_segment(run_id, "run_id")
    target = Path(config.target_dir).resolve()
    store_root = (
        Path(config.store_root)
        if config.store_root is not None
        else Path.cwd() / "data" / "artifacts"
    )
    store = ArtifactStore(store_root)
    started_dt = datetime.now(UTC)

    command = _resolve_command(config, target)

    # The Playwright JSON reporter writes its report to a *file*, not stdout
    # (Playwright ≥1.5x: without ``PLAYWRIGHT_JSON_OUTPUT_FILE`` it emits
    # nothing — verified against 1.62). Point it at a path we control and
    # read it after the run; stdout parsing is kept as a fallback for
    # injected/custom commands that print the report directly.
    workdir = Path(tempfile.mkdtemp(prefix="qa-copilot-exec-"))
    json_path = workdir / "playwright-report.json"
    try:
        env = dict(os.environ)
        bin_dir = target / "node_modules" / ".bin"
        if bin_dir.is_dir():
            env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
        env["PLAYWRIGHT_JSON_OUTPUT_FILE"] = str(json_path)
        env.update(config.extra_env)

        exit_code: int | None = None
        stdout = ""
        stderr = ""
        timed_out = False
        spawn_error: str | None = None
        try:
            proc = subprocess.run(
                command,
                cwd=target,
                env=env,
                capture_output=True,
                text=True,
                timeout=config.timeout_s,
            )
            exit_code = proc.returncode
            stdout = proc.stdout or ""
            stderr = proc.stderr or ""
        except FileNotFoundError as exc:
            spawn_error = f"playwright executable not found: {exc}"
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            stdout = _decoded(exc.stdout)
            stderr = _decoded(exc.stderr)

        results: list[TestResultReport] = []
        run_error: str | None = None
        base_url: str | None = None
        browser: str | None = None
        output_dir = "test-results"

        report_json = _load_report_file(json_path)
        if report_json is None:
            report_json = _extract_json(stdout) if stdout else None
        if report_json is None:
            if timed_out:
                run_error = f"playwright timed out after {config.timeout_s:g}s"
            elif spawn_error is not None:
                run_error = spawn_error
            else:
                run_error = f"playwright exited with code {exit_code} without a JSON report"
                detail = (stderr or stdout).strip()[-400:]
                if detail:
                    run_error = f"{run_error}: {detail}"
            status = RunStatus.FAILED
        else:
            status = RunStatus.COMPLETED
            base_url, browser, output_dir = _config_info(report_json)
            for file_name, spec, chain in _iter_specs(_suites(report_json)):
                results.append(
                    _build_result(
                        store,
                        run_id,
                        target,
                        output_dir,
                        file_name,
                        chain,
                        spec,
                    )
                )

        finished_dt = datetime.now(UTC)
        return RunReport(
            status=status,
            target_dir=str(target),
            base_url=base_url,
            commit_sha=_commit_sha(target),
            browser=browser,
            started_at=_now_iso(started_dt),
            completed_at=_now_iso(finished_dt),
            duration_ms=max(0, round((finished_dt - started_dt).total_seconds() * 1000)),
            totals=_totals(results),
            error=run_error,
            stdout_tail=_tail(stdout),
            stderr_tail=_tail(stderr),
            results=results,
        )
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
