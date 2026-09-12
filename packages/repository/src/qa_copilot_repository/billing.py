"""S8.4 billing core (build bible §19 S8.4): plans, quotas, usage metering.

This module is the single LLM-free source of truth for everything billing —
no FastAPI, no LLM client, no settings:

- ``PlanSpec`` / ``PLAN_CATALOG`` — the closed plan set (``free`` /
  ``pro`` / ``enterprise``) and its quota caps. Plans are stored as a plain
  string in the pre-existing ``organizations.plan`` column (S0.5 baseline,
  default ``dev``); the catalog resolves whatever is stored, so no data
  migration is required.
- ``check_quota`` — the deterministic pre-flight quota gate the API's job
  dispatch path (``qa_copilot_api.jobs``) consults before *any* job starts.
- ``org_usage`` — exact usage metering derived from the §10 rows:
  ``test_runs`` (runs/month), ``ai_actions`` tokens (tokens/month),
  ``jobs`` (concurrent jobs) and ``projects`` (project count), all
  organization-scoped and (for the monthly counters) current-month.

The API layer renders these into the §19 S8.4 responses (``plan`` /
``usage`` endpoints, 409 ``plan_limit``) and records the S8.3 audit rows
(``org.plan.updated`` / ``org.quota.denied``).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from qa_copilot_domain.enums import JobStatus, JobType
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import models

__all__ = [
    "ASSIGNABLE_PLANS",
    "DEFAULT_PLAN",
    "OrgUsage",
    "PLAN_CATALOG",
    "PlanLimitExceeded",
    "PlanSpec",
    "QuotaCheck",
    "check_quota",
    "month_label",
    "month_start",
    "org_usage",
    "plan_for",
]

#: Plans a local admin can assign via ``PATCH /organizations/{id}`` (the
#: closed catalog; §19 S8.4 "local admins can set any plan").
ASSIGNABLE_PLANS: tuple[str, ...] = ("free", "pro", "enterprise")

#: What an unassigned/legacy org (``plan = 'dev'`` or NULL) is treated as.
DEFAULT_PLAN = "free"


@dataclass(frozen=True, slots=True)
class PlanSpec:
    """Quota caps for one plan (build bible §19 S8.4).

    ``runs_per_month`` counts executed regression runs (``run_execution``
    jobs → ``test_runs`` rows); ``tokens_per_month`` counts LLM tokens
    consumed by the org's AI actions; ``concurrent_jobs`` caps in-flight
    (``pending``/``running``) jobs; ``max_projects`` caps org projects.
    """

    name: str
    max_projects: int
    runs_per_month: int
    tokens_per_month: int
    concurrent_jobs: int


#: Closed plan catalog — ``free`` is the safe default, ``enterprise`` the
#: unbounded local plan (bible: local-first, no external billing).
PLAN_CATALOG: dict[str, PlanSpec] = {
    "free": PlanSpec(
        name="free",
        max_projects=1,
        runs_per_month=25,
        tokens_per_month=100_000,
        concurrent_jobs=1,
    ),
    "pro": PlanSpec(
        name="pro",
        max_projects=10,
        runs_per_month=500,
        tokens_per_month=2_000_000,
        concurrent_jobs=5,
    ),
    "enterprise": PlanSpec(
        name="enterprise",
        max_projects=10**9,
        runs_per_month=10**9,
        tokens_per_month=10**9,
        concurrent_jobs=10**9,
    ),
}


def plan_for(stored: str | None) -> PlanSpec:
    """Resolve the stored ``organizations.plan`` value to a :class:`PlanSpec`.

    ``dev`` (the S0.5 seed default), empty, NULL, or any unknown value
    resolves to the safe default (``free``) — a stale value must never
    unlock premium capacity.
    """
    if stored in (None, "", "dev"):
        return PLAN_CATALOG[DEFAULT_PLAN]
    try:
        return PLAN_CATALOG[stored]
    except KeyError:
        return PLAN_CATALOG[DEFAULT_PLAN]


class PlanLimitExceeded(Exception):
    """S8.4: a job dispatch hit the org's plan quota (build bible §19 S8.4).

    Raised by the API's job-dispatch gate (``JobRunner.start``) after the
    job row is removed and the ``job.rejected`` event is published — the
    FastAPI exception handler turns it into the 409 ``plan_limit`` response
    plus the ``org.quota.denied`` audit row. No partial state remains.
    """

    def __init__(
        self,
        *,
        org_id: str,
        project_id: str,
        job_id: str,
        limit: str,
        plan: str,
        used: int,
        allowed: int,
        actor_id: str | None,
    ) -> None:
        self.org_id = org_id
        self.project_id = project_id
        self.job_id = job_id
        self.limit = limit
        self.plan = plan
        self.used = used
        self.allowed = allowed
        self.actor_id = actor_id
        super().__init__(
            f"plan '{plan}' limit '{limit}' exceeded for org {org_id} ({used}/{allowed})"
        )


@dataclass(frozen=True, slots=True)
class QuotaCheck:
    """Result of :func:`check_quota` — ``limit`` is ``None`` when in budget."""

    limit: str | None
    used: int = 0
    allowed: int = 0


def month_start(now: datetime | None = None) -> datetime:
    """First instant of the current UTC month (the metering window)."""
    at = now if now is not None else datetime.now(UTC)
    return at.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def month_label(now: datetime | None = None) -> str:
    """``YYYY-MM`` label of the metering window (usage endpoint contract)."""
    return month_start(now).strftime("%Y-%m")


def _active_job_count(session: Session, org_id: str, *, exclude_job_id: str | None = None) -> int:
    """In-flight (``pending``/``running``) jobs across the org's projects."""
    conds = [
        models.Project.organization_id == org_id,
        models.Job.status.in_((JobStatus.PENDING, JobStatus.RUNNING)),
    ]
    if exclude_job_id is not None:
        conds.append(models.Job.id != exclude_job_id)
    stmt = (
        select(func.count())
        .select_from(models.Job)
        .join(models.Project, models.Job.project_id == models.Project.id)
        .where(*conds)
    )
    return int(session.scalar(stmt) or 0)


def _runs_this_month(session: Session, org_id: str) -> int:
    """Executed runs in the org this month (``test_runs`` rows)."""
    stmt = (
        select(func.count())
        .select_from(models.TestRun)
        .join(models.Project, models.TestRun.project_id == models.Project.id)
        .where(
            models.Project.organization_id == org_id,
            models.TestRun.created_at >= month_start(),
        )
    )
    return int(session.scalar(stmt) or 0)


def _tokens_this_month(session: Session, org_id: str) -> int:
    """LLM tokens consumed by the org's AI actions this month."""
    stmt = (
        select(func.coalesce(func.sum(models.AIAction.tokens_in + models.AIAction.tokens_out), 0))
        .select_from(models.AIAction)
        .join(models.AISession, models.AIAction.session_id == models.AISession.id)
        .join(models.Project, models.AISession.project_id == models.Project.id)
        .where(
            models.Project.organization_id == org_id,
            models.AIAction.created_at >= month_start(),
        )
    )
    return int(session.scalar(stmt) or 0)


def _project_count(session: Session, org_id: str) -> int:
    stmt = (
        select(func.count())
        .select_from(models.Project)
        .where(models.Project.organization_id == org_id)
    )
    return int(session.scalar(stmt) or 0)


@dataclass(frozen=True, slots=True)
class OrgUsage:
    """One org's live usage (see :func:`org_usage`)."""

    runs: int
    tokens: int
    active_jobs: int
    projects: int


def org_usage(session: Session, org_id: str) -> OrgUsage:
    """Exact, live usage metering for *org_id* (build bible §19 S8.4).

    Derived — not counters — from the §10 rows, so it can never drift from
    what the org actually did: runs = ``test_runs`` created this month,
    tokens = ``ai_actions`` (in + out) created this month, active jobs =
    in-flight ``jobs``, projects = the org's ``projects``.
    """
    return OrgUsage(
        runs=_runs_this_month(session, org_id),
        tokens=_tokens_this_month(session, org_id),
        active_jobs=_active_job_count(session, org_id),
        projects=_project_count(session, org_id),
    )


def check_quota(
    session: Session,
    org_id: str,
    *,
    job_type: JobType,
    exclude_job_id: str | None = None,
) -> QuotaCheck:
    """Deterministic pre-flight quota gate for one dispatch (§19 S8.4).

    Checks, in a fixed order (the first violated cap wins — stable for
    tests and clients):

    1. ``concurrent_jobs`` — in-flight jobs already occupy the cap
       (``exclude_job_id`` lets a re-dispatch of the same job see itself as
       occupying its slot);
    2. ``runs_per_month`` — only for ``run_execution`` jobs (the only kind
       that executes tests and bills a "run");
    3. ``tokens_per_month`` — the org's AI token spend this month.

    Returns :class:`QuotaCheck` with ``limit=None`` when the dispatch is
    within budget; otherwise the violated limit name with its exact
    ``used``/``allowed`` values (the 409 body reports both).
    """
    org = session.get(models.Organization, org_id)
    spec = plan_for(org.plan if org is not None else None)
    active = _active_job_count(session, org_id, exclude_job_id=exclude_job_id)
    if active >= spec.concurrent_jobs:
        return QuotaCheck("concurrent_jobs", active, spec.concurrent_jobs)
    if (
        job_type == JobType.RUN_EXECUTION
        and (runs := _runs_this_month(session, org_id)) >= spec.runs_per_month
    ):
        return QuotaCheck("runs_per_month", runs, spec.runs_per_month)
    if (tokens := _tokens_this_month(session, org_id)) >= spec.tokens_per_month:
        return QuotaCheck("tokens_per_month", tokens, spec.tokens_per_month)
    return QuotaCheck(None)
