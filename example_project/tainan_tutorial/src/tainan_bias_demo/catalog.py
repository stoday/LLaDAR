from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .domain import Catalog, DemoCase, PolicyRule


class CatalogError(ValueError):
    """Raised when versioned demo data is incomplete or inconsistent."""


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise CatalogError(f"missing catalog file: {path.name}")
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise CatalogError(f"{path.name} must contain a mapping")
    return loaded


def load_catalog(data_dir: str | Path) -> Catalog:
    root = Path(data_dir)
    cases_data = _read_yaml(root / "cases.yaml")
    policy_data = _read_yaml(root / "policy.yaml")
    if cases_data.get("version") != policy_data.get("version"):
        raise CatalogError("cases and policy versions must match")

    rules = tuple(
        PolicyRule(
            id=item["rule_id"],
            definition=item["definition"],
            violation=item["violation"],
            acceptable=item["acceptable"],
            examples=tuple(item.get("examples", ())),
        )
        for item in policy_data.get("rules", ())
    )
    known_rules = {rule.id for rule in rules}
    cases: list[DemoCase] = []
    seen_ids: set[str] = set()
    for item in cases_data.get("cases", ()):
        case_id = item["id"]
        if case_id in seen_ids:
            raise CatalogError(f"duplicate case id {case_id}")
        seen_ids.add(case_id)
        for rule_id in item.get("rules", ()):
            if rule_id not in known_rules:
                raise CatalogError(f"case {case_id} references unknown rule {rule_id}")
        suite = item["suite"]
        if suite not in {"fixed", "scenario"}:
            raise CatalogError(f"case {case_id} has invalid suite {suite}")
        cases.append(
            DemoCase(
                id=case_id,
                suite=suite,
                question=item["question"],
                context=item["context"],
                facts=tuple(item.get("facts", ())),
                rules=tuple(item.get("rules", ())),
                tags=tuple(item.get("tags", ())),
            )
        )
    return Catalog(version=str(cases_data["version"]), cases=tuple(cases), rules=rules)

