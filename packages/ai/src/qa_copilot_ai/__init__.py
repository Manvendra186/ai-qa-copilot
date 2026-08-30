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

S4.1: the Failure Investigator (build bible §19 Phase 4) — prompt v1 +
schema-validated :class:`Diagnosis` (the §12 failure-analysis contract) over
the S3.3 :class:`~qa_copilot_domain.entities.NormalizedFailure`; the
top-1 ≥ 80% golden-set gate lives in :mod:`qa_copilot_ai.investigator`.

S4.2: the Fix Agent (build bible §19 Phase 4) — prompt v1 +
schema-validated :class:`FixProposal` (patch or decline, §26 category guard)
derived from the broken test file + S3.3 failure + S4.1 diagnosis; the
≥ 5/10 applicable-and-passing gate lives in :mod:`qa_copilot_ai.fixer`.
"""

from .agents import (
    AGENT_NAME,
    AUTOMATOR_NAME,
    FIXER_NAME,
    FRAMEWORKS,
    INVESTIGATOR_NAME,
    LANGUAGES,
    PRIORITIES,
    RISK_LEVELS,
    SUGGESTED_TEST_TYPES,
    TEST_CASE_TYPES,
    TEST_DESIGNER_NAME,
    AutomationAgent,
    AutomationAgentResult,
    AutomationInput,
    Diagnosis,
    FailureInvestigatorAgent,
    FailureInvestigatorAgentResult,
    FixerAgent,
    FixerAgentResult,
    FixerInput,
    FixProposal,
    GeneratedTest,
    InvestigatorInput,
    RequirementAgent,
    RequirementAgentResult,
    RequirementAnalysis,
    RequirementInput,
    TestCase,
    TestDesignAgent,
    TestDesignAgentResult,
    TestDesignInput,
    TestSuite,
    parse_diagnosis,
    parse_fix_proposal,
    parse_generated_test,
)
from .config import ModelSettings, load_dotenv, load_extra_body, load_model_settings
from .gateway import (
    AICallResult,
    AIChunk,
    LLMError,
    LLMGateway,
    LLMInputBudgetError,
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
    "Diagnosis",
    "FRAMEWORKS",
    "FailureInvestigatorAgent",
    "FailureInvestigatorAgentResult",
    "FilePromptStore",
    "FIXER_NAME",
    "FixerAgent",
    "FixerAgentResult",
    "FixerInput",
    "FixProposal",
    "GeneratedTest",
    "INVESTIGATOR_NAME",
    "InMemoryPromptStore",
    "InvestigatorInput",
    "LANGUAGES",
    "LLMError",
    "LLMGateway",
    "LLMInputBudgetError",
    "ModelSettings",
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
    "load_dotenv",
    "load_extra_body",
    "load_model_settings",
    "load_prompt_file",
    "parse_diagnosis",
    "parse_fix_proposal",
    "parse_generated_test",
    "render_prompt",
]
