"""Prompt registry types + rendering (build bible §31.6).

Prompts are versioned: agents reference them by ``name@version``; runtime
resolves via the ``prompt_versions`` table (DB-backed loader:
``qa_copilot_repository.prompts.load_prompt``). This module holds the shared
: class:`PromptSpec` type, the ``PromptStore`` protocol, the in-memory store
(tests / fakes), and strict ``{{variable}}`` rendering — a template that
references an un-supplied variable **fails loud**, never renders empty.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

_PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")


@dataclass(frozen=True, slots=True)
class PromptSpec:
    """One registered prompt version (mirrors the ``prompt_versions`` row)."""

    name: str
    version: int
    body: str
    model_class: str = "coder"
    input_budget: int | None = None
    output_budget: int | None = None
    schema_ref: str | None = None
    temperature: float | None = None

    @property
    def ref(self) -> str:
        """``name@version`` — the form agents reference (§31.6)."""
        return f"{self.name}@{self.version}"


class PromptError(ValueError):
    """Base error for prompt-registry problems."""


class PromptNotFound(PromptError, KeyError):
    """The requested ``name@version`` is not registered."""


class PromptRenderError(PromptError):
    """The template references variables that were not supplied."""


class PromptStore(Protocol):
    """Anything that can resolve ``name@version`` to a :class:`PromptSpec`."""

    def get(self, name: str, version: int | None = None) -> PromptSpec: ...


class InMemoryPromptStore:
    """Prompt store for tests and fakes (DB-backed: repository package)."""

    def __init__(self, prompts: Iterable[PromptSpec] = ()) -> None:
        self._by_name: dict[str, dict[int, PromptSpec]] = {}
        for spec in prompts:
            self.put(spec)

    def put(self, spec: PromptSpec) -> None:
        self._by_name.setdefault(spec.name, {})[spec.version] = spec

    def get(self, name: str, version: int | None = None) -> PromptSpec:
        versions = self._by_name.get(name)
        if not versions:
            raise PromptNotFound(f"{name}@{version if version is not None else 'latest'}")
        wanted = max(versions) if version is None else version
        try:
            return versions[wanted]
        except KeyError:
            raise PromptNotFound(f"{name}@{wanted}") from None


class FilePromptStore:
    """Prompt store that loads versioned prompt files from a directory (§31.6).

    Each ``.md`` file in *directory* is a versioned prompt (front-matter +
    body). The store resolves ``name@version`` by matching the file's
    front-matter ``name`` and ``version``.
    """

    def __init__(self, directory: str | Path) -> None:
        self._directory = Path(directory)
        self._by_name: dict[str, dict[int, PromptSpec]] = {}
        self._load()

    def _load(self) -> None:
        if not self._directory.is_dir():
            return
        for path in sorted(self._directory.glob("*.md")):
            try:
                spec = load_prompt_file(path)
            except PromptError:
                continue
            self._by_name.setdefault(spec.name, {})[spec.version] = spec

    def get(self, name: str, version: int | None = None) -> PromptSpec:
        versions = self._by_name.get(name)
        if not versions:
            raise PromptNotFound(f"{name}@{version if version is not None else 'latest'}")
        wanted = max(versions) if version is None else version
        try:
            return versions[wanted]
        except KeyError:
            raise PromptNotFound(f"{name}@{wanted}") from None


def render_prompt(spec: PromptSpec, **variables: str) -> str:
    """Substitute ``{{name}}`` placeholders in *spec* with *variables*.

    Raises :class:`PromptRenderError` naming every missing variable — prompt
    changes must be regression-tested, never silently render half-empty
    (§31.6).
    """
    supplied = dict(variables)
    missing = sorted({p for p in _PLACEHOLDER.findall(spec.body) if p not in supplied})
    if missing:
        raise PromptRenderError(f"prompt {spec.ref} requires variables: {', '.join(missing)}")
    return _PLACEHOLDER.sub(lambda match: supplied[match.group(1)], spec.body)


def load_prompt_file(path: str | Path) -> PromptSpec:
    """Load a versioned prompt file (§31.6) into a :class:`PromptSpec`.

    The file has a ``---`` delimited front-matter block (``key: value`` lines)
    followed by the prompt body. The front-matter carries the registry
    metadata (``name``, ``version``, ``model_class``, ``input_budget``,
    ``output_budget``, ``schema_ref``, ``temperature``); the body is the
    prompt template (``{{variable}}`` placeholders).
    """
    text = Path(path).read_text(encoding="utf-8")
    if not text.startswith("---"):
        raise PromptError(f"prompt file {path} has no front-matter")
    parts = text.split("---", 2)
    if len(parts) < 3:
        raise PromptError(f"prompt file {path} has malformed front-matter")
    meta_text, body = parts[1], parts[2].lstrip("\n")
    meta: dict[str, str] = {}
    for line in meta_text.strip().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            raise PromptError(f"prompt file {path} has malformed front-matter line: {line!r}")
        key, _, value = line.partition(":")
        meta[key.strip()] = value.strip()
    name = meta.get("name")
    if not name:
        raise PromptError(f"prompt file {path} front-matter is missing 'name'")
    return PromptSpec(
        name=name,
        version=int(meta.get("version", "1")),
        body=body,
        model_class=meta.get("model_class", "coder"),
        input_budget=_int_or_none(meta.get("input_budget")),
        output_budget=_int_or_none(meta.get("output_budget")),
        schema_ref=meta.get("schema_ref"),
        temperature=_float_or_none(meta.get("temperature")),
    )


def _int_or_none(value: str | None) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def _float_or_none(value: str | None) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


__all__ = [
    "FilePromptStore",
    "InMemoryPromptStore",
    "PromptError",
    "PromptNotFound",
    "PromptRenderError",
    "PromptSpec",
    "PromptStore",
    "load_prompt_file",
    "render_prompt",
]
