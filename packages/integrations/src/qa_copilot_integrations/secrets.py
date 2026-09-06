"""Shared secret-redaction helpers (build bible §17).

Every integration client (GitHub S7.1, Jira S7.4, ...) passes non-2xx
response bodies — and any other server text — through :func:`redact_secrets`
before it lands in an exception message, so a token echoed back by a proxy,
error page, or debug body can never leak into logs, audit rows, or CLI
stdout (S7.1/S7.4 exit: "token never appears in logs or audit output").

Two layers, applied in order:

1. **exact-value redaction** — any caller-supplied secret (the PAT / API
   token the client actually sent) is replaced verbatim, so tokens with no
   recognizable shape (Jira Cloud API tokens) are covered too;
2. **pattern redaction** — a conservative set of well-known secret shapes
   (GitHub personal/access tokens, Jira Cloud API tokens, ``Bearer``
   credentials, ``token=`` query material).

Redaction is idempotent: none of the replacements re-match their own
output, and the ``***REDACTED***`` sentinel contains no secret-looking
material.

Kept in the integrations root (not the AI package): integrations must stay
independent of ``qa_copilot_ai`` (S7.1 "no LLM in the path").
"""

from __future__ import annotations

import re

#: Redaction sentinel — same value as ``qa_copilot_ai.redaction.REDACTED``
#: (kept local: integrations must stay independent of the AI package).
REDACTED = "***REDACTED***"

# (pattern, replacement) pairs applied in order. Conservative set: GitHub
# personal/access tokens, Jira Cloud API tokens (``ATATT...``),
# ``Bearer`` credentials, and ``token=`` query material. None of the
# replacements re-match (redaction is idempotent).
_SECRET_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bBearer\s+[A-Za-z0-9\-_\.+/=]+"), f"Bearer {REDACTED}"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{16,}"), REDACTED),
    (re.compile(r"\bATATT[A-Za-z0-9_\-]{16,}"), REDACTED),
    (
        re.compile(r"([?&]token=)([A-Za-z0-9\-_\.+/=]{8,})", re.IGNORECASE),
        r"\1" + REDACTED,
    ),
)


def redact_secrets(text: str, *secrets: str) -> tuple[str, int]:
    """Replace secret-looking material with ``***REDACTED***``.

    *secrets* are exact secret values (e.g. the token the client sent);
    each non-empty value is replaced verbatim first, then the pattern set
    runs over the result. Returns the redacted text plus how many
    replacements were made. Idempotent: redacting twice changes nothing.
    """
    count = 0
    for secret in secrets:
        if secret:
            occurrences = text.count(secret)
            if occurrences:
                text = text.replace(secret, REDACTED)
                count += occurrences
    for pattern, replacement in _SECRET_PATTERNS:
        text, replaced = pattern.subn(replacement, text)
        count += replaced
    return text, count


__all__ = ["REDACTED", "redact_secrets"]
