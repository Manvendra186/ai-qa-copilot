"""Secret redaction for AI context (build bible §31.5, §31.7).

§31.7 requires **secret/PII leaks = 0**: every prompt that leaves the
process (to the local LLM) and every snapshot we log or store for prompt
debugging (§31.5) is passed through a :class:`Redactor` first. Patterns are
conservative — they replace the secret material with ``***REDACTED***`` and
keep the surrounding structure readable, and none of the replacements re-match
any pattern (redaction is idempotent).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

REDACTED = "***REDACTED***"

# (pattern, replacement) pairs applied in order; each targets a distinct
# secret shape. Kept deliberately small — add patterns as leaks are found,
# never silently widen.
_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bBearer\s+[A-Za-z0-9\-_\.+/=]+"), f"Bearer {REDACTED}"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{16,}"), REDACTED),
    (re.compile(r"\bsk-[A-Za-z0-9]{16,}"), REDACTED),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), REDACTED),
    (
        re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}"),
        REDACTED,
    ),
    (
        re.compile(r"\b(postgres(ql)?|mysql|redis|rediss)://([^:/@\s]+):([^@\s]+)@"),
        r"\1://\3:" + REDACTED + "@",
    ),
    (
        re.compile(
            r"\b(api[_-]?key|access[_-]?token|auth[_-]?token|client[_-]?secret|password|passwd)\b"
            r"(\s*[=:]\s*)([\"']?)([A-Za-z0-9\-_\.+/=]{8,})\3"
        ),
        r"\1\2\3" + REDACTED + r"\3",
    ),
)


@dataclass(frozen=True, slots=True)
class RedactResult:
    """Redacted text plus how many secrets were replaced."""

    text: str
    count: int


class Redactor:
    """Applies the secret patterns to a string, or to an OpenAI message list."""

    def redact(self, text: str) -> RedactResult:
        count = 0
        for pattern, replacement in _PATTERNS:
            text, replaced = pattern.subn(replacement, text)
            count += replaced
        return RedactResult(text=text, count=count)

    def redact_messages(
        self, messages: Sequence[dict[str, str]]
    ) -> tuple[list[dict[str, str]], int]:
        """Redact every message's ``content``; return (new list, total count)."""
        redacted: list[dict[str, str]] = []
        total = 0
        for message in messages:
            content = message.get("content")
            if not isinstance(content, str):
                raise TypeError("message content must be a str (V1)")
            result = self.redact(content)
            redacted.append({**message, "content": result.text})
            total += result.count
        return redacted, total


#: Shared instance for the default wiring.
DEFAULT_REDACTOR = Redactor()


__all__ = ["DEFAULT_REDACTOR", "REDACTED", "RedactResult", "Redactor"]
