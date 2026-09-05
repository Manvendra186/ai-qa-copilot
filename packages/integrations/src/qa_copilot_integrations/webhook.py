"""Inbound webhook signature verification (build bible §19 S7.3).

Deterministic + LLM-free (S2.1/S3.3/S5.1/S6.1/S7.1 pattern): the pure core
of the S7.3 GitHub webhook — ``X-Hub-Signature-256`` HMAC-SHA256
verification over the *raw* request bytes. Nothing in this module imports
``qa_copilot_ai``; the §31.1 gateway stays off the integration path, as for
the rest of the package (pinned by ``tests/unit/test_s71_no_llm.py``).

The signature IS the auth on ``POST /api/v1/webhooks/github`` (S7.3):
there is no bearer token and no RBAC on that endpoint — a caller either
presents a valid HMAC with the project's webhook secret or gets 401.
"""

from __future__ import annotations

import hashlib
import hmac

__all__ = ["compute_github_signature", "verify_github_signature"]

_SIGNATURE_PREFIX = "sha256="


def compute_github_signature(secret: str, body: bytes) -> str:
    """The ``X-Hub-Signature-256`` value GitHub sends: ``sha256=<hex>``.

    HMAC-SHA256 of the *raw* request body under *secret*. The exact body
    bytes matter (whitespace/formatting included) — sign what you send.
    """
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"{_SIGNATURE_PREFIX}{digest}"


def verify_github_signature(secret: str, body: bytes, signature_header: str | None) -> bool:
    """Constant-time check of a sender's ``X-Hub-Signature-256`` header (S7.3).

    Missing header, empty secret, or a wrong ``sha256=`` prefix are all
    ``False`` — the route answers 401 either way ("invalid/missing → 401").
    Comparison uses :func:`hmac.compare_digest` (constant-time) so a bad
    signature leaks no timing oracle; non-ASCII header bytes never raise.
    """
    if not secret or not signature_header:
        return False
    expected = compute_github_signature(secret, body)
    return hmac.compare_digest(expected.encode("utf-8"), signature_header.strip().encode("utf-8"))
