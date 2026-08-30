"""Read-only application context for the S4.2 Fix Agent.

The Fix Agent patches only the broken test file, but a *correct* test-side
fix depends on facts about the application under test that neither the S4.1
diagnosis nor the failure text carries: the current ``data-testid`` values
(and their defect-flag alternates), post-action routes and redirects, DOM
structure, real API endpoints/auth/response shapes, and seed data.

This module assembles that context **deterministically** from the demo
application (build bible §23 — the S4.2 app under test): a curated priority
list of the most fix-relevant files plus a generic walk of the source
directories, all size-capped so it fits the local model's context window.
It is pure path/content logic (no model, no network), so the offline unit
tests and the live gate exercise the exact same code path.

The output is **read-only reference** for the prompt — the §26 category
guard and the patch contract still confine the model to the target test
file.
"""

from __future__ import annotations

from pathlib import Path

#: Curated, highest-value files of the S4.2 app under test (build bible §23),
#: in **priority order**: the size cap keeps the first files and drops the
#: rest, so the most fix-relevant sources (test-ids, pages, routes, seeds)
#: always win over lower-value ones (boilerplate, README).
_PRIORITY_FILES: tuple[str, ...] = (
    "client/src/testids.js",
    "client/src/App.jsx",
    "client/src/pages/Login.jsx",
    "client/src/pages/Products.jsx",
    "client/src/pages/Cart.jsx",
    "client/src/pages/Checkout.jsx",
    "client/src/api.js",
    "server/src/defects.js",
    "server/src/db.js",
    "server/src/routes/auth.js",
    "server/src/routes/products.js",
    "server/src/routes/cart.js",
    "server/src/routes/orders.js",
    "playwright.config.js",
    "e2e/demo.spec.js",
    "e2e/fixtures.js",
    "client/src/main.jsx",
    "server/src/app.js",
    "server/src/index.js",
    "README.md",
)

#: Directories walked for any *other* source files (future-proofing — new
#: pages/routes picked up without touching this module).
_SCAN_DIRS: tuple[str, ...] = ("client/src", "server/src", "e2e")

#: Directory names never worth reading (vendored/build output).
_SKIP_DIRS: frozenset[str] = frozenset({"node_modules", "dist", "build", ".git", "__pycache__"})

#: File types that can inform a test-side fix (stylesheets are noise).
_INCLUDED_EXTS: frozenset[str] = frozenset({".js", ".jsx", ".ts", ".tsx", ".md", ".html"})

#: Default size cap for the assembled context (~12k tokens of JS/JSX) — sized
#: to fit the local model's context window alongside the failure, the
#: diagnosis, and the broken test file.
DEFAULT_MAX_CHARS = 48_000

_HEADER = (
    "Read-only source of the application under test. Use it to ground "
    "test-ids, selectors, routes, redirects, API shapes, and seed data. "
    "Reference material only — your patch may only touch the target test "
    "file."
)


def build_app_context(demo_app: str | Path, *, max_chars: int = DEFAULT_MAX_CHARS) -> str:
    """Assemble the deterministic read-only app context for the Fix Agent.

    *demo_app* is the demo application directory (the S4.2 app under test,
    §23). Files are included in priority order (see
    :data:`_PRIORITY_FILES`, then the :data:`_SCAN_DIRS` walk); each file is
    included in full only if the running total stays within *max_chars*, so
    the output — header included — is always ``≤ max_chars``.

    Returns ``""`` when *demo_app* is missing, holds no candidate files, or
    the cap is too small for even the header — callers treat ``""`` as
    "no app context" (the agent renders its fallback line instead).
    """
    root = Path(demo_app)
    if not root.is_dir() or max_chars <= len(_HEADER):
        return ""
    files = _candidate_files(root)
    if not files:
        return ""

    parts: list[str] = [_HEADER]
    used = len(_HEADER)
    omitted = 0
    for rel, path in files:
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            omitted += 1
            continue
        section = f"### {rel}\n{content.rstrip()}\n"
        if used + len("\n") + len(section) <= max_chars:
            parts.append(section)
            used += len("\n") + len(section)
        else:
            omitted += 1
    if omitted:
        note = f"({omitted} file(s) omitted for size)"
        if used + len("\n") + len(note) <= max_chars:
            parts.append(note)
    return "\n".join(parts)


def _candidate_files(root: Path) -> list[tuple[str, Path]]:
    """Curated-first candidate list, de-duplicated, in priority order."""
    seen: set[str] = set()
    ordered: list[tuple[str, Path]] = []

    def _add(path: Path) -> None:
        rel = path.relative_to(root).as_posix()
        if rel in seen:
            return
        seen.add(rel)
        ordered.append((rel, path))

    for rel in _PRIORITY_FILES:
        path = root / rel
        if path.is_file():
            _add(path)

    for scan in _SCAN_DIRS:
        base = root / scan
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if any(part in _SKIP_DIRS for part in path.relative_to(root).parts):
                continue
            if not path.is_file() or path.suffix not in _INCLUDED_EXTS:
                continue
            _add(path)

    return ordered


__all__ = ["DEFAULT_MAX_CHARS", "build_app_context"]
