"""S7.1 integration-config API tests (build bible §19 S7.1, §17).

Covers the project-scoped endpoints (RBAC via ``auth.require_role``):

- ``GET    /api/v1/projects/{id}/integrations``          — member+
- ``PUT    /api/v1/projects/{id}/integrations/{provider}`` — owner+
- ``DELETE /api/v1/projects/{id}/integrations/{provider}`` — owner+

Token-safety contract (S7.1 exit: "PAT never appears in logs or audit"):
the API accepts only a ``token_ref`` (the secret's *name*), and every
response exposes ``token_ref`` + ``token_configured`` — never a token
value, even when the caller tries to smuggle one in the request body.

Same scratch-DB pattern as ``test_auth.py`` (dedicated database).
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_DNS, uuid5

import jwt as pyjwt
import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from qa_copilot_api import auth
from qa_copilot_api.config import Settings
from qa_copilot_api.main import create_app
from qa_copilot_domain.enums import ProjectRole
from qa_copilot_repository import db, models

REPO_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = REPO_ROOT / "alembic.ini"
TEST_DB = "qa_copilot_integrations_test"
TEST_URL = f"postgresql+psycopg://qa:qa@localhost:5433/{TEST_DB}"
ADMIN_URL = "postgresql+psycopg://qa:qa@localhost:5433/postgres"

SECRET = "test-secret-0123456789abcdef"  # 16+ chars, test-only

# ids are Postgres UUIDs — deterministic values, stable across runs
NS = NAMESPACE_DNS
ORG_ID = str(uuid5(NS, "org-acme"))
ACME_ID = str(uuid5(NS, "acme-store"))
GHOST_ID = str(uuid5(NS, "ghost-project"))  # valid UUID, no such project
ALICE_ID = str(uuid5(NS, "user-alice"))
BOB_ID = str(uuid5(NS, "user-bob"))
CAROL_ID = str(uuid5(NS, "user-carol"))
DAVE_ID = str(uuid5(NS, "user-dave"))

EMAILS = {
    "alice": "alice@local.dev",  # owner of acme
    "bob": "bob@local.dev",  # member of acme
    "carol": "carol@local.dev",  # viewer of acme
    "dave": "dave@local.dev",  # owner of beta, NOT a member of acme
}
USER_IDS = {
    "alice": ALICE_ID,
    "bob": BOB_ID,
    "carol": CAROL_ID,
    "dave": DAVE_ID,
}

# sentinel "token value" — must never appear in any API response
SENTINEL_PAT = "ghp_S71ApiSentinel0123456789"


def _admin(sql: str) -> None:
    """Run DDL against the ``postgres`` maintenance database."""
    from sqlalchemy import create_engine, text

    engine = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            conn.execute(text(sql))
    finally:
        engine.dispose()


def _make_token(user: str) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": USER_IDS[user],
        "email": EMAILS[user],
        "iat": int(now.timestamp()),
        "exp": int((now + auth.TOKEN_TTL).timestamp()),
    }
    return pyjwt.encode(payload, SECRET, algorithm="HS256")


def _auth_header(user: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {_make_token(user)}"}


def _integrations_url(project_id: str, provider: str | None = None) -> str:
    base = f"/api/v1/projects/{project_id}/integrations"
    return f"{base}/{provider}" if provider else base


@pytest.fixture()
def env() -> Iterator[dict[str, Any]]:
    """Scratch Postgres DB + migrated schema + users/projects/roles + app."""
    import os

    _admin(f"DROP DATABASE IF EXISTS {TEST_DB}")
    _admin(f"CREATE DATABASE {TEST_DB}")

    saved_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = TEST_URL  # alembic env.py: env var wins
    engine = db.make_engine(TEST_URL)
    command.upgrade(Config(str(ALEMBIC_INI)), "head")

    with db.make_session_factory(engine)() as session:
        for user in EMAILS:
            session.add(models.User(id=USER_IDS[user], email=EMAILS[user], role="developer"))
        session.add(models.Organization(id=ORG_ID, name="Acme Inc"))
        session.add(models.Project(id=ACME_ID, organization_id=ORG_ID, name="Acme Store"))
        session.flush()
        session.add_all(
            [
                models.ProjectMember(project_id=ACME_ID, user_id=ALICE_ID, role=ProjectRole.OWNER),
                models.ProjectMember(project_id=ACME_ID, user_id=BOB_ID, role=ProjectRole.MEMBER),
                models.ProjectMember(project_id=ACME_ID, user_id=CAROL_ID, role=ProjectRole.VIEWER),
            ]
        )
        session.commit()

    app = create_app(
        # ``_env_file=None`` is pydantic-settings' private init kwarg (keep
        # tests from reading the dev .env) — mypy can't see it in the stubs.
        settings=Settings(  # type: ignore[call-arg]
            database_url=TEST_URL, auth_token_secret=SECRET, _env_file=None
        )
    )

    yield {"app": app, "engine": engine}

    # close pooled connections before DROP DATABASE, or Postgres refuses
    app.state.engine.dispose()
    engine.dispose()
    if saved_url is None:
        os.environ.pop("DATABASE_URL", None)
    else:
        os.environ["DATABASE_URL"] = saved_url
    _admin(f"DROP DATABASE IF EXISTS {TEST_DB}")


@pytest.fixture()
def client(env: dict[str, Any]) -> Iterator[TestClient]:
    with TestClient(env["app"]) as test_client:
        yield test_client


PUT_BODY = {
    "base_url": "https://github.example.com",
    "token_ref": "GITHUB_TOKEN",  # a secret NAME, never a value (§17)
    "enabled": True,
}


def _put(client: TestClient, user: str, project_id: str = ACME_ID, provider: str = "github") -> Any:
    return client.put(
        _integrations_url(project_id, provider),
        json=PUT_BODY,
        headers=_auth_header(user),
    )


# --- authentication & RBAC --------------------------------------------------------


def test_list_requires_authentication(client: TestClient) -> None:
    resp = client.get(_integrations_url(ACME_ID))
    assert resp.status_code == 401


def test_list_forbidden_for_viewer_and_non_member(client: TestClient) -> None:
    assert client.get(_integrations_url(ACME_ID), headers=_auth_header("carol")).status_code == 403
    assert client.get(_integrations_url(ACME_ID), headers=_auth_header("dave")).status_code == 403


def test_put_and_delete_require_owner(client: TestClient) -> None:
    for user in ("bob", "carol", "dave"):
        assert _put(client, user).status_code == 403
        assert client.delete(
            _integrations_url(ACME_ID, "github"), headers=_auth_header(user)
        ).status_code == 403


def test_unknown_project_is_403_not_404(client: TestClient) -> None:
    """No existence leak: a valid UUID with no membership is just forbidden."""
    assert client.get(_integrations_url(GHOST_ID), headers=_auth_header("alice")).status_code == 403
    assert _put(client, "alice", project_id=GHOST_ID).status_code == 403


# --- read ------------------------------------------------------------------------


def test_member_can_list_empty(client: TestClient) -> None:
    resp = client.get(_integrations_url(ACME_ID), headers=_auth_header("bob"))
    assert resp.status_code == 200
    assert resp.json() == []


# --- write: idempotent PUT + token-safe responses ---------------------------------


def test_put_creates_then_updates_same_provider_row(client: TestClient) -> None:
    first = _put(client, "alice")
    assert first.status_code == 200
    first_json = first.json()
    assert first_json["provider"] == "github"
    assert first_json["project_id"] == ACME_ID
    assert first_json["base_url"] == PUT_BODY["base_url"]
    assert first_json["token_ref"] == "GITHUB_TOKEN"
    assert first_json["token_configured"] is True
    assert first_json["enabled"] is True

    # idempotent PUT: same (project, provider) → one row, values updated
    updated = client.put(
        _integrations_url(ACME_ID, "github"),
        json={"base_url": None, "token_ref": "OTHER_SECRET_NAME", "enabled": False},
        headers=_auth_header("alice"),
    )
    assert updated.status_code == 200
    updated_json = updated.json()
    assert updated_json["base_url"] is None
    assert updated_json["token_ref"] == "OTHER_SECRET_NAME"
    assert updated_json["token_configured"] is True
    assert updated_json["enabled"] is False
    assert updated_json["created_at"] == first_json["created_at"]  # same row
    assert updated_json["updated_at"] >= first_json["updated_at"]

    listing = client.get(_integrations_url(ACME_ID), headers=_auth_header("bob")).json()
    assert len(listing) == 1
    assert listing[0]["provider"] == "github"


def test_response_contains_only_token_ref_and_token_configured(client: TestClient) -> None:
    body = dict(PUT_BODY, token=SENTINEL_PAT, pat=SENTINEL_PAT, secret=SENTINEL_PAT)  # red-team
    resp = client.put(
        _integrations_url(ACME_ID, "github"), json=body, headers=_auth_header("alice")
    )
    assert resp.status_code == 200
    text = resp.text
    assert SENTINEL_PAT not in text  # smuggled token values never echo back
    keys = set(resp.json())
    assert keys == {
        "project_id",
        "provider",
        "base_url",
        "token_ref",
        "token_configured",
        "enabled",
        "created_at",
        "updated_at",
    }
    # the listing agrees and is token-safe as well
    listing_text = client.get(_integrations_url(ACME_ID), headers=_auth_header("bob")).text
    assert SENTINEL_PAT not in listing_text



def test_put_rejects_invalid_provider_slug(client: TestClient) -> None:
    # URL-safe values that violate the 1-32-char ``[a-z0-9_-]`` slug contract
    for provider in ("GitHub!", "a.b", "UPPER", "a" * 40):
        resp = client.put(
            _integrations_url(ACME_ID, provider), json=PUT_BODY, headers=_auth_header("alice")
        )
        assert resp.status_code == 422, provider
    # no partial writes from the rejected attempts
    assert client.get(_integrations_url(ACME_ID), headers=_auth_header("bob")).json() == []


def test_put_accepts_open_provider_set(client: TestClient) -> None:
    """Future providers need no schema change (open but slug-constrained)."""
    for provider in ("gitlab", "bitbucket"):
        assert _put(client, "alice", provider=provider).status_code == 200
    providers = {
        row["provider"]
        for row in client.get(_integrations_url(ACME_ID), headers=_auth_header("bob")).json()
    }
    assert providers == {"gitlab", "bitbucket"}


# --- delete -----------------------------------------------------------------------


def test_delete_removes_then_404s(client: TestClient) -> None:
    assert _put(client, "alice").status_code == 200
    assert (
        client.delete(
            _integrations_url(ACME_ID, "github"), headers=_auth_header("alice")
        ).status_code
        == 204
    )
    assert client.get(_integrations_url(ACME_ID), headers=_auth_header("bob")).json() == []
    # deleting a missing provider is a 404
    resp = client.delete(_integrations_url(ACME_ID, "github"), headers=_auth_header("alice"))
    assert resp.status_code == 404


# --- repository-layer invariants ---------------------------------------------------


def test_upsert_is_single_row_per_project_provider(client: TestClient, env: dict[str, Any]) -> None:
    assert _put(client, "alice").status_code == 200
    assert _put(client, "alice").status_code == 200
    from sqlalchemy import func, select

    engine = env["engine"]
    with db.make_session_factory(engine)() as session:
        count = session.scalar(
            select(func.count())
            .select_from(models.IntegrationConfig)
            .where(models.IntegrationConfig.project_id == ACME_ID)
        )
    assert count == 1

