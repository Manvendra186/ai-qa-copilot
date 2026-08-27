"""S0.8 auth baseline tests (build bible §31.3).

Covers the full 401/200/403 matrix:

- password hashing (PBKDF2-SHA256): round-trip, wrong password, malformed,
  missing hash (unknown-user timing-equal path)
- JWT (HS256): round-trip, wrong secret, expired, tampered
- ``POST /api/v1/auth/login``: valid → 200 + token; bad password / unknown
  user / no hash → 401
- ``GET /api/v1/auth/me``: no token / invalid / expired / wrong-secret → 401;
  valid → 200 with user + project roles
- project-scoped RBAC (``project_members`` is authoritative, ``users.role``
  is not): non-member → 403; ``viewer`` OK on read, blocked on delete;
  ``member`` blocked where ``owner`` required; ``owner`` delete → 204
- fail loud: no ``AUTH_TOKEN_SECRET`` → 500 (no fallback secret in code)
"""

from __future__ import annotations

import base64
import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
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
TEST_DB = "qa_copilot_auth_test"
TEST_URL = f"postgresql+psycopg://qa:qa@localhost:5433/{TEST_DB}"
ADMIN_URL = "postgresql+psycopg://qa:qa@localhost:5433/postgres"

SECRET = "test-secret-0123456789abcdef"  # 16+ chars, test-only
PASSWORD = "correct-horse-battery-staple"

# ids are Postgres UUIDs — deterministic values, stable across runs
NS = NAMESPACE_DNS
ORG_ID = str(uuid5(NS, "org-acme"))
ACME_ID = str(uuid5(NS, "acme-store"))
BETA_ID = str(uuid5(NS, "beta-app"))
GHOST_ID = str(uuid5(NS, "ghost-project"))  # valid UUID, no such project
ALICE_ID = str(uuid5(NS, "user-alice"))
BOB_ID = str(uuid5(NS, "user-bob"))
CAROL_ID = str(uuid5(NS, "user-carol"))
DAVE_ID = str(uuid5(NS, "user-dave"))
NOHASH_ID = str(uuid5(NS, "user-nohash"))

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


def _admin(sql: str) -> None:
    """Run DDL against the ``postgres`` maintenance database."""
    from sqlalchemy import create_engine, text

    engine = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            conn.execute(text(sql))
    finally:
        engine.dispose()


def _make_token(
    user_id: str,
    email: str,
    secret: str = SECRET,
    exp_delta: timedelta | None = None,
) -> str:
    """Test-side token mint (same shape as ``auth.create_access_token``)."""
    now = datetime.now(UTC)
    payload = {
        "sub": user_id,
        "email": email,
        "iat": int(now.timestamp()),
        "exp": int((now + (exp_delta or auth.TOKEN_TTL)).timestamp()),
    }
    return pyjwt.encode(payload, secret, algorithm="HS256")


def _auth_header(
    user: str, secret: str = SECRET, exp_delta: timedelta | None = None
) -> dict[str, str]:
    token = _make_token(USER_IDS[user], EMAILS[user], secret=secret, exp_delta=exp_delta)
    return {"Authorization": f"Bearer {token}"}


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
            session.add(
                models.User(
                    id=USER_IDS[user],
                    email=EMAILS[user],
                    role="developer",
                    password_hash=auth.hash_password(PASSWORD),
                )
            )
        # pre-auth row: no password hash (users.password_hash is nullable)
        session.add(models.User(id=NOHASH_ID, email="nohash@local.dev"))
        session.add(models.Organization(id=ORG_ID, name="Acme Inc"))
        session.add(models.Project(id=ACME_ID, organization_id=ORG_ID, name="Acme Store"))
        session.add(models.Project(id=BETA_ID, organization_id=ORG_ID, name="Beta App"))
        session.flush()
        session.add_all(
            [
                models.ProjectMember(project_id=ACME_ID, user_id=ALICE_ID, role=ProjectRole.OWNER),
                models.ProjectMember(project_id=ACME_ID, user_id=BOB_ID, role=ProjectRole.MEMBER),
                models.ProjectMember(project_id=ACME_ID, user_id=CAROL_ID, role=ProjectRole.VIEWER),
                models.ProjectMember(project_id=BETA_ID, user_id=DAVE_ID, role=ProjectRole.OWNER),
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


# --- password hashing ----------------------------------------------------------


def test_password_roundtrip() -> None:
    hashed = auth.hash_password("s3cret")
    assert hashed.startswith("pbkdf2_sha256$390000$")
    assert auth.verify_password("s3cret", hashed)


def test_password_rejects_wrong_and_malformed() -> None:
    hashed = auth.hash_password("s3cret")
    assert not auth.verify_password("wrong", hashed)
    assert not auth.verify_password("s3cret", "not-a-hash")
    assert not auth.verify_password("s3cret", "")
    assert not auth.verify_password("s3cret", "argon2id$v=19$...")


def test_check_password_safe_without_hash() -> None:
    hashed = auth.hash_password("s3cret")
    assert not auth.check_password("s3cret", None)
    assert auth.check_password("s3cret", hashed)
    assert not auth.check_password("nope", hashed)


# --- JWT ----------------------------------------------------------------------


def test_token_roundtrip() -> None:
    token = auth.create_access_token("uid-1", "a@b.c", SECRET)
    claims = auth.decode_access_token(token, SECRET)
    assert claims["sub"] == "uid-1"
    assert claims["email"] == "a@b.c"


def test_token_rejects_wrong_secret() -> None:
    token = auth.create_access_token("uid-1", "a@b.c", SECRET)
    with pytest.raises(auth.AuthError):
        auth.decode_access_token(token, "other-secret-0123456789ab")


def test_token_rejects_expired_and_garbage() -> None:
    expired = _make_token("uid-1", "a@b.c", exp_delta=timedelta(seconds=-10))
    with pytest.raises(auth.AuthError):
        auth.decode_access_token(expired, SECRET)
    with pytest.raises(auth.AuthError):
        auth.decode_access_token("garbage.token.here", SECRET)


def test_token_rejects_tampered_payload() -> None:
    token = auth.create_access_token("uid-1", "a@b.c", SECRET)
    header, _payload, signature = token.split(".")
    attacker = (
        base64.urlsafe_b64encode(json.dumps({"sub": "uid-alice", "exp": 9_999_999_999}).encode())
        .rstrip(b"=")
        .decode()
    )
    with pytest.raises(auth.AuthError):
        auth.decode_access_token(f"{header}.{attacker}.{signature}", SECRET)


# --- login --------------------------------------------------------------------


def test_login_ok_returns_token_and_project_roles(client: TestClient) -> None:
    response = client.post(
        "/api/v1/auth/login", json={"email": "alice@local.dev", "password": PASSWORD}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == int(auth.TOKEN_TTL.total_seconds())
    assert body["user"]["email"] == "alice@local.dev"
    assert body["projects"] == [{"id": ACME_ID, "name": "Acme Store", "role": "owner"}]
    claims = auth.decode_access_token(body["token"], SECRET)
    assert claims["sub"] == ALICE_ID


def test_login_rejects_bad_password(client: TestClient) -> None:
    response = client.post(
        "/api/v1/auth/login", json={"email": "alice@local.dev", "password": "wrong"}
    )
    assert response.status_code == 401


def test_login_rejects_unknown_user(client: TestClient) -> None:
    response = client.post(
        "/api/v1/auth/login", json={"email": "ghost@local.dev", "password": PASSWORD}
    )
    assert response.status_code == 401


def test_login_rejects_user_without_password_hash(client: TestClient) -> None:
    response = client.post(
        "/api/v1/auth/login", json={"email": "nohash@local.dev", "password": PASSWORD}
    )
    assert response.status_code == 401


# --- /me ----------------------------------------------------------------------


def test_me_requires_bearer_token(client: TestClient) -> None:
    assert client.get("/api/v1/auth/me").status_code == 401


def test_me_rejects_invalid_expired_and_wrong_secret_tokens(client: TestClient) -> None:
    cases = [
        {"Authorization": "Bearer garbage.token.here"},
        _auth_header("alice", exp_delta=timedelta(seconds=-10)),
        _auth_header("alice", secret="other-secret-0123456789ab"),
        {"Authorization": "Basic dXNlcjpwYXNz"},
        {"Authorization": "Bearer"},
    ]
    for headers in cases:
        assert client.get("/api/v1/auth/me", headers=headers).status_code == 401, headers


def test_me_ok_returns_user_and_projects(client: TestClient) -> None:
    response = client.get("/api/v1/auth/me", headers=_auth_header("bob"))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["user"]["email"] == "bob@local.dev"
    assert body["projects"] == [{"id": ACME_ID, "name": "Acme Store", "role": "member"}]


# --- RBAC (project-scoped, §31.3) ----------------------------------------------


def test_projects_list_requires_auth(client: TestClient) -> None:
    assert client.get("/api/v1/projects").status_code == 401


def test_projects_list_shows_memberships(client: TestClient) -> None:
    response = client.get("/api/v1/projects", headers=_auth_header("alice"))
    assert response.status_code == 200
    assert response.json() == [{"id": ACME_ID, "name": "Acme Store", "role": "owner"}]


def test_project_read_ok_for_owner_member_and_viewer(client: TestClient) -> None:
    for user in ("alice", "bob", "carol"):
        response = client.get(f"/api/v1/projects/{ACME_ID}", headers=_auth_header(user))
        assert response.status_code == 200, (user, response.text)
        assert response.json()["name"] == "Acme Store"


def test_project_read_forbidden_for_non_member(client: TestClient) -> None:
    # dave owns beta but is not a member of acme → 403 (no existence leak either)
    response = client.get(f"/api/v1/projects/{ACME_ID}", headers=_auth_header("dave"))
    assert response.status_code == 403
    response = client.get(f"/api/v1/projects/{GHOST_ID}", headers=_auth_header("dave"))
    assert response.status_code == 403


def test_project_delete_requires_owner(client: TestClient) -> None:
    # member and viewer may not delete (§31.3: owner-only)
    for user in ("bob", "carol"):
        response = client.delete(f"/api/v1/projects/{ACME_ID}", headers=_auth_header(user))
        assert response.status_code == 403, (user, response.text)
    # non-member may not delete either
    response = client.delete(f"/api/v1/projects/{ACME_ID}", headers=_auth_header("dave"))
    assert response.status_code == 403
    # owner deletes → 204, project is gone, its memberships with it
    response = client.delete(f"/api/v1/projects/{ACME_ID}", headers=_auth_header("alice"))
    assert response.status_code == 204
    # alice lost her membership → 403 (not 404: non-members get no existence leak)
    assert (
        client.get(f"/api/v1/projects/{ACME_ID}", headers=_auth_header("alice")).status_code == 403
    )
    assert client.get("/api/v1/projects", headers=_auth_header("bob")).json() == []


def test_project_delete_requires_auth(client: TestClient) -> None:
    assert client.delete(f"/api/v1/projects/{ACME_ID}").status_code == 401


# --- fail loud -----------------------------------------------------------------


def test_missing_secret_fails_loud(monkeypatch: pytest.MonkeyPatch) -> None:
    """No ``AUTH_TOKEN_SECRET`` → 500; there must be no fallback secret."""
    monkeypatch.delenv("AUTH_TOKEN_SECRET", raising=False)
    app = create_app(
        settings=Settings(database_url=TEST_URL, _env_file=None)  # type: ignore[call-arg]
    )
    client = TestClient(app, raise_server_exceptions=False)
    response = client.post(
        "/api/v1/auth/login", json={"email": "alice@local.dev", "password": PASSWORD}
    )
    assert response.status_code == 500
    assert "AUTH_TOKEN_SECRET" in response.text
    app.state.engine.dispose()
