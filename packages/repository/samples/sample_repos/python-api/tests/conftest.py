"""Shared pytest fixtures for the sample suite."""

import pytest


@pytest.fixture()
def sample_user() -> dict[str, str]:
    """A fixture user used across the sample test suite."""
    return {"email": "user@example.com", "role": "member"}


@pytest.fixture()
def expected_item_fields() -> tuple[str, ...]:
    """The fields the items endpoint is expected to return."""
    return ("id", "name", "status")
