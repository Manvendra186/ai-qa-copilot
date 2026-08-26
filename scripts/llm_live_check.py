"""S0.6 live check — one real LLM call that logs ``tokens_in``/``tokens_out``.

Exit criterion (build bible §19 S0.6): "unit tests green against a fake
server; **one live call logs ``tokens_in``/``tokens_out``**".

Run with:

    uv run python scripts/llm_live_check.py

Requires ``LLM_BASE_URL`` / ``LLM_MODEL`` in ``.env`` and a running local
LLM server (LM Studio on :8080 by default).
"""

from __future__ import annotations

import asyncio
import json
import sys

from qa_copilot_ai import AICallResult, LLMGateway
from qa_copilot_api.config import get_settings
from qa_copilot_api.logging_config import configure_logging


async def _run(gateway: LLMGateway, messages: list[dict[str, str]]) -> AICallResult:
    try:
        # 1) the required live call (non-streaming, usage reported by server)
        result = await gateway.chat(messages, agent="llm-live-check")
        # 2) same prompt over the streaming path (verifies chat_stream +
        #    stream_options.include_usage end-to-end)
        chunks = [chunk async for chunk in gateway.chat_stream(messages, agent="llm-live-check")]
        stream_text = "".join(chunk.text for chunk in chunks)
        if stream_text.strip():
            print(f"stream reply: {stream_text.strip()}")
        return result
    finally:
        await gateway.aclose()


def main() -> int:
    settings = get_settings()
    if not settings.llm_base_url or not settings.llm_model:
        print("LLM_BASE_URL / LLM_MODEL are not set — see .env.example (LLM section).")
        return 1
    configure_logging("INFO")
    messages = [
        {"role": "system", "content": "You are a precise assistant. Reply in one short sentence."},
        {"role": "user", "content": "Reply with exactly: QA copilot S0.6 live check OK."},
    ]
    gateway = LLMGateway(base_url=settings.llm_base_url, model=settings.llm_model)
    result = asyncio.run(_run(gateway, messages))
    print("audit payload (what an ai_actions row stores):")
    print(json.dumps(result.audit_dict(), indent=2))
    print(f"model reply: {result.text.strip()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
