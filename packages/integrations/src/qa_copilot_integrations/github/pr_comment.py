"""S7.2 — the idempotent GitHub PR regression comment (deterministic, LLM-free).

Builds the PR comment body from the resolved PR + the S6.x regression set
(plain JSON — the job passes the domain models' ``model_dump(mode="json")``
payloads, so this package stays independent of ``qa_copilot_ai``), and
upserts it on the pull request:

- first post → ``create`` the comment;
- re-post → *update* the existing marker comment instead of creating a
  duplicate (build bible §19 S7.2: "re-posting updates instead of
  duplicating");
- identical re-post → no write at all (``unchanged``).

The marker is an HTML comment on the first line of the body — invisible in
rendered Markdown, greppable in the API.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .client import GitHubClient

#: First line of every S7.2 comment — the idempotency marker (§19 S7.2).
MARKER = "<!-- qa-copilot:regression:v1 -->"

#: Bound the rendered comment (GitHub hard-limits comments at 65,536 bytes).
_MAX_FILES_SHOWN = 40
_MAX_TESTS_SHOWN = 100


@dataclass(frozen=True, slots=True)
class CommentUpsert:
    """Result of :func:`upsert_regression_comment`.

    ``action`` is ``"created"``, ``"updated"``, or ``"unchanged"`` — the
    value the API's ``regression.comment`` SSE event reports back.
    """

    action: str
    comment_id: int
    html_url: str


def has_marker(body: str) -> bool:
    """True when *body* is an S7.2 comment (marker on the first line)."""
    return body.lstrip().startswith(MARKER)


def _md_cell(value: str) -> str:
    """Escape pipes/backticks so a test path cannot break the table."""
    return value.replace("|", "\\|").replace("`", "'")


def _pct(value: object) -> str:
    """A 0..1 risk score as a whole percent (``0.78 → "78%"``)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "n/a"
    return f"{value * 100:.0f}%"


def build_comment_body(
    *,
    owner: str,
    repo: str,
    number: int,
    title: str = "",
    url: str = "",
    recommendation: Mapping[str, Any],
    impact: Mapping[str, Any],
) -> str:
    """Render the deterministic Markdown body for the PR regression set.

    Pure function of its inputs — the same PR + set always produces the
    same body, which is what makes ``unchanged`` re-posts possible (no
    timestamps, no environment-dependent text).
    """
    recs = recommendation.get("recommendations")
    if not isinstance(recs, list):
        recs = []
    top_n = recommendation.get("top_n")

    lines: list[str] = [MARKER, ""]
    lines.append(f"## AI QA Copilot — regression set for PR #{number}")
    lines.append("")

    header = f"**{owner}/{repo}**"
    if title:
        header += f" · *{title}*"
    if url:
        host = url.split("/", 3)[2] if url.count("/") >= 3 else url
        header += f" · [{host}]({url})"
    lines.append(header)
    lines.append("")

    changed = impact.get("changed")
    if isinstance(changed, list):
        shown = [c for c in changed if isinstance(c, str)][:_MAX_FILES_SHOWN]
        extra = len(changed) - len(shown)
        files_md = ", ".join(f"`{_md_cell(c)}`" for c in shown)
        if extra > 0:
            files_md += f" (+{extra} more)"
        lines.append(f"**Changed files ({len(changed)}):** {files_md}")
        lines.append("")

    impacted = impact.get("impacted")
    scanned = impact.get("test_files_scanned")
    if isinstance(impacted, list):
        impact_md = f"{len(impacted)} impacted test file{'s' if len(impacted) != 1 else ''}"
        if isinstance(scanned, int):
            impact_md += f" (of {scanned} scanned)"
        top_score = None
        for entry in recs:
            if isinstance(entry, dict) and isinstance(
                entry.get("risk_score"), (int, float)
            ) and not isinstance(entry.get("risk_score"), bool):
                top_score = entry["risk_score"]
                break
        if top_score is not None:
            impact_md += f" · **Top risk:** {_pct(top_score)}"
        lines.append(f"**Impact:** {impact_md}")
        lines.append("")

    shown_n = top_n if isinstance(top_n, int) and not isinstance(top_n, bool) else len(recs)
    lines.append(f"### Recommended tests (top {shown_n})")
    lines.append("")
    if not recs:
        lines.append("_No recommendations (empty corpus or insufficient samples)._")
    else:
        lines.append("| # | Test | Risk | Impact |")
        lines.append("|---|------|------|--------|")
        for entry in recs[:_MAX_TESTS_SHOWN]:
            if not isinstance(entry, dict):
                continue
            rank = entry.get("rank")
            test_key = entry.get("test_key", "")
            lines.append(
                f"| {rank if isinstance(rank, int) and not isinstance(rank, bool) else ''} "
                f"| `{_md_cell(test_key)}` "
                f"| {_pct(entry.get('risk_score'))} "
                f"| {entry.get('impact_kind') or '—'} |"
            )
        lines.append("")
        rationales: list[tuple[str, list[Any]]] = []
        for entry in recs[:3]:
            if not isinstance(entry, dict):
                continue
            rationale = entry.get("rationale")
            if isinstance(rationale, list):
                rationales.append((str(entry.get("test_key", "")), rationale))
        if rationales:
            lines.append("**Why (top 3):**")
            for test_key, rationale in rationales:
                reasons = [r for r in rationale if isinstance(r, str)]
                lines.append(f"- `{_md_cell(test_key)}` — {'; '.join(reasons)}")
            lines.append("")

    lines.append("---")
    lines.append(
        "_Posted by AI QA Copilot — deterministic change impact, risk ranking, and "
        "top-N set (no LLM in this result). Re-posting updates this comment._"
    )
    return "\n".join(lines)


async def upsert_regression_comment(
    client: GitHubClient,
    owner: str,
    repo: str,
    number: int,
    body: str,
) -> CommentUpsert:
    """Idempotently post *body* as a comment on PR ``{owner}/{repo}#{number}``.

    Finds the first existing comment carrying :data:`MARKER` and updates it
    when the body differs (``"updated"``); when it is identical, performs no
    write (``"unchanged"``). Without a marker comment, creates a new one
    (``"created"``). Never duplicates the S7.2 comment.
    """
    comments = await client.fetch_issue_comments(owner, repo, number)
    for comment in comments:
        if not has_marker(comment.body):
            continue
        if comment.body == body:
            return CommentUpsert(
                action="unchanged", comment_id=comment.id, html_url=comment.html_url
            )
        updated = await client.update_issue_comment(owner, repo, number, comment.id, body)
        return CommentUpsert(action="updated", comment_id=updated.id, html_url=updated.html_url)
    created = await client.create_issue_comment(owner, repo, number, body)
    return CommentUpsert(action="created", comment_id=created.id, html_url=created.html_url)


__all__ = [
    "CommentUpsert",
    "MARKER",
    "build_comment_body",
    "has_marker",
    "upsert_regression_comment",
]
