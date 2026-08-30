"""Live spec executor for the S4.3 loop — real Playwright against the demo app.

``PlaywrightLoopRunner`` adapts the S4.2
:class:`~qa_copilot_ai.fixer.live.PlaywrightVerifier` (demo-stack
management + spec execution) to the loop's ``LoopSpecRunner`` protocol:
the loop's S3 runs — the initial broken spec and the re-run of the
patched spec — execute against the demo app with the target's defect
flags active (``app_env``), exactly like the S4.2 live gate.

The verifier owns the demo stack lifecycle: close the runner (``await
runner.aclose()``) when the loop is done — the CLI does this in a
``finally`` (same contract as the S4.2 CLI).
"""

from __future__ import annotations

from typing import Protocol

from .runner import SpecRun

__all__ = ["PlaywrightLoopRunner", "SpecVerifier"]


class SpecVerifier(Protocol):
    """The minimum of the S4.2 verifier the loop adapter needs.

    :class:`~qa_copilot_ai.fixer.live.PlaywrightVerifier` satisfies this;
    unit tests inject a duck-typed fake (no Playwright, no browser).
    """

    async def run_spec(
        self,
        spec_text: str,
        *,
        spec_name: str,
        flags: frozenset[str],
    ) -> tuple[bool, str]: ...

    async def aclose(self) -> None: ...


class PlaywrightLoopRunner:
    """``LoopSpecRunner`` over the Playwright demo-app verifier."""

    def __init__(self, verifier: SpecVerifier) -> None:
        self._verifier = verifier

    async def run(
        self,
        spec_text: str,
        *,
        spec_name: str,
        flags: frozenset[str],
    ) -> SpecRun:
        """Write *spec_text* to the probe spec, run it, and report pass/fail."""
        ok, detail = await self._verifier.run_spec(spec_text, spec_name=spec_name, flags=flags)
        return SpecRun(ok=ok, detail=detail or None)

    async def aclose(self) -> None:
        """Stop the demo stack the verifier started (no-op otherwise)."""
        await self._verifier.aclose()
