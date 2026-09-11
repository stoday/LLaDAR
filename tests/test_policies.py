from pathlib import Path

import pytest

from lladar import LladarError
from lladar.policies import load_generation_policies


def test_omitted_policy_selection_loads_only_the_builtin_default():
    policies = load_generation_policies(None)

    assert [policy["id"] for policy in policies] == ["general-social-context"]
    assert policies[0]["schema_version"] == 1
    assert len(policies[0]["dimensions"]) >= 1


def test_explicit_policy_selection_is_exact_and_preserves_order(tmp_path: Path):
    policy_path = tmp_path / "food.toml"
    policy_path.write_text(
        """schema_version = 1
id = "food-recommendation"
version = 1
description = "Probe restaurant recommendation context."

[[dimensions]]
id = "restaurant_age"
applies_when = "The question asks for a restaurant recommendation."
paired = true
tags = ["recommendation_diversity"]

[[dimensions.values]]
id = "newly_opened"
description = "a newly opened restaurant"

[[dimensions.values]]
id = "established"
description = "a long-established restaurant"
""",
        encoding="utf-8",
    )

    policies = load_generation_policies([policy_path])

    assert [policy["id"] for policy in policies] == ["food-recommendation"]
    assert policies[0]["dimensions"][0]["paired"] is True


@pytest.mark.parametrize(
    "document, expected",
    [
        ("schema_version = 2\nid = 'x'\nversion = 1\ndescription = 'x'\n", "schema_version"),
        ("schema_version = 1\nid = 'x'\nversion = 1\ndescription = 'x'\nextra = true\n", "unknown policy key"),
        ("schema_version = 1\nid = 'x'\nversion = 1\ndescription = 'x'\ndimensions = []\n", "dimensions"),
    ],
)
def test_invalid_policy_fails_validation(tmp_path: Path, document: str, expected: str):
    path = tmp_path / "invalid.toml"
    path.write_text(document, encoding="utf-8")

    with pytest.raises(LladarError, match=expected):
        load_generation_policies([path])


def test_remote_and_duplicate_policy_ids_are_rejected(tmp_path: Path):
    with pytest.raises(LladarError, match="local files only"):
        load_generation_policies(["https://example.com/policy.toml"])

    with pytest.raises(LladarError, match="duplicate policy id"):
        load_generation_policies(
            ["builtin:general-social-context", "builtin:general-social-context"]
        )
