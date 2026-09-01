"""Data access + Git, AST, and file indexing (build bible §7).

S0.5: SQLAlchemy 2.0 ORM models for the §10 core data model
(:mod:`qa_copilot_repository.models`) and engine/session factories
(:mod:`qa_copilot_repository.db`). Alembic migrations live in
``infra/migrations`` (repo root).

S0.6: prompt-registry loader (:mod:`qa_copilot_repository.prompts`, §31.6)
and ``ai_actions`` audit recorder (:mod:`qa_copilot_repository.audit`, §31.1).

S0.8: user + project-membership lookups for the auth baseline
(:mod:`qa_copilot_repository.membership`, §31.3).

S1.3: suite persistence — requirement + test-case rows + the §10 M:N join
(:mod:`qa_copilot_repository.requirements`).

S2.1: deterministic repository scanner — languages, frameworks, test
structure (:mod:`qa_copilot_repository.scanner`), producing
:class:`qa_copilot_domain.RepositoryProfile`.

S2.2: convention extractor — locators, page objects, fixtures, helpers
(:mod:`qa_copilot_repository.conventions`), producing
:class:`qa_copilot_domain.TestConventions`.

S2.4: generated-test review persistence — the S2.3 agent output as a
``generated_tests`` row + approve/apply/reject transitions
(:mod:`qa_copilot_repository.generated_tests`, build bible §19 S2.4).

S3.1: execution-run persistence — the worker's ``RunReport`` mapped onto
``test_runs`` / ``test_results`` / ``artifacts`` rows + read helpers
(:mod:`qa_copilot_repository.runs`, build bible §10, §15).

S6.1: deterministic change-impact analysis — changed files (explicit list
or a ``base..head`` git range) → impacted test files, LLM-free
(:mod:`qa_copilot_repository.impact`, build bible §19).
"""

from . import (
    audit,
    conventions,
    db,
    generated_tests,
    impact,
    membership,
    models,
    prompts,
    requirements,
    runs,
    scanner,
)
from .conventions import extract_conventions
from .impact import (
    GeneratedTestRef,
    applied_generated_refs,
    changed_files_from_range,
    compute_impact,
    impact_from_session,
    main,
    normalize_changed,
)
from .scanner import scan_repository

__version__ = "0.1.0"

__all__ = [
    "__version__",
    "GeneratedTestRef",
    "applied_generated_refs",
    "audit",
    "changed_files_from_range",
    "compute_impact",
    "conventions",
    "db",
    "extract_conventions",
    "generated_tests",
    "impact",
    "impact_from_session",
    "main",
    "membership",
    "models",
    "normalize_changed",
    "prompts",
    "requirements",
    "runs",
    "scan_repository",
    "scanner",
]
