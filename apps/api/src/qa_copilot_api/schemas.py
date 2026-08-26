"""API response schemas (build bible §7)."""

from datetime import datetime

from pydantic import BaseModel


class HealthResponse(BaseModel):
    """Liveness contract: process up, configuration readable, no I/O."""

    status: str = "ok"
    service: str
    version: str
    env: str
    timestamp: datetime
