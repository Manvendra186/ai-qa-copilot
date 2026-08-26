"""LLM gateway, prompts, and redaction (build bible §7, §31.1, §31.6, §31.7).

S0.6: OpenAI-compatible gateway (streaming + token accounting → ``ai_actions``
payload), secret redaction, and the prompt-registry types. **All model calls
go through :class:`LLMGateway`** — agents never call the model directly
(§31.1).
"""

from .gateway import (
    AICallResult,
    AIChunk,
    LLMError,
    LLMGateway,
    TokenUsage,
)
from .prompts import (
    InMemoryPromptStore,
    PromptError,
    PromptNotFound,
    PromptRenderError,
    PromptSpec,
    PromptStore,
    render_prompt,
)
from .redaction import DEFAULT_REDACTOR, REDACTED, Redactor, RedactResult

__version__ = "0.1.0"

__all__ = [
    "AIChunk",
    "AICallResult",
    "DEFAULT_REDACTOR",
    "InMemoryPromptStore",
    "LLMError",
    "LLMGateway",
    "PromptError",
    "PromptNotFound",
    "PromptRenderError",
    "PromptSpec",
    "PromptStore",
    "REDACTED",
    "RedactResult",
    "Redactor",
    "TokenUsage",
    "render_prompt",
]
