"""FastAPI application package (build bible §7).

S0.3 skeleton: app factory, pydantic-settings, structured JSON logging,
``GET /health``.
S0.8: auth baseline (§31.3) — dev user + PBKDF2-SHA256 password, HS256 JWT
(``:mod:`qa_copilot_api.auth``), project-scoped RBAC
(``owner``/``member``/``viewer``), login/me/project routes
(``:mod:`qa_copilot_api.routes``).
"""

__version__ = "0.1.0"
