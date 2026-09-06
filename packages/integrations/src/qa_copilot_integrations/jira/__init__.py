"""S7.4 Jira linking integration (build bible §19 S7.4).

Deterministic + **LLM-free**: a typed Jira REST v2 issue client
(:mod:`~qa_copilot_integrations.jira.client`), the deterministic failure →
issue ``fields`` mapping (:mod:`~qa_copilot_integrations.jira.issue`), a
fake-server golden replay gate (§22/§31.7, :mod:`~qa_copilot_integrations.jira.runner`),
and a JSON CLI (:mod:`~qa_copilot_integrations.jira.cli`). Nothing in this
package imports ``qa_copilot_ai`` — the §31.1 gateway is off the path.
"""

from .client import (
    JiraAuthError,
    JiraClient,
    JiraError,
    JiraHTTPError,
    JiraIssue,
    JiraNotFoundError,
    redact_secrets,
    validate_issue_key,
    validate_project_key,
)
from .golden import (
    JiraFixture,
    JiraGoldenSet,
    JiraGoldenSetError,
    MappingPin,
    default_golden_path,
    load_jira_golden_set,
)
from .issue import (
    JiraMappingError,
    build_description,
    build_issue_payload,
    build_summary,
)
from .runner import FakeJiraServer, JiraCaseResult, JiraReport, run_jira_eval

__all__ = [
    "FakeJiraServer",
    "JiraAuthError",
    "JiraCaseResult",
    "JiraClient",
    "JiraError",
    "JiraFixture",
    "JiraGoldenSet",
    "JiraGoldenSetError",
    "JiraHTTPError",
    "JiraIssue",
    "JiraMappingError",
    "JiraNotFoundError",
    "JiraReport",
    "MappingPin",
    "build_description",
    "build_issue_payload",
    "build_summary",
    "default_golden_path",
    "load_jira_golden_set",
    "redact_secrets",
    "run_jira_eval",
    "validate_issue_key",
    "validate_project_key",
]
