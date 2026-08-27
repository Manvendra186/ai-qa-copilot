"""ONE-OFF (S1.4): emit ``packages/ai/golden/golden_v1.json``.

The S1.2 test module (tests/unit/test_test_design_agent.py) carries the
fixture requirements, the competent-model "golden" suites, and the QA
oracles inline. S1.4 promotes them into the §22 golden set file. Run once
with ``uv run python scripts/_gen_golden_v1.py`` and delete this script.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
TEST_FILE = ROOT / "tests" / "unit" / "test_test_design_agent.py"
OUT = ROOT / "packages" / "ai" / "golden" / "golden_v1.json"

#: §22 workflow category per S1.2 fixture (all of: auth, search, checkout,
#: payments, permissions — profile/upload added below as hand-authored).
CATEGORIES = {
    "Login with email and password": "auth",
    "Password reset via email": "auth",
    "Search products by name": "search",
    "Add item to cart": "checkout",
    "Checkout with saved card": "payments",
    "Order history": "checkout",
    "Cancel an order": "checkout",
    "Apply discount code": "checkout",
    "Email receipt": "checkout",
    "Admin dashboard": "permissions",
}

spec = importlib.util.spec_from_file_location("s12_tests", TEST_FILE)
assert spec is not None and spec.loader is not None
mod = importlib.util.module_from_spec(spec)
sys.modules["s12_tests"] = mod
spec.loader.exec_module(mod)

fixtures: list[dict[str, object]] = []
for index, fixture in enumerate(mod.FIXTURES, start=1):
    title = fixture.title
    if title not in CATEGORIES:
        raise SystemExit(f"unmapped fixture title: {title!r}")
    fixtures.append(
        {
            "id": f"REQ-{index:03d}",
            "title": title,
            "content": fixture.content,
            "category": CATEGORIES[title],
            "acceptance_criteria": list(fixture.acceptance_criteria),
            "oracle_steps": list(mod.ORACLE_STEPS[title]),
            "suite": {"test_cases": mod.MODEL_OUTPUTS[title]},
        }
    )

# --- Two hand-authored fixtures so v1 covers all §22 workflow categories ----


def _case(
    case_id: str,
    title: str,
    case_type: str,
    steps: list[str],
    expected: list[str],
    *,
    requirement_title: str,
    priority: str = "high",
    risk: str = "medium",
    preconditions: list[str] | None = None,
) -> dict[str, object]:
    return {
        "id": case_id,
        "title": title,
        "type": case_type,
        "priority": priority,
        "preconditions": preconditions or [],
        "steps": steps,
        "expected_results": expected,
        "risk": risk,
        "requirement_refs": [requirement_title],
    }


PROFILE = "Update user profile"
UPLOAD = "Upload product image"

fixtures.append(
    {
        "id": "REQ-011",
        "title": PROFILE,
        "content": "Users can update their profile: display name, email address, and avatar.",
        "category": "profile",
        "acceptance_criteria": [
            "Display name is at most 60 characters",
            "Email must be unique and valid",
            "Avatar is limited to 2 MB and image formats only",
            "Changes are visible immediately on the profile page",
        ],
        "oracle_steps": [
            "Sign in with a registered account",
            "Open the profile settings page",
            "Change the display name to a new value",
            "Save the profile changes",
            "Verify the display name updates on the profile page",
            "Set the email to a value already used by another account",
            "Verify a duplicate email error is shown and the email is unchanged",
            "Upload a 5 MB file as the avatar",
            "Verify the size limit error is shown",
            "Upload a valid PNG avatar under 2 MB",
            "Verify the new avatar is displayed",
        ],
        "suite": {
            "test_cases": [
                _case(
                    "TC-001",
                    "Update display name and see it applied",
                    "functional",
                    [
                        "Sign in with a registered account",
                        "Open the profile settings page",
                        "Change the display name to a new value",
                        "Save the profile changes",
                        "Verify the display name updates on the profile page",
                    ],
                    ["The profile page shows the new display name"],
                    requirement_title=PROFILE,
                    risk="low",
                    preconditions=["A user account exists"],
                ),
                _case(
                    "TC-002",
                    "A duplicate email is rejected",
                    "negative",
                    [
                        "Sign in with a registered account",
                        "Open the profile settings page",
                        "Set the email to a value already used by another account",
                        "Save the profile changes",
                        "Verify a duplicate email error is shown and the email is unchanged",
                    ],
                    ["The error names the duplicate email and the old email remains"],
                    requirement_title=PROFILE,
                ),
                _case(
                    "TC-003",
                    "Avatar size limit is enforced at 2 MB",
                    "boundary",
                    [
                        "Open the profile settings page",
                        "Upload a 5 MB file as the avatar",
                        "Verify the size limit error is shown",
                        "Upload a valid PNG avatar under 2 MB",
                        "Verify the new avatar is displayed",
                    ],
                    ["The oversized file is rejected", "The valid avatar is shown"],
                    requirement_title=PROFILE,
                    preconditions=["A user account exists"],
                ),
                _case(
                    "TC-004",
                    "Profile updates require authentication",
                    "security",
                    [
                        "Open the profile settings page while signed out",
                        "Verify a sign-in prompt is shown",
                        "Verify no profile data is visible before sign-in",
                    ],
                    ["The profile is not editable before sign-in"],
                    requirement_title=PROFILE,
                    risk="high",
                ),
            ]
        },
    }
)

fixtures.append(
    {
        "id": "REQ-012",
        "title": UPLOAD,
        "content": "Merchants can upload an image for a product from the product editor.",
        "category": "upload",
        "acceptance_criteria": [
            "Only JPG, PNG, and WEBP are accepted",
            "Files larger than 5 MB are rejected",
            "The image is previewed before saving",
        ],
        "oracle_steps": [
            "Sign in with a merchant account",
            "Open the product editor for a product",
            "Choose a valid PNG image under 5 MB",
            "Verify the image preview appears",
            "Save the product",
            "Verify the image is shown on the product page",
            "Choose a text file as the image",
            "Verify an unsupported format error is shown",
            "Choose a 6 MB image file",
            "Verify the size limit error is shown",
        ],
        "suite": {
            "test_cases": [
                _case(
                    "TC-001",
                    "Upload a valid product image end to end",
                    "functional",
                    [
                        "Sign in with a merchant account",
                        "Open the product editor for a product",
                        "Choose a valid PNG image under 5 MB",
                        "Verify the image preview appears",
                        "Save the product",
                        "Verify the image is shown on the product page",
                    ],
                    ["The product page shows the uploaded image"],
                    requirement_title=UPLOAD,
                    risk="low",
                    preconditions=["A merchant account owns at least one product"],
                ),
                _case(
                    "TC-002",
                    "Unsupported file types are rejected",
                    "negative",
                    [
                        "Open the product editor for a product",
                        "Choose a text file as the image",
                        "Verify an unsupported format error is shown",
                    ],
                    ["Only JPG, PNG, and WEBP are accepted"],
                    requirement_title=UPLOAD,
                    preconditions=["A merchant account owns at least one product"],
                ),
                _case(
                    "TC-003",
                    "Files over 5 MB are rejected",
                    "boundary",
                    [
                        "Open the product editor for a product",
                        "Choose a 6 MB image file",
                        "Verify the size limit error is shown",
                    ],
                    ["The large file is rejected with a size error"],
                    requirement_title=UPLOAD,
                    preconditions=["A merchant account owns at least one product"],
                ),
                _case(
                    "TC-004",
                    "The upload is scoped to the merchant's own products",
                    "security",
                    [
                        "Sign in with a merchant account",
                        "Open the product editor for another merchant's product",
                        "Verify access to the editor is denied",
                    ],
                    ["A merchant cannot upload for products they do not own"],
                    requirement_title=UPLOAD,
                    risk="high",
                ),
            ]
        },
    }
)

doc = {
    "schema_version": 1,
    "name": "AI QA Copilot golden set",
    "version": "v1",
    "description": (
        "Build Bible §22 evaluation dataset, v1: 12 synthetic SaaS requirements "
        "spanning all seven §22 workflow categories (auth, search, checkout, "
        "payments, permissions, profile, upload). Each fixture carries a "
        "hand-authored QA oracle (the independent expected behavior, §19 S1.2) "
        "and a golden test suite — a competent model's reference output, "
        "validated by the §12 TestSuite schema. Pinned prompt: "
        "test-designer@1 (§31.6). Targets per §31.7. Consumed by the S1.4 "
        "eval runner and the S1.2 unit tests."
    ),
    "source": {
        "build_bible": "docs/AI_QA_Copilot_Build_Bible_v1.1.md",
        "dataset_section": "22",
        "targets_section": "31.7",
        "prompt": "test-designer@1",
    },
    "targets": {
        "schema_valid_min": 0.99,
        "oracle_step_coverage_min": 0.85,
    },
    "categories": sorted({fixture["category"] for fixture in fixtures}),
    "fixtures": fixtures,
}

OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
print(f"wrote {OUT.relative_to(ROOT)} ({len(fixtures)} fixtures)")
