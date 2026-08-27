"""API response schemas (build bible §7)."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    """Liveness contract: process up, configuration readable, no I/O."""

    status: str = "ok"
    service: str
    version: str
    env: str
    timestamp: datetime


# --- Auth baseline (S0.8, §31.3) ---------------------------------------------


class LoginRequest(BaseModel):
    """Dev-mode login (single user per project team; SSO is Phase 8)."""

    email: str = Field(min_length=3)
    password: str = Field(min_length=1)


class UserOut(BaseModel):
    """A user; ``role`` is the default role (authorization uses project roles)."""

    id: str
    email: str
    role: str


class ProjectRef(BaseModel):
    """A project the caller is a member of, with the caller's role in it."""

    id: str
    name: str
    role: str


class TokenResponse(BaseModel):
    """Login result: Bearer access token + the caller's project memberships."""

    token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserOut
    projects: list[ProjectRef]


class MeResponse(BaseModel):
    """``GET /api/v1/auth/me``: who I am + where I have roles."""

    user: UserOut
    projects: list[ProjectRef]


class ProjectOut(BaseModel):
    """Project detail (read endpoints, viewer floor)."""

    id: str
    name: str
    settings: dict[str, Any]
