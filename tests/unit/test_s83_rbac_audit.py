"""S8.3 RBAC hardening + append-only audit trail tests (build bible §19 S8.3, §17).

Covers the owner-gated org surface and the ``audit_log`` contract:

- ``DELETE /api/v1/organizations/{id}``      — org **owner**, current-password
  re-authentication (wrong password → 401, audited ``org.delete.reauth_failure``)
- ``GET    /api/v1/organizations/{id}/audit`` — org **owner** only; newest
  first, capped at 200 rows
- role matrix: owner / member / outsider / unauthenticated on both routes —
  member and outsider get 403 (audited ``org.gate.denied``, target = org id,
  outcome ``denied``); unauthenticated gets 401
- invite create / accept: success rows (actor + org target) and failure rows
  (no target — the org id is unknown by design)
- append-only (§17): audit rows outlive **both** the org deletion (``target``
  is an opaque id) and the actor's own account deletion (``actor_id`` is
  ``ON DELETE SET NULL``)

Same scratch-DB pattern as ``test_s82_teams.py`` (dedicated database, skipped
when no Postgres is reachable — ``pytest`` stays green without docker).
"""

from __future__ import annotations

import os
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
from qa_copilot_domain.enums import AuditAction, AuditOutcome, OrgRole
from qa_copilot_repository import db, models
from sqlalchemy import select

REPO_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = REPO_ROOT / "alembic.ini"
TEST_DB = "qa_copilot_s83_test"
TEST_URL = f"postgresql+psycopg://qa:qa@localhost:5433/{TEST_DB}"
ADMIN_URL = "postgresql+psycopg://qa:qa@localhost:5433/postgres"

SECRET = "test-secret-0123456789abcdef"  # 16+ chars, test-only
PASSWORD = "correct-horse-battery-staple"  # S8.3: org deletion re-auth

# ids are Postgres UUIDs — deterministic values, stable across runs
NS = NAMESPACE_DNS
ACME_ID = str(uuid5(NS, "org-acme"))
BETA_ID = str(uuid5(NS, "org-beta"))  # carol's org; carol is NOT in acme
ACME_PROJECT_ID = str(uuid5(NS, "acme-store"))
ALICE_ID = str(uuid5(NS, "user-alice"))
BOB_ID = str(uuid5(NS, "user-bob"))
CAROL_ID = str(uuid5(NS, "user-carol"))
DAVE_ID = str(uuid5(NS, "user-dave"))

EMAILS = {
    "alice": "alice@local.dev",  # owner of acme
    "bob": "bob@local.dev",  # member of acme
    "carol": "carol@local.dev",  # NOT a member of acme (owner of beta)
    "dave": "dave@local.dev",  # no org yet; invitee
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


def _org_url(org_id: str, sub: str = "") -> str:
    return f"/api/v1/organizations/{org_id}{sub}"


@pytest.fixture()
def env() -> Iterator[dict[str, Any]]:
    """Scratch database + alembic + a small org/project fixture."""
    saved_url = os.environ.get("DATABASE_URL")

    def _probe() -> bool:
        try:
            _admin("SELECT 1")
            return True
        except Exception:
            return False

    if not _probe():
        pytest.skip("no Postgres reachable — S8.3 API tests need a database")

    _admin(f"DROP DATABASE IF EXISTS {TEST_DB}")
    _admin(f"CREATE DATABASE {TEST_DB}")

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
                    # S8.3: org deletion requires a password re-auth
                    password_hash=auth.hash_password(PASSWORD),
                )
            )
        session.add_all(
            [
                models.Organization(id=ACME_ID, name="Acme Inc"),
                models.Organization(id=BETA_ID, name="Beta Inc"),
            ]
        )
        session.add(models.Project(id=ACME_PROJECT_ID, organization_id=ACME_ID, name="Acme Store"))
        session.flush()
        # acme membership: alice owner, bob member; carol owns beta (outsider);
        # dave belongs to no org (the invitee)
        session.add_all(
            [
                models.OrganizationMember(
                    organization_id=ACME_ID, user_id=ALICE_ID, role=OrgRole.OWNER
                ),
                models.OrganizationMember(
                    organization_id=ACME_ID, user_id=BOB_ID, role=OrgRole.MEMBER
                ),
                models.OrganizationMember(
                    organization_id=BETA_ID, user_id=CAROL_ID, role=OrgRole.OWNER
                ),
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


def _audit_rows(
    engine: Any,
    *,
    target: str | None = None,
    action: AuditAction | None = None,
) -> list[models.AuditLog]:
    """Read ``audit_log`` rows (newest first) — the append-only surface."""
    stmt = select(models.AuditLog).order_by(models.AuditLog.at.desc(), models.AuditLog.id.desc())
    if target is not None:
        stmt = stmt.where(models.AuditLog.target == target)
    if action is not None:
        stmt = stmt.where(models.AuditLog.action == action)
    with db.make_session_factory(engine)() as session:
        return list(session.scalars(stmt))


def _live_tokens(engine: Any, user_id: str) -> int:
    """Unrevoked refresh tokens for *user_id*."""
    with db.make_session_factory(engine)() as session:
        return len(
            session.scalars(
                select(models.UserRefreshToken).where(
                    models.UserRefreshToken.user_id == user_id,
                    models.UserRefreshToken.revoked_at.is_(None),
                )
            ).all()
        )


def _create_invite(client: TestClient, org_id: str, who: str, email: str) -> dict[str, Any]:
    """Owner creates an invite — 201 + the one-time ``code``."""
    resp = client.post(
        _org_url(org_id, "/invites"),
        json={"email": email, "role": "member"},
        headers=_auth_header(who),
    )
    assert resp.status_code == 201, resp.text
    invite: dict[str, Any] = resp.json()
    return invite


# --- DELETE /organizations/{id}: role matrix ------------------------------------


def test_delete_org_role_matrix(client: TestClient, env: dict[str, Any]) -> None:
    """Owner-gated: member → 403, outsider → 403, unauthenticated → 401.

    Both 403s are audited ``org.gate.denied`` (target = org id, outcome
    ``denied``); the org is untouched.
    """
    # member: the *correct* password does not help — the role is checked first
    bob = client.request(
        "DELETE",
        _org_url(ACME_ID),
        headers=_auth_header("bob"),
        json={"current_password": PASSWORD},
    )
    assert bob.status_code == 403
    assert "owner" in bob.json()["detail"]

    # outsider: same 403 shape as the member — no existence leak
    carol = client.request(
        "DELETE",
        _org_url(ACME_ID),
        headers=_auth_header("carol"),
        json={"current_password": PASSWORD},
    )
    assert carol.status_code == 403
    assert "not a member" in carol.json()["detail"]

    # unauthenticated: no user → 401, and nothing to attribute a row to
    anon = client.request("DELETE", _org_url(ACME_ID), json={"current_password": PASSWORD})
    assert anon.status_code == 401

    # the org is untouched
    members = client.get(_org_url(ACME_ID, "/members"), headers=_auth_header("bob"))
    assert members.status_code == 200

    # exactly two gate-denial rows — bob's and carol's
    denied = _audit_rows(env["engine"], action=AuditAction.ORG_GATE_DENIED, target=ACME_ID)
    assert len(denied) == 2
    assert {row.actor_id for row in denied} == {BOB_ID, CAROL_ID}
    assert all(row.outcome == AuditOutcome.DENIED for row in denied)


# --- DELETE /organizations/{id}: re-authentication -------------------------------


def test_delete_org_reauth_failure(client: TestClient, env: dict[str, Any]) -> None:
    """Wrong password → 401 + ``org.delete.reauth_failure`` — nothing deleted.

    RBAC also precedes body validation: a member with a missing body gets the
    role 403, not a 422; the owner with a missing body gets the 422.
    """
    # owner + missing body → 422 (RBAC passes, the body is required)
    owner_nobody = client.request("DELETE", _org_url(ACME_ID), headers=_auth_header("alice"))
    assert owner_nobody.status_code == 422
    # member + missing body → the 403 wins (RBAC before body validation)
    member_nobody = client.request("DELETE", _org_url(ACME_ID), headers=_auth_header("bob"))
    assert member_nobody.status_code == 403

    # wrong password → 401, audited, and nothing happens
    res = client.request(
        "DELETE",
        _org_url(ACME_ID),
        headers=_auth_header("alice"),
        json={"current_password": "wrong-password-123"},
    )
    assert res.status_code == 401
    assert res.json()["detail"] == "current password is incorrect"

    rows = _audit_rows(env["engine"], action=AuditAction.ORG_DELETE_REAUTH_FAILURE)
    assert len(rows) == 1
    assert rows[0].actor_id == ALICE_ID
    assert rows[0].target == ACME_ID
    assert rows[0].outcome == AuditOutcome.FAILURE

    # the org and its members are untouched; no success row exists
    members = client.get(_org_url(ACME_ID, "/members"), headers=_auth_header("alice"))
    assert members.status_code == 200
    assert _audit_rows(env["engine"], action=AuditAction.ORG_DELETE) == []


def test_delete_org_success_reauth(client: TestClient, env: dict[str, Any]) -> None:
    """Correct password → 204: cascade, token revocation, ``org.delete`` row."""
    # bob (member) and dave (no org) each hold a live refresh token
    for who in ("bob", "dave"):
        login = client.post("/api/v1/auth/login", json={"email": EMAILS[who], "password": PASSWORD})
        assert login.status_code == 200, login.text
    assert _live_tokens(env["engine"], BOB_ID) >= 1
    assert _live_tokens(env["engine"], DAVE_ID) >= 1

    res = client.request(
        "DELETE",
        _org_url(ACME_ID),
        headers=_auth_header("alice"),
        json={"current_password": PASSWORD},
    )
    assert res.status_code == 204

    # the org is gone — the membership gate runs first: 403, never a 404
    gone_alice = client.get(_org_url(ACME_ID, "/members"), headers=_auth_header("alice"))
    gone_bob = client.get(_org_url(ACME_ID, "/members"), headers=_auth_header("bob"))
    assert gone_alice.status_code == 403
    assert gone_bob.status_code == 403

    # the success row committed with the cascade
    rows = _audit_rows(env["engine"], action=AuditAction.ORG_DELETE)
    assert len(rows) == 1
    assert rows[0].actor_id == ALICE_ID
    assert rows[0].target == ACME_ID
    assert rows[0].outcome == AuditOutcome.SUCCESS

    # the member's refresh tokens are dead; dave (not in acme) is untouched
    assert _live_tokens(env["engine"], BOB_ID) == 0
    assert _live_tokens(env["engine"], DAVE_ID) >= 1

    # the users themselves survive — bob can still log in, now without orgs
    relogin = client.post("/api/v1/auth/login", json={"email": EMAILS["bob"], "password": PASSWORD})
    assert relogin.status_code == 200
    me = client.get("/api/v1/auth/me", headers=_auth_header("bob")).json()
    assert [org["id"] for org in me["organizations"]] == []


# --- GET /organizations/{id}/audit: owner-only export ----------------------------


def test_org_audit_export_owner_only_newest_first(client: TestClient, env: dict[str, Any]) -> None:
    """Owner reads the trail (newest first); member/outsider 403, anon 401."""
    # seed three rows, one per request (each request is its own transaction,
    # so ``at`` is strictly increasing — newest-first == reverse seed order)
    invite = _create_invite(client, ACME_ID, "alice", EMAILS["dave"])
    accept = client.post(f"/api/v1/invites/{invite['code']}/accept", headers=_auth_header("dave"))
    assert accept.status_code == 200, accept.text
    denied = client.request(
        "DELETE",
        _org_url(ACME_ID),
        headers=_auth_header("bob"),
        json={"current_password": PASSWORD},
    )
    assert denied.status_code == 403

    resp = client.get(_org_url(ACME_ID, "/audit"), headers=_auth_header("alice"))
    assert resp.status_code == 200
    body = resp.json()
    assert [row["action"] for row in body] == [
        AuditAction.ORG_GATE_DENIED,
        AuditAction.ORG_INVITE_ACCEPT,
        AuditAction.ORG_INVITE_CREATE,
    ]
    assert all(row["target"] == ACME_ID for row in body)
    by_action = {row["action"]: row for row in body}
    assert by_action[AuditAction.ORG_GATE_DENIED]["actor_id"] == BOB_ID
    assert by_action[AuditAction.ORG_INVITE_ACCEPT]["actor_id"] == DAVE_ID
    assert by_action[AuditAction.ORG_INVITE_CREATE]["actor_id"] == ALICE_ID

    # the trail is never exposed to members or outsiders
    member_read = client.get(_org_url(ACME_ID, "/audit"), headers=_auth_header("bob"))
    outsider_read = client.get(_org_url(ACME_ID, "/audit"), headers=_auth_header("carol"))
    anon_read = client.get(_org_url(ACME_ID, "/audit"))
    assert member_read.status_code == 403
    assert outsider_read.status_code == 403
    assert anon_read.status_code == 401


def test_org_audit_export_capped_at_200(client: TestClient, env: dict[str, Any]) -> None:
    """More than 200 rows → exactly the 200 newest, newest first."""
    actions = [
        AuditAction.ORG_MEMBERSHIP_ADD,
        AuditAction.ORG_INVITE_CREATE,
        AuditAction.ORG_INVITE_ACCEPT,
    ]
    base = datetime(2026, 9, 10, 12, 0, 0, tzinfo=UTC)
    with db.make_session_factory(env["engine"])() as session:
        session.add_all(
            [
                models.AuditLog(
                    actor_id=ALICE_ID,
                    action=actions[i % len(actions)],
                    target=ACME_ID,
                    outcome=AuditOutcome.SUCCESS,
                    ip=None,
                    at=base + timedelta(seconds=i),
                )
                for i in range(210)
            ]
        )
        session.commit()

    resp = client.get(_org_url(ACME_ID, "/audit"), headers=_auth_header("alice"))
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 200
    # newest first: the last-seeded row is first, row #10 is the oldest kept
    assert body[0]["action"] == actions[209 % len(actions)]
    assert body[-1]["action"] == actions[10 % len(actions)]
    ats = [row["at"] for row in body]
    assert ats == sorted(ats, reverse=True)


# --- invite create / accept: audit rows -----------------------------------------


def test_invite_create_and_accept_audit_rows(client: TestClient, env: dict[str, Any]) -> None:
    """Invite create/accept: success rows carry the org; failures carry none."""
    # create → success row (actor = the owner, target = the org)
    invite = _create_invite(client, ACME_ID, "alice", EMAILS["dave"])
    created = _audit_rows(env["engine"], action=AuditAction.ORG_INVITE_CREATE)
    assert len(created) == 1
    assert created[0].actor_id == ALICE_ID
    assert created[0].target == ACME_ID
    assert created[0].outcome == AuditOutcome.SUCCESS

    # accept → success row (actor = the invitee, target = the joined org)
    resp = client.post(f"/api/v1/invites/{invite['code']}/accept", headers=_auth_header("dave"))
    assert resp.status_code == 200, resp.text
    accepted = _audit_rows(env["engine"], action=AuditAction.ORG_INVITE_ACCEPT, target=ACME_ID)
    assert len(accepted) == 1
    assert accepted[0].actor_id == DAVE_ID
    assert accepted[0].outcome == AuditOutcome.SUCCESS

    # reused code (→ 404) and bogus code (→ 404): failure rows with NO target —
    # the org id is unknown by design, and never guessed
    reused = client.post(f"/api/v1/invites/{invite['code']}/accept", headers=_auth_header("carol"))
    bogus = client.post("/api/v1/invites/not-a-real-code/accept", headers=_auth_header("carol"))
    assert reused.status_code == 404
    assert bogus.status_code == 404
    failures = [
        row
        for row in _audit_rows(env["engine"], action=AuditAction.ORG_INVITE_ACCEPT)
        if row.outcome == AuditOutcome.FAILURE
    ]
    assert len(failures) == 2
    assert all(row.target is None and row.actor_id == CAROL_ID for row in failures)


# --- append-only: rows outlive org AND actor deletion ----------------------------


def test_audit_rows_outlive_org_and_actor_deletion(client: TestClient, env: dict[str, Any]) -> None:
    """§17: the trail survives the org deletion and both actors' self-deletes.

    ``target`` is an opaque id (survives the org row's death); ``actor_id``
    is ``ON DELETE SET NULL`` (survives the user row's death, nulled).
    """
    # seed rows attributable to both actors, targeting the org
    _create_invite(client, ACME_ID, "alice", EMAILS["dave"])  # invite.create — alice
    member_denied = client.request(
        "DELETE",
        _org_url(ACME_ID),
        headers=_auth_header("bob"),
        json={"current_password": PASSWORD},
    )
    assert member_denied.status_code == 403  # gate.denied — bob
    deleted = client.request(
        "DELETE",
        _org_url(ACME_ID),
        headers=_auth_header("alice"),
        json={"current_password": PASSWORD},
    )
    assert deleted.status_code == 204  # org.delete — alice

    # both actors then delete their own accounts
    alice_gone = client.request("DELETE", "/api/v1/auth/account", headers=_auth_header("alice"))
    bob_gone = client.request("DELETE", "/api/v1/auth/account", headers=_auth_header("bob"))
    assert alice_gone.status_code == 204
    assert bob_gone.status_code == 204

    # every org row survives: action/target/outcome intact, actor_id nulled
    rows = _audit_rows(env["engine"], target=ACME_ID)
    by_action = {row.action: row for row in rows}
    assert set(by_action) >= {
        AuditAction.ORG_INVITE_CREATE,
        AuditAction.ORG_GATE_DENIED,
        AuditAction.ORG_DELETE,
    }
    assert all(row.actor_id is None for row in rows)
    assert by_action[AuditAction.ORG_DELETE].outcome == AuditOutcome.SUCCESS
    assert by_action[AuditAction.ORG_GATE_DENIED].outcome == AuditOutcome.DENIED

    # the self-deletions are audited too — and survive their own authors
    deletes = _audit_rows(env["engine"], action=AuditAction.ACCOUNT_DELETE)
    assert {row.target for row in deletes} == {ALICE_ID, BOB_ID}
    assert all(row.actor_id is None for row in deletes)
