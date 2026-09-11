"""S0.8 auth baseline tests (build bible Â§31.3) + S8.1 hardening (Â§19).

Covers the full 401/200/403 matrix:

- password hashing (PBKDF2-SHA256): round-trip, wrong password, malformed,
  missing hash (unknown-user timing-equal path)
- JWT (HS256): round-trip, wrong secret, expired, tampered
- ``POST /api/v1/auth/login``: valid â†’ 200 + token; bad password / unknown
  user / no hash â†’ 401
- ``GET /api/v1/auth/me``: no token / invalid / expired / wrong-secret â†’ 401;
  valid â†’ 200 with user + organizations + project roles
- project-scoped RBAC (``project_members`` is authoritative, ``users.role``
  is not): non-member â†’ 403; ``viewer`` OK on read, blocked on delete;
  ``member`` blocked where ``owner`` required; ``owner`` delete â†’ 204
- fail loud: no ``AUTH_TOKEN_SECRET`` â†’ 500 (no fallback secret in code)

S8.1 additions:

- ``POST /api/v1/auth/register``: 201 + user + owned organization;
  duplicate email â†’ 409; password policy / bad email â†’ 422
- rotating opaque refresh tokens: login issues one; ``/auth/refresh``
  rotates; reuse after rotation â†’ 401 + whole family revoked; unknown â†’ 401
- ``POST /api/v1/auth/change-password``: 204 + all refresh tokens revoked +
  old password dead; wrong current â†’ 401 (tokens survive); weak new â†’ 422
- login brute-force throttling (Redis): blocks after the failure limit,
  success resets, and it fails open when Redis is unreachable (tests that
  need a live Redis skip honestly when none is running)
"""

from __future__ import annotations

import base64
import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_DNS, uuid4, uuid5

import jwt as pyjwt
import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from qa_copilot_api import auth, throttle
from qa_copilot_api.config import Settings
from qa_copilot_api.main import create_app
from qa_copilot_domain.enums import ProjectRole
from qa_copilot_repository import db, models
from sqlalchemy import select

REPO_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = REPO_ROOT / "alembic.ini"
TEST_DB = "qa_copilot_auth_test"
TEST_URL = f"postgresql+psycopg://qa:qa@localhost:5433/{TEST_DB}"
ADMIN_URL = "postgresql+psycopg://qa:qa@localhost:5433/postgres"

SECRET = "test-secret-0123456789abcdef"  # 16+ chars, test-only
PASSWORD = "correct-horse-battery-staple"
# S8.1 policy (10+ chars, letter + digit) â€” required by register / change-password.
STRONG_PASSWORD = "correct-horse-battery-9staple"

# ids are Postgres UUIDs â€” deterministic values, stable across runs
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


# --- S8.1: throttle stubs + live-Redis helpers ----------------------------------


class _OpenThrottler:
    """Permissive stand-in for ``throttle.LoginThrottler`` (no Redis).

    Installed by the ``env`` fixture so the HTTP tests stay deterministic:
    never blocks, never counts, never needs a live Redis (and never pollutes
    shared counters). The throttle behaviour itself is covered by the
    dedicated tests at the bottom of this file.
    """

    def check(self, email: str, ip: str) -> throttle.ThrottleDecision:
        return throttle.ThrottleDecision(False, 0)

    def record_failure(self, email: str, ip: str) -> None:
        return None

    def reset(self, email: str, ip: str) -> None:
        return None

    def close(self) -> None:
        return None


class _BlockedThrottler:
    """``LoginThrottler`` stand-in that always blocks (deterministic 429).

    ``record_failure`` / ``reset`` must never run while blocked (no password
    check, no timing signal) â€” if the route reaches them the test fails loud.
    """

    def __init__(self, retry_after_s: int = 42) -> None:
        self._retry_after_s = retry_after_s
        self.checked: list[tuple[str, str]] = []

    def check(self, email: str, ip: str) -> throttle.ThrottleDecision:
        self.checked.append((email, ip))
        return throttle.ThrottleDecision(True, self._retry_after_s)

    def record_failure(self, email: str, ip: str) -> None:
        raise AssertionError("password check ran while throttled")

    def reset(self, email: str, ip: str) -> None:
        raise AssertionError("throttle reset ran while throttled")

    def close(self) -> None:
        return None


#: A URL where nothing listens (localhost port 1) â€” exercises the fail-open path.
DEAD_REDIS_URL = "redis://127.0.0.1:1/0"


def _live_redis_url() -> str:
    """The URL the app's throttler would use (same resolution as ``main``)."""
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    return settings.redis_url or "redis://localhost:6379/0"


def _redis_up(url: str) -> bool:
    """True when a Redis answers ``PING`` at *url* (skip live tests otherwise)."""
    from redis import Redis

    client = Redis.from_url(url, socket_connect_timeout=1, socket_timeout=1)
    try:
        return bool(client.ping())
    except Exception:
        return False
    finally:
        client.close()


#: Live-Redis tests skip honestly when no Redis is running (local-first, Â§19).
REQUIRES_REDIS = pytest.mark.skipif(
    not _redis_up(_live_redis_url()), reason="live Redis unavailable"
)


def _real_throttler(settings: Settings) -> throttle.LoginThrottler:
    """The same ``LoginThrottler`` construction ``main.py`` uses (S8.1)."""
    return throttle.LoginThrottler(
        settings.redis_url or "redis://localhost:6379/0",
        max_failures=settings.login_throttle_max_failures,
        window_s=settings.login_throttle_window_s,
    )


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
        # tests from reading the dev .env) â€” mypy can't see it in the stubs.
        settings=Settings(  # type: ignore[call-arg]
            database_url=TEST_URL, auth_token_secret=SECRET, _env_file=None
        )
    )

    # S8.1: deterministic login throttling â€” swap the app's Redis-backed
    # throttler for a permissive stub so the HTTP tests never depend on (or
    # pollute) live Redis state; the dedicated throttle tests below install
    # their own stubs / real throttlers on demand.
    app.state.login_throttler = _OpenThrottler()

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
    # S8.1: bob has no organization membership in the seed data â†’ empty list
    assert body["organizations"] == []


# --- RBAC (project-scoped, Â§31.3) ----------------------------------------------


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
    # dave owns beta but is not a member of acme â†’ 403 (no existence leak either)
    response = client.get(f"/api/v1/projects/{ACME_ID}", headers=_auth_header("dave"))
    assert response.status_code == 403
    response = client.get(f"/api/v1/projects/{GHOST_ID}", headers=_auth_header("dave"))
    assert response.status_code == 403


def test_project_delete_requires_owner(client: TestClient) -> None:
    # member and viewer may not delete (Â§31.3: owner-only)
    for user in ("bob", "carol"):
        response = client.delete(f"/api/v1/projects/{ACME_ID}", headers=_auth_header(user))
        assert response.status_code == 403, (user, response.text)
    # non-member may not delete either
    response = client.delete(f"/api/v1/projects/{ACME_ID}", headers=_auth_header("dave"))
    assert response.status_code == 403
    # owner deletes â†’ 204, project is gone, its memberships with it
    response = client.delete(f"/api/v1/projects/{ACME_ID}", headers=_auth_header("alice"))
    assert response.status_code == 204
    # alice lost her membership â†’ 403 (not 404: non-members get no existence leak)
    assert (
        client.get(f"/api/v1/projects/{ACME_ID}", headers=_auth_header("alice")).status_code == 403
    )
    assert client.get("/api/v1/projects", headers=_auth_header("bob")).json() == []


def test_project_delete_requires_auth(client: TestClient) -> None:
    assert client.delete(f"/api/v1/projects/{ACME_ID}").status_code == 401


# --- fail loud -----------------------------------------------------------------


def test_missing_secret_fails_loud(monkeypatch: pytest.MonkeyPatch) -> None:
    """No ``AUTH_TOKEN_SECRET`` â†’ 500; there must be no fallback secret."""
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


# --- S8.1: register (account + owned workspace, Â§19) ----------------------------


def test_register_creates_account_and_owned_organization(client: TestClient) -> None:
    """One signup creates the user and a new organization the user owns."""
    response = client.post(
        "/api/v1/auth/register",
        json={
            "email": "new@local.dev",
            "password": STRONG_PASSWORD,
            "organization_name": "New Org",
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["user"]["email"] == "new@local.dev"
    assert body["user"]["role"] == "owner"
    assert body["organization"]["name"] == "New Org"
    assert body["organization"]["role"] == "owner"
    assert body["projects"] == []
    # the new account sees its owned workspace in /auth/me (S8.1)
    login = client.post(
        "/api/v1/auth/login", json={"email": "new@local.dev", "password": STRONG_PASSWORD}
    )
    assert login.status_code == 200, login.text
    token = login.json()["token"]
    me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200, me.text
    me_body = me.json()
    assert me_body["organizations"] == [
        {"id": body["organization"]["id"], "name": "New Org", "role": "owner"}
    ]
    assert me_body["projects"] == []


def test_register_default_organization_name(client: TestClient) -> None:
    response = client.post(
        "/api/v1/auth/register", json={"email": "new@local.dev", "password": STRONG_PASSWORD}
    )
    assert response.status_code == 201, response.text
    assert response.json()["organization"]["name"] == "new's workspace"


def test_register_rejects_duplicate_email(client: TestClient) -> None:
    # an already-seeded emailâ€¦
    response = client.post(
        "/api/v1/auth/register",
        json={"email": "alice@local.dev", "password": PASSWORD},
    )
    assert response.status_code == 409
    # â€¦and a freshly registered one (same email â†’ one account)
    client.post(
        "/api/v1/auth/register", json={"email": "new@local.dev", "password": STRONG_PASSWORD}
    )
    response = client.post(
        "/api/v1/auth/register",
        json={"email": "new@local.dev", "password": STRONG_PASSWORD},
    )
    assert response.status_code == 409


def test_register_rejects_weak_password(client: TestClient) -> None:
    for weak in ("short", "alllettershere", "1234567890"):
        response = client.post(
            "/api/v1/auth/register", json={"email": "new@local.dev", "password": weak}
        )
        assert response.status_code == 422, (weak, response.text)
        assert "password must" in response.json()["detail"]


def test_register_rejects_bad_email(client: TestClient) -> None:
    response = client.post(
        "/api/v1/auth/register", json={"email": "not-an-email", "password": PASSWORD}
    )
    assert response.status_code == 422


# --- S8.1: rotating refresh tokens (opaque, stored hashed, Â§17) -----------------


def test_login_issues_rotating_refresh_token(client: TestClient) -> None:
    response = client.post(
        "/api/v1/auth/login", json={"email": "alice@local.dev", "password": PASSWORD}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert isinstance(body["refresh_token"], str) and body["refresh_token"]
    assert body["refresh_expires_in"] == int(auth.REFRESH_TTL.total_seconds())
    # the token is opaque, not a JWT (a JWT would contain dots)
    assert "." not in body["refresh_token"]


def test_refresh_rotates_and_new_access_token_works(client: TestClient) -> None:
    login = client.post(
        "/api/v1/auth/login", json={"email": "alice@local.dev", "password": PASSWORD}
    )
    assert login.status_code == 200, login.text
    first = login.json()["refresh_token"]
    response = client.post("/api/v1/auth/refresh", json={"refresh_token": first})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["refresh_token"] != first
    # the successor's access token works against /auth/me
    me = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {body['token']}"},
    )
    assert me.status_code == 200, me.text
    assert me.json()["user"]["email"] == "alice@local.dev"


def test_refresh_reuse_revokes_whole_family(client: TestClient) -> None:
    login = client.post(
        "/api/v1/auth/login", json={"email": "alice@local.dev", "password": PASSWORD}
    )
    assert login.status_code == 200, login.text
    first = login.json()["refresh_token"]
    # rotate: the presented token dies, a successor lives
    second = client.post("/api/v1/auth/refresh", json={"refresh_token": first})
    assert second.status_code == 200, second.text
    successor = second.json()["refresh_token"]
    # reuse of the rotated token â†’ 401 â€¦
    response = client.post("/api/v1/auth/refresh", json={"refresh_token": first})
    assert response.status_code == 401, response.text
    # â€¦and the whole token family is revoked â€” the successor is dead too
    response = client.post("/api/v1/auth/refresh", json={"refresh_token": successor})
    assert response.status_code == 401, response.text


def test_refresh_rejects_unknown_token(client: TestClient) -> None:
    response = client.post("/api/v1/auth/refresh", json={"refresh_token": "not-a-token"})
    assert response.status_code == 401


# --- S8.1: change password (re-auth + full token revocation, Â§17) ---------------


def test_change_password_success_reauth_and_revoke(client: TestClient) -> None:
    login = client.post(
        "/api/v1/auth/login", json={"email": "alice@local.dev", "password": PASSWORD}
    )
    assert login.status_code == 200, login.text
    refresh_token = login.json()["refresh_token"]
    headers = {"Authorization": f"Bearer {login.json()['token']}"}
    new_password = "new-password-12345"
    response = client.post(
        "/api/v1/auth/change-password",
        json={"current_password": PASSWORD, "new_password": new_password},
        headers=headers,
    )
    assert response.status_code == 204, response.text
    # old password is dead, new one works
    assert (
        client.post(
            "/api/v1/auth/login", json={"email": "alice@local.dev", "password": PASSWORD}
        ).status_code
        == 401
    )
    assert (
        client.post(
            "/api/v1/auth/login",
            json={"email": "alice@local.dev", "password": new_password},
        ).status_code
        == 200
    )
    # every active refresh token was revoked (other sessions are out, Â§17)
    response = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token})
    assert response.status_code == 401


def test_change_password_wrong_current_password(client: TestClient) -> None:
    login = client.post(
        "/api/v1/auth/login", json={"email": "alice@local.dev", "password": PASSWORD}
    )
    assert login.status_code == 200, login.text
    refresh_token = login.json()["refresh_token"]
    headers = {"Authorization": f"Bearer {login.json()['token']}"}
    response = client.post(
        "/api/v1/auth/change-password",
        json={"current_password": "wrong-current", "new_password": "new-password-12345"},
        headers=headers,
    )
    assert response.status_code == 401, response.text
    # nothing changed: the old refresh token still rotatesâ€¦
    response = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token})
    assert response.status_code == 200, response.text
    # â€¦and the password is still the old one
    assert (
        client.post(
            "/api/v1/auth/login",
            json={"email": "alice@local.dev", "password": "new-password-12345"},
        ).status_code
        == 401
    )


def test_change_password_weak_new_password(client: TestClient) -> None:
    login = client.post(
        "/api/v1/auth/login", json={"email": "alice@local.dev", "password": PASSWORD}
    )
    assert login.status_code == 200, login.text
    headers = {"Authorization": f"Bearer {login.json()['token']}"}
    response = client.post(
        "/api/v1/auth/change-password",
        json={"current_password": PASSWORD, "new_password": "weak"},
        headers=headers,
    )
    assert response.status_code == 422, response.text
    assert "new password must" in response.json()["detail"]


def test_change_password_requires_auth(client: TestClient) -> None:
    response = client.post(
        "/api/v1/auth/change-password",
        json={"current_password": PASSWORD, "new_password": "new-password-12345"},
    )
    assert response.status_code == 401


# --- S8.2/S8.3: account self-delete (PII purge, §17) ------------------------------


def test_account_delete_requires_auth(client: TestClient) -> None:
    """Unauthenticated self-delete is 401 (no account to delete, no leak)."""
    assert client.delete("/api/v1/auth/account").status_code == 401
    assert (
        client.delete(
            "/api/v1/auth/account", headers={"Authorization": "Bearer garbage"}
        ).status_code
        == 401
    )


def test_account_delete_purges_account_and_keeps_history(
    client: TestClient, env: dict[str, Any]
) -> None:
    """Self-delete kills the user (PII), memberships and tokens; sessions stay.

    §17: the ``users`` row (email + password hash) is the PII — it goes.
    With it cascade the memberships and refresh tokens (no token can resolve
    to a missing user). ``ai_sessions.user_id`` has no ON DELETE rule, so
    the session row survives project-scoped with ``user_id`` nulled — the
    AI history outlives the person.
    """
    login = client.post("/api/v1/auth/login", json={"email": EMAILS["dave"], "password": PASSWORD})
    assert login.status_code == 200, login.text
    access = f"Bearer {login.json()['token']}"
    refresh_token = login.json()["refresh_token"]
    # dave's AI session on BETA must survive the self-delete
    with db.make_session_factory(env["engine"])() as session:
        session.add(models.AISession(project_id=BETA_ID, user_id=DAVE_ID, task_type="regression"))
        session.commit()

    response = client.delete("/api/v1/auth/account", headers={"Authorization": access})
    assert response.status_code == 204, response.text

    # the access token can no longer resolve to a user…
    assert client.get("/api/v1/auth/me", headers={"Authorization": access}).status_code == 401
    # …the refresh token is gone (cascade) and login is dead (hash purged)
    assert (
        client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token}).status_code
        == 401
    )
    assert (
        client.post(
            "/api/v1/auth/login", json={"email": EMAILS["dave"], "password": PASSWORD}
        ).status_code
        == 401
    )
    # DB: user + project membership gone…
    with db.make_session_factory(env["engine"])() as session:
        assert session.get(models.User, DAVE_ID) is None
        assert (
            session.scalar(
                select(models.ProjectMember).where(models.ProjectMember.user_id == DAVE_ID)
            )
            is None
        )
        # …the session row survives with user_id nulled…
        session_row = session.scalar(
            select(models.AISession).where(models.AISession.project_id == BETA_ID)
        )
        assert session_row is not None and session_row.user_id is None
        # …and the org + project it belonged to are untouched
        assert session.get(models.Organization, ORG_ID) is not None
        assert session.get(models.Project, BETA_ID) is not None


# --- S8.1: login brute-force throttling (Redis, Â§19) ----------------------------


def test_throttle_fails_open_when_redis_unreachable() -> None:
    """No Redis â†’ no 429, no exception: availability wins (local-first)."""
    throttler = throttle.LoginThrottler(DEAD_REDIS_URL, max_failures=1, window_s=10)
    try:
        assert not throttler.check("a@b.c", "192.0.2.1").blocked
        throttler.record_failure("a@b.c", "192.0.2.1")
        assert not throttler.check("a@b.c", "192.0.2.1").blocked
        throttler.reset("a@b.c", "192.0.2.1")
    finally:
        throttler.close()


@REQUIRES_REDIS
def test_throttle_counters_block_then_reset() -> None:
    """Failure counter: unblocked â†’ blocked after the limit â†’ unblocked on reset."""
    email = f"throttle-{uuid4().hex}@local.dev"
    ip = "192.0.2.77"  # TEST-NET-1: never a real client address
    throttler = throttle.LoginThrottler(_live_redis_url(), max_failures=3, window_s=30)
    try:
        assert not throttler.check(email, ip).blocked
        for _ in range(3):
            throttler.record_failure(email, ip)
        decision = throttler.check(email, ip)
        assert decision.blocked
        assert 0 < decision.retry_after_s <= 30
        throttler.reset(email, ip)
        assert not throttler.check(email, ip).blocked
    finally:
        throttler.reset(email, ip)
        throttler.close()


def test_login_blocked_answers_429_with_retry_after(
    client: TestClient, env: dict[str, Any]
) -> None:
    """A blocked email gets 429 + Retry-After â€” even with the correct password."""
    app = env["app"]
    stub = _BlockedThrottler(retry_after_s=42)
    app.state.login_throttler = stub
    response = client.post(
        "/api/v1/auth/login",
        json={"email": "alice@local.dev", "password": PASSWORD},
        headers={"x-forwarded-for": "192.0.2.9"},
    )
    assert response.status_code == 429, response.text
    assert response.headers["retry-after"] == "42"
    assert "too many failed login attempts" in response.json()["detail"]
    # the check saw the email and the (X-Forwarded-For) client IPâ€¦
    assert stub.checked == [("alice@local.dev", "192.0.2.9")]
    # â€¦and the password was never checked (record_failure / reset never ran)


def test_login_fails_open_when_redis_is_down(client: TestClient, env: dict[str, Any]) -> None:
    """Redis down â†’ logins still answer 401/200, never 429 or 500."""
    app = env["app"]
    # max_failures=1: a single failure would block if the counters worked at all
    app.state.login_throttler = throttle.LoginThrottler(DEAD_REDIS_URL, max_failures=1)
    body = {"email": "alice@local.dev"}
    response = client.post("/api/v1/auth/login", json={**body, "password": "wrong"})
    assert response.status_code == 401, response.text
    response = client.post("/api/v1/auth/login", json={**body, "password": "wrong"})
    assert response.status_code == 401, response.text
    response = client.post("/api/v1/auth/login", json={**body, "password": PASSWORD})
    assert response.status_code == 200, response.text


@REQUIRES_REDIS
def test_login_blocked_after_max_failures(client: TestClient, env: dict[str, Any]) -> None:
    """Live Redis: the (max_failures + 1)-th login attempt is throttled (429)."""
    app = env["app"]
    settings: Settings = app.state.settings
    app.state.login_throttler = _real_throttler(settings)
    email = f"block-{uuid4().hex}@local.dev"
    assert (
        client.post(
            "/api/v1/auth/register", json={"email": email, "password": STRONG_PASSWORD}
        ).status_code
        == 201
    )
    headers = {"x-forwarded-for": "192.0.2.51"}
    max_failures = settings.login_throttle_max_failures
    for _ in range(max_failures):
        response = client.post(
            "/api/v1/auth/login", json={"email": email, "password": "wrong"}, headers=headers
        )
        assert response.status_code == 401, response.text
    # limit reached â†’ blocked before any password check
    response = client.post(
        "/api/v1/auth/login", json={"email": email, "password": STRONG_PASSWORD}, headers=headers
    )
    assert response.status_code == 429, response.text
    retry_after = int(response.headers["retry-after"])
    assert 0 < retry_after <= settings.login_throttle_window_s
    # a reset unblocks again (proves the block came from the counter)
    app.state.login_throttler.reset(email, "192.0.2.51")
    response = client.post(
        "/api/v1/auth/login", json={"email": email, "password": STRONG_PASSWORD}, headers=headers
    )
    assert response.status_code == 200, response.text


@REQUIRES_REDIS
def test_successful_login_resets_failure_counter(client: TestClient, env: dict[str, Any]) -> None:
    """Live Redis: a successful login clears the failure counters (S8.1, Â§19)."""
    app = env["app"]
    settings: Settings = app.state.settings
    app.state.login_throttler = _real_throttler(settings)
    email = f"reset-{uuid4().hex}@local.dev"
    assert (
        client.post(
            "/api/v1/auth/register", json={"email": email, "password": STRONG_PASSWORD}
        ).status_code
        == 201
    )
    headers = {"x-forwarded-for": "192.0.2.52"}
    max_failures = settings.login_throttle_max_failures
    # (max_failures âˆ’ 1) failures, then a success resets the counterâ€¦
    for _ in range(max_failures - 1):
        response = client.post(
            "/api/v1/auth/login", json={"email": email, "password": "wrong"}, headers=headers
        )
        assert response.status_code == 401, response.text
    response = client.post(
        "/api/v1/auth/login", json={"email": email, "password": STRONG_PASSWORD}, headers=headers
    )
    assert response.status_code == 200, response.text
    # â€¦so (max_failures âˆ’ 1) more failures must not block (counter restarted)
    for _ in range(max_failures - 1):
        response = client.post(
            "/api/v1/auth/login", json={"email": email, "password": "wrong"}, headers=headers
        )
        assert response.status_code == 401, response.text
    response = client.post(
        "/api/v1/auth/login", json={"email": email, "password": STRONG_PASSWORD}, headers=headers
    )
    assert response.status_code == 200, response.text
