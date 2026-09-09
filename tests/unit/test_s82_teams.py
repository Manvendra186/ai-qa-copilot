"""S8.2 organization membership + code-based invite API tests (build bible §19 S8.2).

Covers the organization/invite endpoints (RBAC via ``organization_members``):

- ``GET    /api/v1/organizations``                    — authenticated
- ``GET    /api/v1/organizations/{id}/members``       — org members
- ``POST   /api/v1/organizations/{id}/members``       — org owner only
- ``PATCH  /api/v1/organizations/{id}/members/{uid}`` — org owner only
- ``DELETE /api/v1/organizations/{id}/members/{uid}`` — self, or the owner
- ``POST   /api/v1/organizations/{id}/invites``       — org owner only
- ``POST   /api/v1/invites/{code}/accept``            — invitee (email match)

S8.2 access model: the org role is the **baseline** project role for the
org's projects (``owner`` → ``owner``, ``member`` → ``member``); an explicit
``project_members`` row always wins — it can narrow *or* widen the baseline.

Invite taxonomy is non-leaking (§31.3): unknown / expired / reused code →
``404`` (never distinguishable), email mismatch → ``403``, already a member
→ ``409``.

Same scratch-DB pattern as ``test_integrations_api.py`` (dedicated database,
skipped when no Postgres is reachable — ``pytest`` stays green without docker).
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
from qa_copilot_domain.enums import OrgRole, ProjectRole
from qa_copilot_repository import db, models

REPO_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = REPO_ROOT / "alembic.ini"
TEST_DB = "qa_copilot_teams_test"
TEST_URL = f"postgresql+psycopg://qa:qa@localhost:5433/{TEST_DB}"
ADMIN_URL = "postgresql+psycopg://qa:qa@localhost:5433/postgres"

SECRET = "test-secret-0123456789abcdef"  # 16+ chars, test-only

# ids are Postgres UUIDs — deterministic values, stable across runs
NS = NAMESPACE_DNS
ACME_ID = str(uuid5(NS, "org-acme"))
BETA_ID = str(uuid5(NS, "org-beta"))  # carol's org; carol is NOT in acme
GHOST_ORG_ID = str(uuid5(NS, "org-ghost"))  # valid UUID, no such org
ACME_PROJECT_ID = str(uuid5(NS, "acme-store"))  # explicit: alice viewer, bob owner
BASELINE_PROJECT_ID = str(uuid5(NS, "acme-baseline"))  # no explicit rows: baseline
GHOST_PROJECT_ID = str(uuid5(NS, "ghost-project"))  # valid UUID, no such project
ALICE_ID = str(uuid5(NS, "user-alice"))
BOB_ID = str(uuid5(NS, "user-bob"))
CAROL_ID = str(uuid5(NS, "user-carol"))
DAVE_ID = str(uuid5(NS, "user-dave"))

EMAILS = {
    "alice": "alice@local.dev",  # owner of acme
    "bob": "bob@local.dev",  # member of acme
    "carol": "carol@local.dev",  # NOT a member of acme (owner of beta)
    "dave": "dave@local.dev",  # NOT a member of acme; invitee
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
        pytest.skip("no Postgres reachable — S8.2 API tests need a database")

    _admin(f"DROP DATABASE IF EXISTS {TEST_DB}")
    _admin(f"CREATE DATABASE {TEST_DB}")

    os.environ["DATABASE_URL"] = TEST_URL  # alembic env.py: env var wins
    engine = db.make_engine(TEST_URL)
    command.upgrade(Config(str(ALEMBIC_INI)), "head")

    with db.make_session_factory(engine)() as session:
        for user in EMAILS:
            session.add(models.User(id=USER_IDS[user], email=EMAILS[user], role="developer"))
        session.add_all(
            [
                models.Organization(id=ACME_ID, name="Acme Inc"),
                models.Organization(id=BETA_ID, name="Beta Inc"),
            ]
        )
        session.add_all(
            [
                models.Project(id=ACME_PROJECT_ID, organization_id=ACME_ID, name="Acme Store"),
                models.Project(
                    id=BASELINE_PROJECT_ID, organization_id=ACME_ID, name="Acme Baseline"
                ),
            ]
        )
        session.flush()
        # acme membership: alice owner, bob member (the S8.2 surface under test)
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
                models.OrganizationMember(
                    organization_id=BETA_ID, user_id=DAVE_ID, role=OrgRole.MEMBER
                ),
            ]
        )
        # explicit project roles on "Acme Store" (always win over the baseline):
        # alice narrowed to viewer, bob widened to owner. "Acme Baseline" has
        # NO explicit rows → org-baseline access is exercised there.
        session.add_all(
            [
                models.ProjectMember(
                    project_id=ACME_PROJECT_ID, user_id=ALICE_ID, role=ProjectRole.VIEWER
                ),
                models.ProjectMember(
                    project_id=ACME_PROJECT_ID, user_id=BOB_ID, role=ProjectRole.OWNER
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


def _member_rows(client: TestClient, org_id: str) -> list[dict[str, Any]]:
    resp = client.get(_org_url(org_id, "/members"), headers=_auth_header("alice"))
    assert resp.status_code == 200, resp.text
    body: list[dict[str, Any]] = resp.json()
    return body


# --- organization listing -------------------------------------------------------


def test_list_organizations(client: TestClient) -> None:
    resp = client.get("/api/v1/organizations", headers=_auth_header("alice"))
    assert resp.status_code == 200
    orgs = resp.json()
    assert len(orgs) == 1
    org = orgs[0]
    assert org["id"] == ACME_ID
    assert org["name"] == "Acme Inc"
    assert org["role"] == "owner"
    assert org["member_count"] == 2
    assert org["created_at"]

    carol_orgs = client.get("/api/v1/organizations", headers=_auth_header("carol")).json()
    assert [o["id"] for o in carol_orgs] == [BETA_ID]
    assert carol_orgs[0]["role"] == "owner"
    assert carol_orgs[0]["member_count"] == 2


def test_list_organizations_requires_authentication(client: TestClient) -> None:
    assert client.get("/api/v1/organizations").status_code == 401


# --- member listing --------------------------------------------------------------


def test_list_members(client: TestClient) -> None:
    rows = _member_rows(client, ACME_ID)
    assert len(rows) == 2
    by_email = {row["email"]: row for row in rows}
    alice = by_email[EMAILS["alice"]]
    bob = by_email[EMAILS["bob"]]
    assert alice["id"] == ALICE_ID
    assert alice["role"] == "owner"
    assert alice["joined_at"]
    assert bob["role"] == "member"


def test_list_members_requires_membership(client: TestClient) -> None:
    # non-members of acme → 403
    assert (
        client.get(_org_url(ACME_ID, "/members"), headers=_auth_header("carol")).status_code == 403
    )
    assert (
        client.get(_org_url(ACME_ID, "/members"), headers=_auth_header("dave")).status_code == 403
    )
    # unknown org → 403 (never 404 — membership is checked first, no
    # existence leak of orgs the caller does not belong to)
    assert (
        client.get(_org_url(GHOST_ORG_ID, "/members"), headers=_auth_header("alice")).status_code
        == 403
    )
    # unauthenticated → 401
    assert client.get(_org_url(ACME_ID, "/members")).status_code == 401


# --- adding members ---------------------------------------------------------------


def test_add_member_requires_org_owner(client: TestClient) -> None:
    body = {"email": EMAILS["carol"], "role": "member"}
    assert (
        client.post(
            _org_url(ACME_ID, "/members"), json=body, headers=_auth_header("bob")
        ).status_code
        == 403
    )
    assert (
        client.post(
            _org_url(ACME_ID, "/members"), json=body, headers=_auth_header("carol")
        ).status_code
        == 403
    )
    assert client.post(_org_url(ACME_ID, "/members"), json=body).status_code == 401


def test_add_member_rules(client: TestClient) -> None:
    base = _org_url(ACME_ID, "/members")
    # ok: adds carol (a beta member, not in acme)
    resp = client.post(
        base, json={"email": EMAILS["carol"], "role": "member"}, headers=_auth_header("alice")
    )
    assert resp.status_code == 201, resp.text
    added = resp.json()
    assert added["id"] == CAROL_ID
    assert added["email"] == EMAILS["carol"]
    assert added["role"] == "member"
    assert added["joined_at"]
    assert {row["email"] for row in _member_rows(client, ACME_ID)} == {
        EMAILS["alice"],
        EMAILS["bob"],
        EMAILS["carol"],
    }

    # unknown email → 404
    resp = client.post(
        base, json={"email": "ghost@local.dev", "role": "member"}, headers=_auth_header("alice")
    )
    assert resp.status_code == 404
    # already a member → 409
    resp = client.post(
        base, json={"email": EMAILS["bob"], "role": "member"}, headers=_auth_header("alice")
    )
    assert resp.status_code == 409
    # second owner is rejected (single-owner invariant) → 409
    resp = client.post(
        base, json={"email": EMAILS["dave"], "role": "owner"}, headers=_auth_header("alice")
    )
    assert resp.status_code == 409
    # invalid role → 422
    resp = client.post(
        base, json={"email": EMAILS["dave"], "role": "admin"}, headers=_auth_header("alice")
    )
    assert resp.status_code == 422
    # unknown org → 403 (owner check runs before any lookup — no existence leak)
    resp = client.post(
        _org_url(GHOST_ORG_ID, "/members"),
        json={"email": EMAILS["dave"], "role": "member"},
        headers=_auth_header("alice"),
    )
    assert resp.status_code == 403


# --- role changes (owner transfer) ---------------------------------------------------


def test_update_member_rules(client: TestClient) -> None:
    base = _org_url(ACME_ID, "/members")
    # non-owner cannot change roles → 403
    resp = client.patch(f"{base}/{DAVE_ID}", json={"role": "owner"}, headers=_auth_header("bob"))
    assert resp.status_code == 403
    # unknown member → 404
    resp = client.patch(f"{base}/{DAVE_ID}", json={"role": "member"}, headers=_auth_header("alice"))
    assert resp.status_code == 404
    # invalid role → 422
    resp = client.patch(f"{base}/{BOB_ID}", json={"role": "admin"}, headers=_auth_header("alice"))
    assert resp.status_code == 422
    # missing body → 422
    resp = client.patch(f"{base}/{BOB_ID}", headers=_auth_header("alice"))
    assert resp.status_code == 422


def test_owner_transfer_by_promotion(client: TestClient) -> None:
    base = _org_url(ACME_ID, "/members")
    # owner adds dave as a member…
    resp = client.post(
        base, json={"email": EMAILS["dave"], "role": "member"}, headers=_auth_header("alice")
    )
    assert resp.status_code == 201
    # …then promotes dave to owner. The acting owner steps down to member.
    resp = client.patch(f"{base}/{DAVE_ID}", json={"role": "owner"}, headers=_auth_header("alice"))
    assert resp.status_code == 200, resp.text
    assert resp.json()["role"] == "owner"

    by_email = {row["email"]: row for row in _member_rows(client, ACME_ID)}
    assert by_email[EMAILS["dave"]]["role"] == "owner"
    assert by_email[EMAILS["alice"]]["role"] == "member"  # stepped down

    # the new owner can manage; the old owner can no longer
    resp = client.patch(f"{base}/{ALICE_ID}", json={"role": "member"}, headers=_auth_header("dave"))
    assert resp.status_code == 200
    resp = client.patch(f"{base}/{DAVE_ID}", json={"role": "member"}, headers=_auth_header("alice"))
    assert resp.status_code == 403
    # dave is now the sole owner → cannot demote or remove themselves
    resp = client.patch(f"{base}/{DAVE_ID}", json={"role": "member"}, headers=_auth_header("dave"))
    assert resp.status_code == 409
    assert client.delete(f"{base}/{DAVE_ID}", headers=_auth_header("dave")).status_code == 409


def test_sole_owner_protection(client: TestClient) -> None:
    base = _org_url(ACME_ID, "/members")
    # alice is the sole owner of acme: cannot demote or remove themselves
    resp = client.patch(
        f"{base}/{ALICE_ID}", json={"role": "member"}, headers=_auth_header("alice")
    )
    assert resp.status_code == 409
    assert resp.json()["detail"] == "cannot demote the organization owner"
    resp = client.delete(f"{base}/{ALICE_ID}", headers=_auth_header("alice"))
    assert resp.status_code == 409
    assert resp.json()["detail"] == "cannot remove the organization owner"


# --- removing members -----------------------------------------------------------------


def test_remove_member_by_owner(client: TestClient) -> None:
    base = _org_url(ACME_ID, "/members")
    # a member cannot remove another member → 403
    assert client.delete(f"{base}/{ALICE_ID}", headers=_auth_header("bob")).status_code == 403
    # unknown member → 404
    assert client.delete(f"{base}/{CAROL_ID}", headers=_auth_header("alice")).status_code == 404
    # owner removes bob → 204; bob is gone…
    assert client.delete(f"{base}/{BOB_ID}", headers=_auth_header("alice")).status_code == 204
    assert client.get(_org_url(ACME_ID, "/members"), headers=_auth_header("bob")).status_code == 403
    # …and removing them again is an unknown member
    assert client.delete(f"{base}/{BOB_ID}", headers=_auth_header("alice")).status_code == 404


def test_member_self_removal(client: TestClient) -> None:
    base = _org_url(ACME_ID, "/members")
    assert client.delete(f"{base}/{BOB_ID}", headers=_auth_header("bob")).status_code == 204
    # bob is out: membership gone, only alice remains
    assert client.get(_org_url(ACME_ID, "/members"), headers=_auth_header("bob")).status_code == 403
    assert {row["email"] for row in _member_rows(client, ACME_ID)} == {EMAILS["alice"]}


# --- invites ---------------------------------------------------------------------------


def test_invite_requires_org_owner(client: TestClient) -> None:
    url = _org_url(ACME_ID, "/invites")
    body = {"email": EMAILS["dave"], "role": "member"}
    assert client.post(url, json=body, headers=_auth_header("bob")).status_code == 403
    assert client.post(url, json=body, headers=_auth_header("carol")).status_code == 403
    assert client.post(url, json=body).status_code == 401


def _issue_invite(
    client: TestClient, org_id: str, email: str, role: str = "member"
) -> dict[str, Any]:
    resp = client.post(
        _org_url(org_id, "/invites"),
        json={"email": email, "role": role},
        headers=_auth_header("alice"),
    )
    assert resp.status_code == 201, resp.text
    body: dict[str, Any] = resp.json()
    return body


def test_invite_flow_end_to_end(client: TestClient) -> None:
    invite = _issue_invite(client, ACME_ID, EMAILS["dave"])
    assert invite["email"] == EMAILS["dave"]
    assert invite["role"] == "member"
    assert invite["code"]
    assert invite["created_at"]
    created = datetime.fromisoformat(invite["created_at"])
    expires = datetime.fromisoformat(invite["expires_at"])
    # 7-day TTL (S8.2): created_at is DB-side, expires_at is now()+TTL — allow slack
    assert timedelta(hours=24) < expires - created < timedelta(days=8)

    # dave (the invited email) accepts → joins acme as member
    resp = client.post(f"/api/v1/invites/{invite['code']}/accept", headers=_auth_header("dave"))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["organization"]["id"] == ACME_ID
    assert body["organization"]["name"] == "Acme Inc"
    assert body["organization"]["role"] == "member"
    assert body["role"] == "member"

    # dave is now a member…
    by_email = {row["email"]: row for row in _member_rows(client, ACME_ID)}
    assert by_email[EMAILS["dave"]]["role"] == "member"
    # …and gets org-baseline access to both acme projects (no explicit rows)
    resp = client.get("/api/v1/projects", headers=_auth_header("dave"))
    assert resp.status_code == 200
    projects = {p["name"]: p["role"] for p in resp.json()}
    assert projects == {"Acme Store": "member", "Acme Baseline": "member"}

    # the code is single-use → reuse is 404 (same shape as unknown: no reason leak)
    resp = client.post(f"/api/v1/invites/{invite['code']}/accept", headers=_auth_header("dave"))
    assert resp.status_code == 404


def test_invite_unknown_code_and_auth(client: TestClient) -> None:
    resp = client.post("/api/v1/invites/not-a-real-code/accept", headers=_auth_header("dave"))
    assert resp.status_code == 404
    # unauthenticated → 401 (auth runs before any lookup)
    assert client.post("/api/v1/invites/not-a-real-code/accept").status_code == 401


def test_invite_email_mismatch(client: TestClient) -> None:
    invite = _issue_invite(client, ACME_ID, EMAILS["dave"])
    # the code is not transferable: a different authenticated email → 403
    resp = client.post(f"/api/v1/invites/{invite['code']}/accept", headers=_auth_header("bob"))
    assert resp.status_code == 403
    resp = client.post(f"/api/v1/invites/{invite['code']}/accept", headers=_auth_header("carol"))
    assert resp.status_code == 403
    # dave's membership is untouched
    assert {row["email"] for row in _member_rows(client, ACME_ID)} == {
        EMAILS["alice"],
        EMAILS["bob"],
    }


def test_invite_already_member(client: TestClient) -> None:
    # inviting an existing member: the email matches, but the account is in
    # the org already → 409
    invite = _issue_invite(client, ACME_ID, EMAILS["bob"])
    resp = client.post(f"/api/v1/invites/{invite['code']}/accept", headers=_auth_header("bob"))
    assert resp.status_code == 409
    assert resp.json()["detail"] == "already a member of this organization"


def test_invite_invalid_role(client: TestClient) -> None:
    resp = client.post(
        _org_url(ACME_ID, "/invites"),
        json={"email": EMAILS["dave"], "role": "admin"},
        headers=_auth_header("alice"),
    )
    assert resp.status_code == 422


# --- S8.2 project access model -------------------------------------------------------


def test_project_listing_org_baseline_and_explicit_override(client: TestClient) -> None:
    def listing(user: str) -> dict[str, str]:
        resp = client.get("/api/v1/projects", headers=_auth_header(user))
        assert resp.status_code == 200, resp.text
        return {p["name"]: p["role"] for p in resp.json()}

    # explicit rows win (narrow/widen); no explicit row → org baseline
    assert listing("alice") == {"Acme Store": "viewer", "Acme Baseline": "owner"}
    assert listing("bob") == {"Acme Store": "owner", "Acme Baseline": "member"}
    # non-members of acme see none of its projects
    assert listing("dave") == {}
    assert listing("carol") == {}


def test_explicit_role_narrows_org_owner(client: TestClient) -> None:
    # alice is the org owner but an explicit row narrows her to viewer on
    # "Acme Store": read yes, write no (the baseline owner would allow both)
    resp = client.get(f"/api/v1/projects/{ACME_PROJECT_ID}", headers=_auth_header("alice"))
    assert resp.status_code == 200
    assert resp.json()["name"] == "Acme Store"
    resp = client.delete(f"/api/v1/projects/{ACME_PROJECT_ID}", headers=_auth_header("alice"))
    assert resp.status_code == 403


def test_explicit_role_widens_org_member(client: TestClient) -> None:
    # bob is an org member but an explicit row widens him to owner on
    # "Acme Store": owner-only deletion is allowed (the baseline member would 403)
    resp = client.delete(f"/api/v1/projects/{ACME_PROJECT_ID}", headers=_auth_header("bob"))
    assert resp.status_code == 204
    # the project is gone: alice has no role on it anymore → 403 (a deleted
    # project is indistinguishable from a non-member's — no existence leak),
    # and it is no longer in her listing
    resp = client.get(f"/api/v1/projects/{ACME_PROJECT_ID}", headers=_auth_header("alice"))
    assert resp.status_code == 403
    resp = client.get("/api/v1/projects", headers=_auth_header("alice"))
    assert resp.status_code == 200
    assert {p["name"] for p in resp.json()} == {"Acme Baseline"}


def test_org_baseline_grants_project_access(client: TestClient) -> None:
    # "Acme Baseline" has NO explicit rows: access is the org-baseline role.
    # bob = org member → viewer floor met, owner floor not met
    resp = client.get(f"/api/v1/projects/{BASELINE_PROJECT_ID}", headers=_auth_header("bob"))
    assert resp.status_code == 200
    resp = client.delete(f"/api/v1/projects/{BASELINE_PROJECT_ID}", headers=_auth_header("bob"))
    assert resp.status_code == 403
    # alice = org owner → baseline owner → owner-only deletion allowed
    resp = client.delete(f"/api/v1/projects/{BASELINE_PROJECT_ID}", headers=_auth_header("alice"))
    assert resp.status_code == 204


def test_non_member_project_access_denied(client: TestClient) -> None:
    # no org membership → no baseline → no role → 403 (no existence leak,
    # same for a valid UUID that names no project)
    for project_id in (ACME_PROJECT_ID, BASELINE_PROJECT_ID, GHOST_PROJECT_ID):
        resp = client.get(f"/api/v1/projects/{project_id}", headers=_auth_header("dave"))
        assert resp.status_code == 403
        resp = client.delete(f"/api/v1/projects/{project_id}", headers=_auth_header("dave"))
        assert resp.status_code == 403
