"""Knowledge source adapters (build bible §14: where context comes from).

Pure, deterministic adapters from domain entities / plain wire records / the
repository file tree to :class:`KnowledgeDocument` — no DB, no LLM. The API
layer (S5.3) feeds these from persisted rows; the CLI (S5.1) from a local
repository plus an ad-hoc corpus.
"""

from __future__ import annotations

import os
import re
from collections.abc import Sequence
from pathlib import Path

from qa_copilot_domain import Requirement, TestCase, TestConventions
from qa_copilot_repository.scanner import MAX_FILES, SKIP_DIRS, is_test_file, read_text_capped

from .models import (
    KnowledgeDocument,
    KnowledgeSourceType,
    RunRecord,
    TestOutcomeRecord,
)

_SLUG_RE = re.compile(r"[^a-z0-9]+")

#: File extensions that can carry source knowledge (lowercase, with dot).
_KNOWLEDGE_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs",
        ".py", ".json", ".yaml", ".yml", ".toml",
        ".md", ".html", ".css", ".scss", ".sql", ".sh", ".txt",
    }
)

#: Lockfiles / generated noise that never carry QA knowledge.
_SKIP_FILE_NAMES: frozenset[str] = frozenset(
    {
        "pnpm-lock.yaml", "package-lock.json", "yarn.lock",
        "bun.lockb", "poetry.lock", "uv.lock",
    }
)
_SKIP_SUFFIXES: tuple[str, ...] = (".min.js", ".min.css", ".map")

#: Failure evidence shown per outcome (build bible §14: evidence, capped).
_MAX_EVIDENCE_LINES = 2
_MAX_EVIDENCE_CHARS = 200


def requirement_documents(
    requirements: Sequence[Requirement],
    test_cases: Sequence[TestCase] | None = None,
) -> list[KnowledgeDocument]:
    """Requirement + test-case documents (build bible §14: requirements, test cases)."""
    docs: list[KnowledgeDocument] = []
    for req in requirements:
        parts: list[str] = [f"Requirement: {req.title}"]
        if req.content:
            parts.append(req.content)
        if req.acceptance_criteria:
            criteria = "\n".join(f"- {c}" for c in req.acceptance_criteria)
            parts.append("Acceptance criteria:\n" + criteria)
        parts.append(f"Risk: {req.risk.value}")
        docs.append(
            KnowledgeDocument(
                id=req.id,
                source_type=KnowledgeSourceType.REQUIREMENT,
                source_ref=req.id or _slug(f"requirement-{req.title}"),
                title=req.title,
                content="\n\n".join(parts),
                metadata={"risk": req.risk.value},
            )
        )
    for tc in test_cases or []:
        parts = [
            f"Test case: {tc.title}",
            f"(type: {tc.type.value}, priority: {tc.priority.value})",
        ]
        if tc.preconditions:
            parts.append("Preconditions:\n" + "\n".join(f"- {p}" for p in tc.preconditions))
        if tc.steps:
            steps = "\n".join(f"{i}. {s}" for i, s in enumerate(tc.steps, start=1))
            parts.append("Steps:\n" + steps)
        if tc.expected_results:
            parts.append("Expected results:\n" + "\n".join(f"- {e}" for e in tc.expected_results))
        if tc.requirement_refs:
            parts.append("Requirement refs: " + ", ".join(tc.requirement_refs))
        docs.append(
            KnowledgeDocument(
                id=tc.id,
                source_type=KnowledgeSourceType.TEST_CASE,
                source_ref=tc.id or _slug(f"test-case-{tc.title}"),
                title=tc.title,
                content="\n\n".join(parts),
                metadata={"type": tc.type.value, "priority": tc.priority.value},
            )
        )
    return docs


def standard_documents(
    conventions: TestConventions, *, repo_name: str | None = None
) -> list[KnowledgeDocument]:
    """Conventions document (build bible §14: standards/conventions)."""
    suffix = f" for {repo_name}" if repo_name else ""
    parts: list[str] = [f"Test conventions{suffix}"]
    if conventions.test_file_patterns:
        patterns = "\n".join(f"- {p}" for p in conventions.test_file_patterns)
        parts.append("Test file patterns:\n" + patterns)
    if conventions.locator_styles:
        parts.append(
            "Locator APIs in use:\n"
            + "\n".join(
                f"- {style.api} ({style.framework}, {style.count} uses)"
                for style in conventions.locator_styles
            )
        )
    if conventions.page_object_files:
        files = "\n".join(f"- {p}" for p in conventions.page_object_files)
        parts.append("Page object files:\n" + files)
    if conventions.fixture_files:
        parts.append("Fixture files:\n" + "\n".join(f"- {p}" for p in conventions.fixture_files))
    if conventions.helper_files:
        parts.append("Helper files:\n" + "\n".join(f"- {p}" for p in conventions.helper_files))
    if conventions.test_configs:
        parts.append("Test configs:\n" + "\n".join(f"- {p}" for p in conventions.test_configs))
    if conventions.test_ids:
        ids = "\n".join(f"- {t}" for t in conventions.test_ids)
        parts.append("data-testid vocabulary:\n" + ids)
    if conventions.base_url:
        parts.append(f"Base URL: {conventions.base_url}")
    if conventions.test_scripts:
        script_lines = [f"- {script.name}: {script.command}" for script in conventions.test_scripts]
        parts.append("Test scripts:\n" + "\n".join(script_lines))
    if conventions.notes:
        parts.append("Notes:\n" + "\n".join(f"- {n}" for n in conventions.notes))
    return [
        KnowledgeDocument(
            source_type=KnowledgeSourceType.STANDARD,
            source_ref="standards/test-conventions",
            title=f"Test conventions{suffix}",
            content="\n\n".join(parts),
            metadata={"base_url": conventions.base_url},
        )
    ]


def history_documents(runs: Sequence[RunRecord]) -> list[KnowledgeDocument]:
    """Run-history documents (build bible §14: execution history, failure patterns)."""
    docs: list[KnowledgeDocument] = []
    for run in runs:
        header = f"Test run {run.run_id}: status {run.status}"
        extras: list[str] = []
        if run.commit_sha:
            extras.append(f"commit {run.commit_sha}")
        if run.started_at is not None:
            extras.append(run.started_at.strftime("%Y-%m-%d"))
        if extras:
            header += f" ({', '.join(extras)})"
        parts = [header]
        if run.results:
            parts.append("Results:\n" + "\n".join(_outcome_line(r) for r in run.results))
        docs.append(
            KnowledgeDocument(
                source_type=KnowledgeSourceType.RUN_HISTORY,
                source_ref=f"run-{run.run_id}",
                title=f"Run {run.run_id} ({run.status})",
                content="\n\n".join(parts),
            )
        )
    return docs


def repository_file_documents(
    root: Path, *, max_files: int = MAX_FILES
) -> tuple[list[KnowledgeDocument], bool]:
    """Knowledge documents for the source files under *root* (build bible §14).

    Returns ``(documents, capped)``; deterministic walk order (sorted dirs +
    files). Unreadable/binary files, lockfiles and generated noise are
    skipped (build bible §11: never crash on a bad repository).
    """
    root_path = root.resolve()
    if not root_path.is_dir():
        raise NotADirectoryError(f"repository root is not a directory: {root}")
    docs: list[KnowledgeDocument] = []
    capped = False
    for dirpath, dirnames, filenames in os.walk(root_path):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for name in sorted(filenames):
            if capped:
                break
            if name in _SKIP_FILE_NAMES or name.endswith(_SKIP_SUFFIXES):
                continue
            suffix = Path(name).suffix.lower()
            if suffix not in _KNOWLEDGE_EXTENSIONS:
                continue
            path = Path(dirpath) / name
            text = read_text_capped(path)
            if text is None:
                continue
            rel = path.relative_to(root_path).as_posix()
            docs.append(
                KnowledgeDocument(
                    source_type=KnowledgeSourceType.REPOSITORY_FILE,
                    source_ref=rel,
                    title=rel,
                    content=text,
                    metadata={
                        "path": rel,
                        "language": suffix.lstrip("."),
                        "is_test": is_test_file(name, os.path.basename(dirpath)),
                    },
                )
            )
            if len(docs) >= max_files:
                capped = True
                break
        if capped:
            break
    return docs, capped


def _outcome_line(record: TestOutcomeRecord) -> str:
    line = f"- {record.test}: {record.status}"
    if record.failure_category or record.failure_root_cause:
        details = []
        if record.failure_category:
            details.append(record.failure_category)
        if record.failure_root_cause:
            details.append(record.failure_root_cause)
        line += f" ({', '.join(details)})"
    for evidence in record.failure_evidence[:_MAX_EVIDENCE_LINES]:
        line += f"; evidence: {evidence[:_MAX_EVIDENCE_CHARS]}"
    return line


def _slug(value: str) -> str:
    return _SLUG_RE.sub("-", value.lower()).strip("-")[:60] or "document"
