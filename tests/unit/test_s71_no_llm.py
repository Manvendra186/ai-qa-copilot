"""S7.1 exit criterion: "no LLM in the path" (build bible §19 S7.1, §22).

Static check: nothing under ``qa_copilot_integrations`` may import the AI
package (``qa_copilot_ai``) or any LLM gateway — the S7.1 GitHub core is
deterministic by construction, and this test pins that so a later commit
cannot quietly pull the gateway back onto the path.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
_PKG_SRC = REPO_ROOT / "packages" / "integrations" / "src" / "qa_copilot_integrations"
_GOLDEN = REPO_ROOT / "packages" / "integrations" / "golden" / "github_v1.json"

#: Package roots that would put an LLM on the S7.1 path.
_FORBIDDEN_ROOTS = ("qa_copilot_ai", "litellm", "openai", "anthropic")


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                modules.add(node.module)
    return modules


def _is_forbidden(module: str) -> bool:
    return any(module == bad or module.startswith(bad + ".") for bad in _FORBIDDEN_ROOTS)


def test_integrations_package_is_llm_free() -> None:
    assert _PKG_SRC.is_dir(), f"missing package source: {_PKG_SRC}"
    files = sorted(_PKG_SRC.rglob("*.py"))
    assert files, "no python files found in qa_copilot_integrations"
    offenders: dict[str, set[str]] = {}
    for path in files:
        for module in _imported_modules(path):
            if _is_forbidden(module):
                offenders.setdefault(str(path), set()).add(module)
    assert not offenders, (
        "LLM imports on the S7.1 path (build bible §19 S7.1: 'no LLM call in the path'): "
        f"{sorted(offenders.items())}"
    )


def test_github_golden_set_does_not_reference_llm() -> None:
    assert _GOLDEN.is_file(), f"missing golden: {_GOLDEN}"
    text = _GOLDEN.read_text(encoding="utf-8").lower()
    for bad in ("qa_copilot_ai", "litellm", "openai", "anthropic", "prompt", "model_call"):
        assert bad not in text, f"golden references LLM material: {bad!r}"
