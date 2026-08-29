"""The Automation Agent — generated test code from repo conventions (S2.3).

Build bible §19 Phase 2: "Test automation: generate tests using extracted
conventions." The agent:

1. loads its prompt from the registry (§31.6 — ``test-automator@1``),
2. renders it with the approved test case (the S1.2 :class:`TestCase`) plus
   the target repository's shared S2.x contract — ``RepositoryProfile``
   (S2.1) and ``TestConventions`` (S2.2) from ``qa_copilot_domain``,
3. calls the model through the gateway (§31.1),
4. validates the output into a :class:`GeneratedTest` — the §21 gate input
   (schema-valid + convention-respecting + lint/type-passing).

Like its siblings the agent is **pure**: it takes a
:class:`~qa_copilot_ai.prompts.PromptStore` and an
:class:`~qa_copilot_ai.gateway.LLMGateway` and returns an
:class:`AutomationAgentResult` (validated output + audit payload). It never
touches the database or the file system — the caller persists the generated
test and runs the lint/type gate (``qa_copilot_ai.automation.checker``).

**Output contract.** The model answers with (1) a single-line JSON metadata
object (``file_path`` / ``language`` / ``framework``) and (2) the complete
file content in one fenced code block. :func:`parse_generated_test` is
tolerant (fences, surrounding whitespace, stray prose) but strict about the
contract — a broken output raises ``ValueError`` (fail loud, §31.7).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator
from qa_copilot_domain import RepositoryProfile, TestConventions
from qa_copilot_domain import TestCase as DomainTestCase

from ..config import load_model_settings
from ..gateway import AICallResult, LLMGateway
from ..prompts import PromptStore, render_prompt
from .test_design import TestCase

#: The agent's registry name (build bible §31.6).
AUTOMATOR_NAME = "test-automator"

#: Fixed wire vocabularies — the model may only use these.
LANGUAGES: frozenset[str] = frozenset({"typescript", "javascript", "python"})
FRAMEWORKS: frozenset[str] = frozenset({"playwright", "vitest", "jest", "mocha", "pytest"})

#: Which languages each framework runs in (cross-field constraint).
_FRAMEWORK_LANGUAGES: dict[str, frozenset[str]] = {
    "playwright": frozenset({"typescript", "javascript"}),
    "vitest": frozenset({"typescript", "javascript"}),
    "jest": frozenset({"typescript", "javascript"}),
    "mocha": frozenset({"typescript", "javascript"}),
    "pytest": frozenset({"python"}),
}

#: A repo-relative test-file name (the §21 gate only accepts test files).
_TEST_FILE_RE = re.compile(
    r"\.(?:spec|test)\.(?:m?c?js|tsx?)"  # *.spec.ts / *.test.mjs / *.spec.jsx …
    r"|test_[A-Za-z0-9_\-]*\.py"  # tests/test_api.py
    r"|[A-Za-z0-9_\-]+_test\.py"  # tests/api_test.py
)

#: A fenced code block: the first ```…``` span in the model output.
_FENCE_RE = re.compile(r"```[^\n]*\n(.*?)```", re.DOTALL)


class GeneratedTest(BaseModel):
    """Schema-validated output of the Automation Agent (build bible §21).

    One generated test file: where it goes in the target repository
    (``file_path``), what it is (``language`` / ``framework``), and the
    complete ``content``. Validated by the same schema the agents are held
    to (§12 style) before it may enter the convention + lint/type gate.
    """

    __test__ = False

    file_path: str = Field(
        min_length=1, description="Repo-relative POSIX path of the new test file"
    )
    language: str = Field(description="One of: typescript, javascript, python")
    framework: str = Field(description="One of: playwright, vitest, jest, mocha, pytest")
    content: str = Field(min_length=1, description="Complete file content")
    notes: list[str] = Field(
        default_factory=list,
        description="Assumptions the automation made (empty when none)",
    )

    @field_validator("file_path")
    @classmethod
    def _check_file_path(cls, value: str) -> str:
        if value.startswith(("/", "\\")) or "\\" in value:
            raise ValueError(
                "file_path must be a repo-relative POSIX path (no absolute paths, no backslashes)"
            )
        parts = value.split("/")
        if ".." in parts or parts[0] == ".":
            raise ValueError("file_path must not escape the repository root")
        if _TEST_FILE_RE.search(value) is None:
            raise ValueError(
                "file_path must be a test file (e.g. e2e/counter.spec.ts or tests/test_api.py)"
            )
        return value

    @field_validator("language")
    @classmethod
    def _check_language(cls, value: str) -> str:
        if value not in LANGUAGES:
            raise ValueError(f"unknown language {value!r}; allowed: {sorted(LANGUAGES)}")
        return value

    @field_validator("framework")
    @classmethod
    def _check_framework(cls, value: str) -> str:
        if value not in FRAMEWORKS:
            raise ValueError(f"unknown framework {value!r}; allowed: {sorted(FRAMEWORKS)}")
        return value

    @model_validator(mode="after")
    def _check_language_for_framework(self) -> GeneratedTest:
        allowed = _FRAMEWORK_LANGUAGES[self.framework]
        if self.language not in allowed:
            raise ValueError(
                f"framework {self.framework!r} does not run in {self.language!r}; "
                f"allowed: {sorted(allowed)}"
            )
        return self


@dataclass(frozen=True, slots=True)
class AutomationInput:
    """What the agent automates: the approved test case + the repo contract.

    ``repository_profile`` (S2.1) and ``conventions`` (S2.2) are the shared
    S2.x contract from ``qa_copilot_domain`` — the same objects the S2.2
    extractor produces and the S2.4 diff review will reuse.

    ``test_case`` accepts the S1.2 suite-local :class:`TestCase` (golden
    fixtures / S2.3 runner) or the domain ``TestCase`` entity (S2.4 job,
    loaded from the DB) — both render to the same prompt variables.
    """

    test_case: TestCase | DomainTestCase
    repository_profile: RepositoryProfile
    conventions: TestConventions


@dataclass(frozen=True, slots=True)
class AutomationAgentResult:
    """Everything the caller needs: the validated generated test + audit."""

    test: GeneratedTest
    call: AICallResult
    prompt_ref: str


def _dump_json(model: BaseModel) -> str:
    """A rendered-prompt variable: stable, human-readable JSON (mode json)."""
    return json.dumps(model.model_dump(mode="json"), indent=2, ensure_ascii=False)


def _scan_object_end(text: str, start: int) -> int:
    """Index of the ``}`` closing the object opened at ``start``.

    String-aware (braces inside JSON strings don't count) — the object may
    be embedded in prose or fences without breaking the scan.
    """
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in "{[":
            depth += 1
        elif char in "}]":
            depth -= 1
            if depth == 0:
                return index
    raise ValueError("automation output has an unterminated JSON metadata object")


def _extract_code(rest: str) -> str:
    """The generated code: the first fenced block after the metadata (or raw)."""
    match = _FENCE_RE.search(rest)
    code = (match.group(1) if match is not None else rest).strip()
    if not code:
        raise ValueError("automation output has no generated code after the JSON metadata")
    return code


def parse_generated_test(text: str) -> GeneratedTest:
    """Parse model output (JSON metadata + fenced code) into a :class:`GeneratedTest`.

    Raises:
        ValueError: no metadata object, invalid JSON, a schema violation, or
            missing code — the job fails loud (schema-valid gate, §31.7).
    """
    start = text.find("{")
    if start == -1:
        raise ValueError("automation output has no JSON metadata object")
    end = _scan_object_end(text, start)
    try:
        meta = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise ValueError(f"automation output JSON metadata is not valid JSON: {exc}") from exc
    if not isinstance(meta, dict):
        raise ValueError("automation output JSON metadata must be an object")
    code = _extract_code(text[end + 1 :])
    notes = meta.get("notes", [])
    if not isinstance(notes, list) or not all(isinstance(note, str) for note in notes):
        notes = []
    try:
        return GeneratedTest(
            file_path=meta["file_path"],
            language=meta["language"],
            framework=meta["framework"],
            content=code,
            notes=notes,
        )
    except KeyError as exc:
        raise ValueError(f"automation output JSON metadata is missing key: {exc}") from exc
    except ValidationError as exc:
        raise ValueError(f"automation output failed the GeneratedTest schema: {exc}") from exc


class AutomationAgent:
    """Test-automation agent (prompt ``test-automator@1``, §31.6).

    Consumes the approved :class:`TestCase` (S1.2) plus the target repo's
    ``RepositoryProfile`` / ``TestConventions`` (S2.1/S2.2) and returns one
    generated test file, validated, with the full audit payload.
    """

    def __init__(
        self,
        store: PromptStore,
        gateway: LLMGateway,
        *,
        prompt_name: str = AUTOMATOR_NAME,
        prompt_version: int | None = None,
    ) -> None:
        self._store = store
        self._gateway = gateway
        self._prompt_name = prompt_name
        self._prompt_version = prompt_version

    def _variables(self, automation_input: AutomationInput) -> dict[str, str]:
        return {
            "repository_profile": _dump_json(automation_input.repository_profile),
            "conventions": _dump_json(automation_input.conventions),
            "test_case": _dump_json(automation_input.test_case),
        }

    async def run(self, automation_input: AutomationInput) -> AutomationAgentResult:
        """Automate one test case; returns the generated test + audit payload.

        Raises ``ValueError`` when the model output is not schema-valid
        (see :func:`parse_generated_test`), or ``PromptNotFound`` when the
        registry has no such prompt.
        """
        spec = self._store.get(self._prompt_name, self._prompt_version)
        rendered = render_prompt(spec, **self._variables(automation_input))
        messages = [{"role": "user", "content": rendered}]
        # §9 budgets: the prompt's own values win; the AI_* environment
        # defaults (qa_copilot_ai.config) are the fallback.
        settings = load_model_settings()
        call = await self._gateway.chat(
            messages,
            agent=AUTOMATOR_NAME,
            temperature=spec.temperature if spec.temperature is not None else settings.temperature,
            max_tokens=(
                spec.output_budget if spec.output_budget is not None else settings.max_output_tokens
            ),
            max_input_tokens=(
                spec.input_budget if spec.input_budget is not None else settings.max_input_tokens
            ),
        )
        test = parse_generated_test(call.text)
        return AutomationAgentResult(test=test, call=call, prompt_ref=spec.ref)
