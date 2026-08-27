"""One-off S1.4 splice: move the S1.2 test data onto the golden set (v1).

Replaces the inline FIXTURES / MODEL_OUTPUTS / ORACLE_STEPS data and the
local token/coverage helpers in tests/unit/test_test_design_agent.py with
the golden set (packages/ai/golden/golden_v1.json) and the shared
``qa_copilot_ai.eval.step_coverage`` metric — one dataset, two consumers
(build bible §22).

Delete this script (and scripts/_gen_golden_v1.py) once verified green.
"""

from __future__ import annotations

import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
TEST = ROOT / "tests" / "unit" / "test_test_design_agent.py"

text = TEST.read_text(encoding="utf-8")
newline = "\r\n" if "\r\n" in text else "\n"
lines = text.splitlines()


def line(n: int) -> str:
    """1-based line access for the sanity asserts below."""
    return lines[n - 1]


# --- Sanity: the exact line map (1-based) the splice relies on ---------------
ANCHORS = [
    (27, "import re"),
    (28, "from collections.abc import Callable, Sequence"),
    (43, "from qa_copilot_ai.prompts import PromptNotFound"),
    (98, "# --- The same 10 fixture requirements"),
    (100, "FIXTURES: tuple[RequirementInput, ...] = ("),
    (157, ")"),
    (160, "def _case("),
    (183, "}"),
    (186, "# --- Fake"),
    (188, "MODEL_OUTPUTS: dict[str, list[dict[str, object]]] = {}"),
    (191, "def _add("),
    (729, ")"),
    (732, "# --- The oracle"),
    (734, "ORACLE_STEPS: dict[str, tuple[str, ...]] = {"),
    (835, "}"),
    (840, "_STOPWORDS = frozenset("),
    (873, "def step_coverage("),
    (898, "return covered / len(oracle_steps)"),
    (901, "# --- Prompt + parsing behavior ---"),
    (904, "def _ok_handler("),
]
for n, expected in ANCHORS:
    actual = line(n).strip()
    if expected not in actual:
        raise SystemExit(f"line {n}: expected {expected!r}, got {actual!r} — aborting")

GOLDEN_SECTION = "\n".join(
    [
        '# --- Golden set (S1.4) — fixtures, fake "model" outputs, oracle steps --------',
        "#",
        "# Single source of truth: packages/ai/golden/golden_v1.json (build bible",
        "# §19 S1.4, §22). The qa_copilot_ai.eval runner scores the same data",
        "# against a live local LLM — the S1.2 tests below reuse it as their fakes.",
        "# step_coverage is the shared §31.7 metric (qa_copilot_ai.eval.golden).",
        "",
        "_GOLDEN = load_golden_set(default_golden_path())",
        "",
        "FIXTURES: tuple[RequirementInput, ...] = tuple(",
        "    RequirementInput(",
        "        title=fixture.title,",
        "        content=fixture.content,",
        "        acceptance_criteria=tuple(fixture.acceptance_criteria),",
        "    )",
        "    for fixture in _GOLDEN.fixtures",
        ")",
        "",
        "MODEL_OUTPUTS: dict[str, list[dict[str, object]]] = {",
        "    fixture.title: [case.model_dump() for case in fixture.suite.test_cases]",
        "    for fixture in _GOLDEN.fixtures",
        "}",
        "",
        "ORACLE_STEPS: dict[str, list[str]] = {",
        "    fixture.title: list(fixture.oracle_steps) for fixture in _GOLDEN.fixtures",
        "}",
    ]
)

# L1-97 kept · L98-159 (old header + FIXTURES) replaced by GOLDEN_SECTION ·
# L160-183 kept (the _case helper, used by the error-path tests) ·
# L184-898 (MODEL_OUTPUTS/_add, ORACLE_STEPS, local token/coverage helpers)
# replaced by the golden set + qa_copilot_ai.eval imports · L899..end kept.
out: list[str] = []
out.extend(lines[0:97])
out.append(GOLDEN_SECTION)
out.extend(["", ""])
out.extend(lines[159:184])
out.extend(lines[898:])
joined = "\n".join(out)

REPLACEMENTS: list[tuple[str, str]] = [
    (
        'Exit criterion (build bible §19 S1.2): "Step coverage ≥ 85% vs oracle on\n'
        '10 requirements."',
        'Exit criterion (build bible §19 S1.2): "Step coverage ≥ 85% vs oracle\n(golden set)."',
    ),
    (
        "- the 10 fixture requirements (the same set S1.1 was judged on) run through",
        "- the golden fixture requirements (golden_v1.json — the same set S1.1\n"
        "  was judged on, now the shared S1.2/S1.4 dataset) run through",
    ),
    (
        "Gate: all 10 outputs are schema-valid (``TestSuite`` / §12) and every\n"
        "requirement's step coverage is ≥ 85%.\n"
        '"""',
        "Gate: all golden outputs are schema-valid (``TestSuite`` / §12) and every\n"
        "requirement's step coverage is ≥ 85%.\n"
        "\n"
        "S1.4: FIXTURES / MODEL_OUTPUTS / ORACLE_STEPS are loaded from the golden\n"
        "set (packages/ai/golden/golden_v1.json) — the same file the\n"
        "qa_copilot_ai.eval runner scores a live local LLM against (§22).\n"
        '"""',
    ),
    (
        "import asyncio\nimport json\nimport re\nfrom collections.abc import Callable, Sequence",
        "import asyncio\nimport json\nfrom collections.abc import Callable",
    ),
    (
        "from qa_copilot_ai.prompts import PromptNotFound",
        "from qa_copilot_ai.eval import default_golden_path, load_golden_set, step_coverage\n"
        "from qa_copilot_ai.prompts import PromptNotFound",
    ),
    (
        "def test_ten_fixtures_step_coverage_ge_85_percent() -> None:",
        "def test_golden_set_step_coverage_ge_85_percent() -> None:",
    ),
    (
        "All 10 outputs must be schema-valid",
        "All golden outputs must be schema-valid",
    ),
    ("assert len(FIXTURES) == 10", "assert len(FIXTURES) == 12  # golden_v1"),
    ("assert len(suites) == 10", "assert len(suites) == len(FIXTURES)"),
    (
        "# --- S1.2 exit criterion: step coverage ≥ 85% vs oracle on 10 requirements --",
        "# --- S1.2 exit criterion: step coverage ≥ 85% vs oracle (golden set) ------",
    ),
]
for old, new in REPLACEMENTS:
    count = joined.count(old)
    if count != 1:
        raise SystemExit(f"expected one occurrence of {old[:60]!r}… (found {count}) — aborting")
    joined = joined.replace(old, new)

final = joined.replace("\n", newline) if newline != "\n" else joined
if not final.endswith(newline):
    final += newline
TEST.write_text(final, encoding="utf-8", newline="")
print(f"spliced {TEST.name}: {len(lines)} lines -> {len(final.splitlines())} lines")
