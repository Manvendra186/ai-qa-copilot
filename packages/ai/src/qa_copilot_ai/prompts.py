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


__all__ = [
    "InMemoryPromptStore",
    "PromptError",
    "PromptNotFound",
    "PromptRenderError",
    "PromptSpec",
    "PromptStore",
    "render_prompt",
]
