"""Change-impact analysis core (build bible §7, §19 S6.1).

Deterministic, LLM-free mapping from a set of changed files (an explicit
list or a ``git diff`` range) to the test files that should be re-run. This
is the regression-intelligence anchor the S6.3 recommendation (LLM) ranks on
top of, and the S6.4 API serves it as JSON (build bible §19 S6.1–S6.4).

Impact kinds (a test file can carry several in the same set):

- ``direct`` — the changed file is itself a test file (S2.1 heuristic,
  :func:`qa_copilot_repository.scanner.is_test_file`);
- ``generated`` — the changed file is an *applied* generated test
  (``generated_tests.file_path``, S2.4); its ``test_case_id`` and the linked
  requirement ids (``requirement_test_cases`` join, §10) ride along so the
  S6.3 recommendation can rank by ``requirements.risk`` /
  ``test_cases.priority``;
- ``referenced`` — a test file in the repo imports/requires a changed
  source file, or uses one of the ``data-testid`` values defined in a
  changed file (the S2.2 ``data-testid`` vocabulary).

The core (:func:`compute_impact`) is pure: repo checkout + changed paths +
generated-test provenance refs — no DB, no LLM, no network. The thin DB
adapter (:func:`applied_generated_refs`) reads a project's ``generated_tests``
rows so the S6.4 endpoint calls :func:`impact_from_session` in one line.

Determinism: equal inputs always produce equal output — ``impacted`` is
sorted by test-file path and every list is sorted and deduped; the only
wall-clock value is ``computed_at`` (golden tests drop it before comparing).

Safety: same walk rules as the S2.1 scanner (pruned dirs, no symlinks,
capped file count); only test files and changed files are read, and only up
to :data:`qa_copilot_repository.scanner.MAX_READ_BYTES`.

CLI::

    python -m qa_copilot_repository.impact <repo-root> --changed a.py,b.ts
    python -m qa_copilot_repository.impact <repo-root> --range BASE..HEAD
"""

from __future__ import annotations

import argparse
import json
import os
import posixpath
import re
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from qa_copilot_domain import ImpactedTest, ImpactKind, ImpactSet
from qa_copilot_domain.enums import GeneratedTestStatus
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import models
from .scanner import MAX_FILES, MAX_READ_BYTES, SKIP_DIRS, is_test_file, read_text_capped

__all__ = [
    "GeneratedTestRef",
    "applied_generated_refs",
    "build_parser",
    "changed_files_from_range",
    "compute_impact",
    "impact_from_session",
    "main",
    "normalize_changed",
]

#: JS/TS module file extensions (import specifiers commonly omit the ext).
_JS_MODULE_EXTS: tuple[str, ...] = (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs")

#: Relative JS/TS import specifiers: ``import x from "…"`` (and
#: ``export … from``), side-effect ``import "…"``, dynamic ``import("…"``)
#: and CommonJS ``require("…")``.
_JS_SPEC_RE = re.compile(
    r"(?:\bfrom\s+|\bimport\s+|\bimport\s*\(\s*|\brequire\s*\(\s*)['\"]([^'\"]+)['\"]"
)

#: Python module imports, line-anchored: ``from x.y import z`` / ``import x.y``.
_PY_FROM_RE = re.compile(r"(?m)^\s*from\s+([A-Za-z_][A-Za-z0-9_.]*)\s+import\b")
_PY_IMPORT_RE = re.compile(
    r"(?m)^\s*import\s+([A-Za-z_][A-Za-z0-9_.]*(?:\s*,\s*[A-Za-z_][A-Za-z0-9_.]*)*)"
)

#: The S2.2 ``data-testid`` vocabulary — the static attribute form.
_TESTID_DEF_RE = re.compile(r"data-testid\s*=\s*[\"']([A-Za-z0-9][A-Za-z0-9_-]*)[\"']")
#: Playwright locator usage in tests: ``page.getByTestId('…')``.
_TESTID_USE_RE = re.compile(r"getByTestId\s*\(\s*[\"']([A-Za-z0-9][A-Za-z0-9_-]*)[\"']")


@dataclass(frozen=True)
class GeneratedTestRef:
    """Provenance of one *applied* generated test (S2.4 row → S6.1 input).

    ``file_path`` is the repo-relative path the test was applied to
    (``generated_tests.file_path``); ``test_case_id`` and ``requirement_ids``
    are the S1.2 links (``requirement_test_cases`` join, §10) the impact set
    surfaces for the S6.3 regression recommendation.
    """

    file_path: str
    test_case_id: str | None = None
    requirement_ids: tuple[str, ...] = ()


def normalize_changed(raw: str) -> str:
    """Normalize a changed-file path to repo-relative POSIX form.

    Accepts ``./``-prefixed and backslash paths; rejects absolute paths and
    ``..`` segments — impact analysis must never read or report outside the
    repository checkout. Raises :class:`ValueError` on invalid input.
    """
    cleaned = raw.strip().replace("\\", "/")
    if not cleaned:
        raise ValueError("changed-file path is empty")
    if cleaned.startswith("/") or re.fullmatch(r"[A-Za-z]:[/\\].*", cleaned):
        raise ValueError(f"changed-file path must be repo-relative: {raw!r}")
    parts = [part for part in cleaned.split("/") if part not in ("", ".")]
    if not parts:
        raise ValueError(f"changed-file path is empty: {raw!r}")
    if ".." in parts:
        raise ValueError(f"changed-file path escapes the repository: {raw!r}")
    return "/".join(parts)


def _js_targets(base: str) -> frozenset[str]:
    """Candidate repo-relative paths a specifier or changed file may refer to.

    ``src/components/Counter`` matches ``Counter.tsx`` / ``Counter.ts`` /
    ``Counter/index.*``; ``e2e/fixtures.js`` also matches its
    extension-less stem (specifiers commonly omit the extension).
    """
    targets = {base, f"{base}/index"}
    for ext in _JS_MODULE_EXTS:
        if base.endswith(ext):
            targets.add(base[: -len(ext)])
        else:
            targets.add(base + ext)
    return frozenset(targets)


def _py_module_files(modules: set[str]) -> set[str]:
    """File suffixes a Python module name may live at (under any src root):

    ``app.main`` → ``app/main.py`` or ``app/main/__init__.py``.
    """
    files: set[str] = set()
    for module in modules:
        module = module.strip()
        if module:
            dotted = module.replace(".", "/")
            files.add(f"{dotted}.py")
            files.add(f"{dotted}/__init__.py")
    return files


def _py_matches(changed_file: str, module_files: set[str]) -> bool:
    """True when *changed_file* is one of the module's possible locations."""
    return any(changed_file == m or changed_file.endswith("/" + m) for m in module_files)


def _collect_test_files(root: Path, notes: list[str]) -> list[str]:
    """Repo-relative POSIX paths of every test file (S2.1 heuristic).

    Walks like the S2.1 scanner: pruned dirs, never follows symlinks, capped
    file count.
    """
    test_files: list[str] = []
    visited = 0
    capped = False
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith("."))
        for filename in sorted(filenames):
            visited += 1
            if visited > MAX_FILES:
                capped = True
                break
            if is_test_file(filename, os.path.basename(dirpath)):
                test_files.append(Path(dirpath, filename).relative_to(root).as_posix())
        if capped:
            break
    if capped:
        notes.append(
            f"file walk capped at {MAX_FILES} files; test files beyond the cap were not scanned"
        )
    return test_files


@dataclass
class _Accumulator:
    """Per-test-file accumulation of impact evidence (deduped by set)."""

    kinds: set[ImpactKind] = field(default_factory=set)
    changed_files: set[str] = field(default_factory=set)
    test_case_ids: set[str] = field(default_factory=set)
    requirement_ids: set[str] = field(default_factory=set)
    signals: set[str] = field(default_factory=set)


def compute_impact(
    root: str | Path,
    changed: Sequence[str],
    *,
    generated: Sequence[GeneratedTestRef] = (),
) -> ImpactSet:
    """Compute the deterministic change-impact set for *changed* files (S6.1).

    Args:
        root: the repository checkout scanned for tests that reference the
            changed files.
        changed: repo-relative changed file paths (a diff).
        generated: provenance of the project's *applied* generated tests —
            typically :func:`applied_generated_refs`; a ref contributes the
            ``generated`` kind plus its ``test_case_id`` / requirement ids
            when its ``file_path`` is in *changed*.

    Returns:
        A :class:`qa_copilot_domain.ImpactSet` — ``impacted`` sorted by test
        file path, every list sorted and deduped (see module docstring).

    Raises:
        ValueError: when *root* is missing, or a changed path is absolute or
            escapes the repository.
    """
    repo_root = Path(root)
    if not repo_root.is_dir():
        raise ValueError(f"repository root is not a directory: {repo_root}")

    changed_set = sorted({normalize_changed(p) for p in changed})
    for ref in generated:
        normalize_changed(ref.file_path)  # validate; ValueError when invalid

    notes: list[str] = []
    test_files = _collect_test_files(repo_root, notes)
    if not test_files:
        notes.append("no test files detected in the repository (S2.1 heuristics)")

    by_test: dict[str, _Accumulator] = {}

    def add(test_file: str, kind: ImpactKind, changed_file: str, signal: str) -> _Accumulator:
        acc = by_test.setdefault(test_file, _Accumulator())
        acc.kinds.add(kind)
        acc.changed_files.add(changed_file)
        acc.signals.add(signal)
        return acc

    # --- direct: changed files that are test files themselves ----------------
    for changed_file in changed_set:
        parts = changed_file.split("/")
        parent_dirname = parts[-2] if len(parts) >= 2 else ""
        if is_test_file(parts[-1], parent_dirname):
            add(changed_file, ImpactKind.DIRECT, changed_file, "changed test file")

    # --- generated: applied generated tests inside the diff ------------------
    for ref in generated:
        ref_path = normalize_changed(ref.file_path)
        if ref_path not in changed_set:
            continue
        if ref.test_case_id:
            signal = f"generated test applied (test case {ref.test_case_id})"
        else:
            signal = "generated test applied (no test-case link)"
        acc = add(ref_path, ImpactKind.GENERATED, ref_path, signal)
        if ref.test_case_id:
            acc.test_case_ids.add(ref.test_case_id)
        acc.requirement_ids.update(ref.requirement_ids)

    # --- referenced: test files that import / use changed source -------------
    js_targets_by_file: dict[str, frozenset[str]] = {}
    py_changed_files: list[str] = []
    testids_by_file: dict[str, set[str]] = {}
    for changed_file in changed_set:
        target = repo_root / changed_file
        if not target.is_file():
            notes.append(
                f"changed file not present at repo root (deleted or moved): {changed_file}"
            )
            continue
        if changed_file.lower().endswith(_JS_MODULE_EXTS):
            js_targets_by_file[changed_file] = _js_targets(changed_file)
        if changed_file.endswith(".py"):
            py_changed_files.append(changed_file)
        text = read_text_capped(target)
        if text is not None:
            test_ids = set(_TESTID_DEF_RE.findall(text))
            if test_ids:
                testids_by_file[changed_file] = test_ids

    for test_file in test_files:
        text = read_text_capped(repo_root / test_file)
        if text is None:
            notes.append(
                f"test file skipped (unreadable or over {MAX_READ_BYTES} bytes): {test_file}"
            )
            continue

        # JS/TS: resolve relative import specifiers against the changed set.
        test_dir = posixpath.dirname(test_file)
        resolved: set[str] = set()
        for spec in _JS_SPEC_RE.findall(text):
            if spec.startswith(("./", "../")):
                resolved |= _js_targets(posixpath.normpath(posixpath.join(test_dir, spec)))
        for changed_file, targets in js_targets_by_file.items():
            if resolved & targets:
                add(test_file, ImpactKind.REFERENCED, changed_file, f"imports {changed_file}")

        # Python: match `from x.y import …` / `import x.y` against changed files.
        modules = set(_PY_FROM_RE.findall(text))
        for clause in _PY_IMPORT_RE.findall(text):
            modules.update(part.strip() for part in clause.split(","))
        module_files = _py_module_files(modules)
        if module_files:
            for changed_file in py_changed_files:
                if _py_matches(changed_file, module_files):
                    add(test_file, ImpactKind.REFERENCED, changed_file, f"imports {changed_file}")

        # data-testid: usage in the test vs values defined in the changed file.
        used_ids = set(_TESTID_USE_RE.findall(text)) | set(_TESTID_DEF_RE.findall(text))
        for changed_file, test_ids in testids_by_file.items():
            for test_id in sorted(used_ids & test_ids):
                add(
                    test_file,
                    ImpactKind.REFERENCED,
                    changed_file,
                    f"uses data-testid '{test_id}' from {changed_file}",
                )

    impacted = [
        ImpactedTest(
            path=path,
            kinds=sorted(acc.kinds),
            changed_files=sorted(acc.changed_files),
            test_case_ids=sorted(acc.test_case_ids),
            requirement_ids=sorted(acc.requirement_ids),
            signals=sorted(acc.signals),
        )
        for path, acc in sorted(by_test.items())
    ]
    return ImpactSet(
        changed=changed_set,
        impacted=impacted,
        test_files_scanned=len(test_files),
        notes=notes,
        computed_at=datetime.now(UTC),
    )


def changed_files_from_range(root: str | Path, base: str, head: str) -> list[str]:
    """Changed files between two git refs — ``BASE..HEAD`` (S6.1 input).

    Runs ``git diff --name-only BASE..HEAD`` inside *root*; returns the
    sorted repo-relative POSIX paths. Raises :class:`ValueError` when the
    refs are empty, git is unavailable, or the diff fails (bad refs, not a
    repository).
    """
    base = base.strip()
    head = head.strip()
    if not base or not head:
        raise ValueError("range must be BASE..HEAD with both refs non-empty")
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "diff", "--name-only", f"{base}..{head}"],
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        stderr = getattr(exc, "stderr", None)
        detail = stderr.strip() if isinstance(stderr, str) and stderr.strip() else str(exc)
        raise ValueError(f"git diff BASE..HEAD failed: {detail}") from exc
    return sorted(line.strip() for line in proc.stdout.splitlines() if line.strip())


def applied_generated_refs(session: Session, project_id: str) -> list[GeneratedTestRef]:
    """Provenance refs for a project's *applied* generated tests (S2.4 → S6.1).

    ``generated_tests`` rows with ``status = applied`` (the file actually
    written into the workspace, §19 S2.4), each resolved to its
    ``test_case_id`` and the requirement ids linked through the
    ``requirement_test_cases`` join (§10) — the S6.3 recommendation ranks
    regressions on those links' risk/priority. Sorted by file path for
    deterministic input to :func:`compute_impact`.
    """
    rows = session.scalars(
        select(models.GeneratedTest).where(
            models.GeneratedTest.project_id == project_id,
            models.GeneratedTest.status == GeneratedTestStatus.APPLIED,
        )
    ).all()
    refs: list[GeneratedTestRef] = []
    for row in rows:
        test_case = row.test_case
        requirement_ids = tuple(
            sorted(r.id for r in (test_case.requirements if test_case else ()) if r.id)
        )
        refs.append(
            GeneratedTestRef(
                file_path=row.file_path,
                test_case_id=row.test_case_id,
                requirement_ids=requirement_ids,
            )
        )
    return sorted(refs, key=lambda ref: (ref.file_path, ref.test_case_id or ""))


def impact_from_session(
    session: Session,
    project_id: str,
    root: str | Path,
    changed: Sequence[str],
) -> ImpactSet:
    """S6.1 impact for a project: the pure core plus its generated-test provenance."""
    return compute_impact(root, changed, generated=applied_generated_refs(session, project_id))


def build_parser() -> argparse.ArgumentParser:
    """CLI parser for ``python -m qa_copilot_repository.impact``."""
    parser = argparse.ArgumentParser(
        prog="qa_copilot_repository.impact",
        description="Deterministic change-impact set (build bible §19 S6.1, LLM-free) as JSON.",
    )
    parser.add_argument("root", type=Path, help="repository checkout to scan for referencing tests")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--changed",
        metavar="PATH[,PATH...]",
        help="comma-separated repo-relative changed file paths",
    )
    source.add_argument(
        "--range",
        dest="git_range",
        metavar="BASE..HEAD",
        help="changed files from `git diff --name-only BASE..HEAD`",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the impact CLI; the JSON impact set goes to stdout."""
    args = build_parser().parse_args(argv)
    try:
        if args.changed is not None:
            changed = [p for p in args.changed.split(",") if p.strip()]
            if not changed:
                raise ValueError("--changed needs at least one path")
        else:
            base, sep, head = (args.git_range or "").partition("..")
            if not sep:
                raise ValueError("--range must be BASE..HEAD")
            changed = changed_files_from_range(args.root, base, head)
        result = compute_impact(args.root, changed)
    except ValueError as exc:
        print(f"impact: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result.model_dump(mode="json"), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
