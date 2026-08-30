"""Deterministic unified-diff utilities — the S4.2 "applicable" contract.

The Fix Agent (S4.2) emits a reviewable **patch/diff** (build bible §19 S4.2:
"reviewable patch/diff, not a silent change"). Whether that patch is
*applicable* — i.e. it can actually be applied to the broken test file — is
checked deterministically, never by the model:

- :func:`make_patch` turns a (broken, fixed) text pair into a git-style
  unified diff (3 context lines) — the same shape the model is asked to
  produce, and the oracle reference in the unit tests;
- :func:`apply_patch` applies such a diff to a file and returns the patched
  text. It is deliberately tolerant of the drift local models introduce
  (trailing-whitespace / CRLF on context lines, a dropped leading space on
  empty context lines, ``\\ No newline`` markers) — but it **fails loud**
  (:class:`PatchError`) when a hunk cannot be found: a patch that does not
  apply is a gate failure, not a guess.

Pure: no I/O, no model calls. Both functions are inverse of each other —
``apply_patch(old, make_patch(old, new)) == new`` — which the unit tests pin.
"""

from __future__ import annotations

import difflib
import re
from typing import cast

__all__ = ["PatchError", "apply_patch", "make_patch", "parse_hunks"]


class PatchError(ValueError):
    """The patch is empty or cannot be applied to the given file (fail loud)."""


_HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def make_patch(old_text: str, new_text: str, path: str, *, context: int = 3) -> str:
    """Unified diff (git style) of ``old_text`` → ``new_text`` for ``path``.

    Returns ``""`` when the texts are identical (there is no change to make)
    — callers treat an empty patch as "nothing to apply".
    """
    if old_text == new_text:
        return ""
    old_lines = old_text.splitlines()
    new_lines = new_text.splitlines()
    diff = difflib.unified_diff(
        old_lines,
        new_lines,
        fromfile=f"a/{path}",
        tofile=f"b/{path}",
        lineterm="",
        n=context,
    )
    return "\n".join(diff).strip("\n")


def parse_hunks(patch: str) -> list[dict[str, object]]:
    """Parse a unified diff into hunks (pure parsing, no application).

    Each hunk carries its old-side lines (context + ``-``), new-side lines
    (context + ``+``), and the old start line from the ``@@`` header (used
    to anchor pure-insertion hunks that have no old-side anchor of their
    own). Header lines (``---``/``+++``), ``\\ No newline`` markers and
    blank context lines (a dropped leading space) are all tolerated.
    """
    hunks: list[dict[str, object]] = []
    current: dict[str, object] | None = None
    # The hunk dict holds lists, but ``dict[str, object]`` erases that — keep
    # the live lists here (rebound per hunk) so appends stay type-safe.
    old_lines: list[str] = []
    new_lines: list[str] = []
    for line in patch.splitlines():
        match = _HUNK_HEADER.match(line)
        if match:
            old_lines = []
            new_lines = []
            current = {
                "old_start": int(match.group(1)),
                "old": old_lines,
                "new": new_lines,
            }
            hunks.append(current)
            continue
        if current is None:
            continue  # file header / prose before the first hunk
        if line.startswith("--- ") or line.startswith("+++ "):
            continue
        if line.startswith("\\"):
            continue  # "\ No newline at end of file"
        if line.startswith("+"):
            new_lines.append(line[1:])
        elif line.startswith("-"):
            old_lines.append(line[1:])
        elif line.startswith(" ") or line == "":
            # ' ' + context, or an empty context line that lost its space.
            content = "" if line == "" else line[1:]
            old_lines.append(content)
            new_lines.append(content)
    return hunks


def _find_block(lines: list[str], block: list[str], start: int) -> int | None:
    """Locate *block* in *lines* at or after *start*.

    Exact match first; then a trailing-whitespace-tolerant match (local
    models occasionally re-flow the padding on context lines). Returns the
    index of the first line of the match, or ``None``.
    """
    n = len(block)
    if n == 0:
        return None
    limit = len(lines)
    for i in range(start, limit - n + 1):
        if lines[i : i + n] == block:
            return i
    block_rstripped = [line.rstrip() for line in block]
    for i in range(start, limit - n + 1):
        if [line.rstrip() for line in lines[i : i + n]] == block_rstripped:
            return i
    return None


def apply_patch(original: str, patch: str) -> str:
    """Apply a unified diff to ``original``; return the patched text.

    Raises :class:`PatchError` when the patch is empty, has no hunks, or a
    hunk's old-side block cannot be located — the S4.2 gate counts that
    fixture as *not applicable* (the report keeps the reason).
    """
    if not patch or not patch.strip():
        raise PatchError("empty patch — nothing to apply")
    hunks = parse_hunks(patch)
    if not hunks:
        raise PatchError(f"no @@ hunks found in patch: {patch[:200]!r}")

    result = list(original.splitlines())
    search_from = 0
    for index, hunk in enumerate(hunks, start=1):
        old_block = cast(list[str], hunk["old"])
        new_block = cast(list[str], hunk["new"])
        if old_block:
            pos = _find_block(result, old_block, search_from)
            if pos is None:
                expected = "\n".join(old_block[:5])
                raise PatchError(
                    f"hunk {index} of {len(hunks)} does not apply — "
                    f"old-side block not found (expected to start with: {expected[:160]!r})"
                )
        else:
            # Pure insertion: no old-side anchor — use the @@ header line.
            pos = min(max(cast(int, hunk["old_start"]) - 1, 0), len(result))
        result[pos : pos + len(old_block)] = new_block
        search_from = pos + len(new_block)

    return "\n".join(result) + ("\n" if original.endswith("\n") else "")
