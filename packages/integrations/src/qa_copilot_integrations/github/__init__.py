"""S7.1 GitHub core integration (build bible §19 S7.1).

Deterministic + **LLM-free**: a typed GitHub REST v3 client
(:mod:`~qa_copilot_integrations.github.client`), a fake-server golden
replay gate (§22/§31.7, :mod:`~qa_copilot_integrations.github.runner`),
and a JSON CLI (:mod:`~qa_copilot_integrations.github.cli`). Nothing in
this package imports ``qa_copilot_ai`` — the §31.1 gateway is off the path.
"""

from .client import (
    GitHubAuthError,
    GitHubClient,
    GitHubError,
    GitHubHTTPError,
    GitHubNotFoundError,
    PullRequestInfo,
    RepositoryInfo,
    redact_secrets,
)
from .golden import (
    GitHubFixture,
    GitHubGoldenSet,
    GitHubGoldenSetError,
    default_golden_path,
    load_github_golden_set,
)
from .runner import GitHubCaseResult, GitHubReport, run_github_eval

__all__ = [
    "GitHubAuthError",
    "GitHubCaseResult",
    "GitHubClient",
    "GitHubError",
    "GitHubFixture",
    "GitHubGoldenSet",
    "GitHubGoldenSetError",
    "GitHubHTTPError",
    "GitHubNotFoundError",
    "GitHubReport",
    "PullRequestInfo",
    "RepositoryInfo",
    "default_golden_path",
    "load_github_golden_set",
    "redact_secrets",
    "run_github_eval",
]
