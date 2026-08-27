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
"""

from .agents import (
    AGENT_NAME,
    PRIORITIES,
    RISK_LEVELS,
    SUGGESTED_TEST_TYPES,
    TEST_CASE_TYPES,
    TEST_DESIGNER_NAME,
    RequirementAgent,
    RequirementAgentResult,
    RequirementAnalysis,
    RequirementInput,
    TestCase,
    TestDesignAgent,
    TestDesignAgentResult,
    TestDesignInput,
    TestSuite,
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
    "DEFAULT_REDACTOR",
    "FilePromptStore",
    "InMemoryPromptStore",
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
    "render_prompt",
]
