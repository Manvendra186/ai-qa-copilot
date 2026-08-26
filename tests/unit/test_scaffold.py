"""Scaffold smoke test — all workspace packages must be importable (S0.1)."""

import qa_copilot_ai
import qa_copilot_api
import qa_copilot_domain
import qa_copilot_execution
import qa_copilot_integrations
import qa_copilot_knowledge
import qa_copilot_repository

PACKAGES = (
    qa_copilot_ai,
    qa_copilot_api,
    qa_copilot_domain,
    qa_copilot_execution,
    qa_copilot_integrations,
    qa_copilot_knowledge,
    qa_copilot_repository,
)


def test_workspace_packages_importable() -> None:
    for pkg in PACKAGES:
        assert pkg.__version__ == "0.1.0"


def test_package_count() -> None:
    assert len(PACKAGES) == 7
