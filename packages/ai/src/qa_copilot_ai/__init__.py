"""LLM gateway, agents, prompts, and redaction (build bible §7, §31.1, §31.6, §31.7).

S0.6: OpenAI-compatible gateway (streaming + token accounting → ``ai_actions``
payload), secret redaction, and the prompt-registry types. **All model calls
go through :class:`LLMGateway`** — agents never call the model directly
(§31.1).

S1.1: the Requirement Agent (build bible §19 Phase 1) — prompt v1 +
schema-validated output through the gateway.

S1.2: the Test Design Agent (build bible §19 Phase 1) — prompt v1 +
schema-validated :class:`TestSuite` (the §12 test-case schema) through the
gateway.

S2.3: the Automation Agent (build bible §19 Phase 2) — prompt v1 +
schema-validated :class:`GeneratedTest` from ``RepositoryProfile`` (S2.1) +
``TestConventions`` (S2.2); the §21 lint/type gate and the golden-set eval
live in :mod:`qa_copilot_ai.automation`.
"""

from .agents import (
    AGENT_NAME,
    AUTOMATOR_NAME,
    FRAMEWORKS,
    LANGUAGES,
    PRIORITIES,
    RISK_LEVELS,
    SUGGESTED_TEST_TYPES,
    TEST_CASE_TYPES,
    TEST_DESIGNER_NAME,
    AutomationAgent,
    AutomationAgentResult,
    AutomationInput,
    GeneratedTest,
    RequirementAgent,
    RequirementAgentResult,
    RequirementAnalysis,
    RequirementInput,
    TestCase,
    TestDesignAgent,
    TestDesignAgentResult,
    TestDesignInput,
    TestSuite,
    parse_generated_test,
)
from .gateway import (
    AICallResult,
    AIChunk,
    LLMError,
    LLMGateway,
    TokenUsage,
)
from .prompts import (
    FilePromptStore,
    InMemoryPromptStore,
    PromptError,
    PromptNotFound,
    PromptRenderError,
    PromptSpec,
    PromptStore,
    load_prompt_file,
    render_prompt,
)
from .redaction import DEFAULT_REDACTOR, REDACTED, Redactor, RedactResult

__version__ = "0.1.0"

__all__ = [
    "AGENT_NAME",
    "AIChunk",
    "AICallResult",
    "AUTOMATOR_NAME",
    "AutomationAgent",
    "AutomationAgentResult",
    "AutomationInput",
    "DEFAULT_REDACTOR",
    "FRAMEWORKS",
    "FilePromptStore",
    "GeneratedTest",
    "InMemoryPromptStore",
    "LANGUAGES",
    "LLMError",
    "LLMGateway",
    "PRIORITIES",
    "PromptError",
    "PromptNotFound",
    "PromptRenderError",
    "PromptSpec",
    "PromptStore",
    "REDACTED",
    "RISK_LEVELS",
    "RedactResult",
    "Redactor",
    "RequirementAgent",
    "RequirementAgentResult",
    "RequirementAnalysis",
    "RequirementInput",
    "SUGGESTED_TEST_TYPES",
    "TEST_CASE_TYPES",
    "TEST_DESIGNER_NAME",
    "TestDesignAgent",
    "TestDesignAgentResult",
    "TestDesignInput",
    "TestCase",
    "TestSuite",
    "TokenUsage",
    "load_prompt_file",
    "parse_generated_test",
    "render_prompt",
]
