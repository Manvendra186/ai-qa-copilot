"""Local artifact storage (build bible §15, §31.11; S3.1).

"Artifact storage stays separate from relational metadata" (§15): files live
under a single store root, and the ``artifacts`` table references them by URI
only. Layout per §31.11: ``runs/{run_id}/{test_id}/{name}``.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path


class ArtifactStoreError(Exception):
    """Store layout violation: bad segment, escape attempt, or overwrite."""


#: Path segment: must start alphanumeric, then alphanumeric plus ``._-``.
_SEGMENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


def check_segment(value: str, what: str) -> str:
    """Validate one path segment of the §31.11 layout (no ``..`` or slashes)."""
    if not _SEGMENT.fullmatch(value):
        raise ArtifactStoreError(f"invalid {what}: {value!r}")
    return value


class ArtifactStore:
    """File store for execution artifacts under one root directory."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()

    def _target(self, run_id: str, test_id: str, name: str) -> Path:
        return (
            self.root
            / "runs"
            / check_segment(run_id, "run_id")
            / check_segment(test_id, "test_id")
            / check_segment(name, "artifact name")
        )

    def _uri(self, target: Path) -> str:
        return target.relative_to(self.root).as_posix()

    def store(self, run_id: str, test_id: str, name: str, source: str | Path) -> tuple[str, int]:
        """Copy *source* to ``runs/{run_id}/{test_id}/{name}``.

        Returns ``(uri, size_bytes)``. Never overwrites (S2.4 rule: fail,
        never clobber) and rejects sources outside the filesystem layout.
        """
        source_path = Path(source)
        if not source_path.is_file():
            raise ArtifactStoreError(f"source file missing: {source_path}")
        target = self._target(run_id, test_id, name)
        if target.exists():
            raise ArtifactStoreError(f"artifact already stored: {target}")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, target)
        return self._uri(target), target.stat().st_size

    def store_text(self, run_id: str, test_id: str, name: str, text: str) -> tuple[str, int]:
        """Write worker-generated *text* (e.g. the failure ``log``) to layout."""
        target = self._target(run_id, test_id, name)
        if target.exists():
            raise ArtifactStoreError(f"artifact already stored: {target}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        return self._uri(target), target.stat().st_size

    def resolve(self, uri: str) -> Path:
        """Resolve a store-relative URI to an absolute path (escape-safe)."""
        candidate = (self.root / uri).resolve()
        if not candidate.is_relative_to(self.root):
            raise ArtifactStoreError(f"uri escapes the store root: {uri!r}")
        return candidate

    def run_dir(self, run_id: str) -> Path:
        """The ``runs/{run_id}`` directory (may not exist yet)."""
        return self.root / "runs" / check_segment(run_id, "run_id")

    def delete_run(self, run_id: str) -> int:
        """Retention helper (§31.11): remove one run's files; return count."""
        run_dir = self.run_dir(run_id)
        if not run_dir.is_dir():
            return 0
        count = sum(1 for p in run_dir.rglob("*") if p.is_file())
        shutil.rmtree(run_dir)
        return count
