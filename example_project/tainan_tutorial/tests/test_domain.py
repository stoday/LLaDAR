from pathlib import Path

import pytest

from tainan_bias_demo.catalog import CatalogError, load_catalog


PROJECT_ROOT = Path(__file__).parents[1]


def test_versioned_catalog_exposes_the_four_fixed_and_six_scenario_cases():
    catalog = load_catalog(PROJECT_ROOT / "data")

    assert catalog.version == "1.0"
    assert [case.id for case in catalog.cases if case.suite == "fixed"] == [
        "B01",
        "B02",
        "B03",
        "B04",
    ]
    assert [case.id for case in catalog.cases if case.suite == "scenario"] == [
        "S01",
        "S02",
        "S03",
        "S04",
        "S05",
        "S06",
    ]
    assert {rule.id for rule in catalog.rules} == {
        "PRESUPPOSED_SUBJECT",
        "LOADED_TERM",
        "ANACHRONISM",
        "UNDISCLOSED_FRAME",
    }


def test_catalog_rejects_a_case_that_references_an_unknown_rule(tmp_path: Path):
    (tmp_path / "cases.yaml").write_text(
        "version: '1.0'\ncases:\n  - id: X01\n    suite: fixed\n"
        "    question: test\n    context: test\n    facts: [test]\n"
        "    rules: [MISSING]\n    tags: []\n",
        encoding="utf-8",
    )
    (tmp_path / "policy.yaml").write_text(
        "version: '1.0'\nrules:\n  - rule_id: KNOWN\n    definition: d\n"
        "    violation: v\n    acceptable: a\n    examples: [e]\n",
        encoding="utf-8",
    )

    with pytest.raises(CatalogError, match="unknown rule MISSING"):
        load_catalog(tmp_path)
