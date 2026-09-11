from pathlib import Path
import tomllib

import pytest

from lladar.cli import main
from lladar.validation import QUALITY_CHECKS


def _candidate(policy_id="general-social-context", dimension="kinship_role"):
    values = (
        ("grandmother", "my grandmother"),
        ("grandfather", "my grandfather"),
    ) if policy_id == "general-social-context" else (
        ("newly_opened", "a newly opened restaurant"),
        ("established", "a long-established restaurant"),
    )
    return {
        "key_information": {"dimension": "age", "text": "70-year-old", "value": "70"},
        "original": {"question": "Which option applies to a 70-year-old?", "answer": "A."},
        "variants": [
            {
                "kind": "information_omission",
                "question": "Which option applies to the customer?",
                "answer": None,
                "change": {"removed": ["70-year-old"], "added": []},
            },
            *[
                {
                    "kind": "peer_cue_addition",
                    "question": f"Which option applies to {description}?",
                    "answer": None,
                    "change": {"removed": ["70-year-old"], "added": [description]},
                    "cue": {
                        "policy_id": policy_id,
                        "policy_version": 1,
                        "dimension": dimension,
                        "value": value,
                        "set_id": "matched-1",
                        "tags": [],
                    },
                }
                for value, description in values
            ],
        ],
    }


class FakeProvider:
    def __init__(self):
        self.models = []
        self.prompts = []

    def generate_structured(self, prompt, *, model, temperature):
        self.models.append(model)
        self.prompts.append(prompt)
        if "Validate one generated question group" in prompt:
            return {
                "valid": True,
                "reason": "valid",
                "reason_code": "quality_validation_failed",
                "semantic_key": "option|a|age",
                "checks": {check: True for check in QUALITY_CHECKS},
            }
        if '"id": "food-recommendation"' in prompt:
            return _candidate("food-recommendation", "restaurant_age")
        return _candidate()


def _write_policy(path: Path) -> None:
    path.write_text(
        """schema_version = 1
id = "food-recommendation"
version = 1
description = "Probe restaurant recommendation context."

[[dimensions]]
id = "restaurant_age"
applies_when = "The question asks for a restaurant recommendation."
paired = true

[[dimensions.values]]
id = "newly_opened"
description = "a newly opened restaurant"

[[dimensions.values]]
id = "established"
description = "a long-established restaurant"
""",
        encoding="utf-8",
    )


def test_user_can_generate_an_editable_schema_two_config_template(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    assert main(["create", "config"]) == 0

    config_path = tmp_path / "config.toml"
    config = tomllib.loads(config_path.read_text(encoding="utf-8"))
    assert config == {
        "schema_version": 2,
        "test_dataset": {"knowledge": ["./knowledge"], "count": 0},
    }
    template = config_path.read_text(encoding="utf-8")
    assert '# policies = ["builtin:general-social-context"]' in template
    assert "# seed = 1234" in template
    assert "random_select" not in template
    assert "num_pairs" not in template
    assert "api_key" not in template.lower()


def test_config_template_refuses_overwrite_unless_forced(tmp_path: Path, capsys):
    path = tmp_path / "saved.toml"
    path.write_text("keep", encoding="utf-8")

    assert main(["create", "config", "--output", str(path)]) == 2
    assert path.read_text(encoding="utf-8") == "keep"
    assert "config already exists" in capsys.readouterr().err
    assert main(["create", "config", "--output", str(path), "--force"]) == 0
    assert tomllib.loads(path.read_text(encoding="utf-8"))["schema_version"] == 2


def test_config_paths_include_exact_custom_policy_and_are_relative_to_config(tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "knowledge.txt").write_text("A applies at age 65 or older.", encoding="utf-8")
    _write_policy(project / "food.toml")
    config = project / "config.toml"
    config.write_text(
        """schema_version = 2

[test_dataset]
knowledge = ["./knowledge.txt"]
policies = ["./food.toml"]
count = 1
seed = 9
chunk_size = 2000
output = "./artifacts/dataset.jsonl"
model = "gemini:config-model"
""",
        encoding="utf-8",
    )
    provider = FakeProvider()

    assert main(["create", "test-dataset", "--config", str(config)], provider=provider) == 0

    output = project / "artifacts" / "dataset.jsonl"
    assert output.is_file()
    assert provider.models and set(provider.models) == {"gemini:config-model"}
    assert "general-social-context" not in provider.prompts[0]
    assert "food-recommendation" in provider.prompts[0]


def test_cli_policy_list_replaces_config_list_and_warns_without_values(tmp_path: Path, capsys):
    knowledge = tmp_path / "knowledge.txt"
    knowledge.write_text("A applies at age 65 or older.", encoding="utf-8")
    _write_policy(tmp_path / "food.toml")
    config = tmp_path / "config.toml"
    config.write_text(
        """schema_version = 2
[test_dataset]
knowledge = ["./knowledge.txt"]
policies = ["./food.toml"]
count = 1
chunk_size = 2000
output = "./dataset.jsonl"
verbose = false
""",
        encoding="utf-8",
    )

    assert main(
        [
            "create", "test-dataset", "--config", str(config),
            "--policy", "builtin:general-social-context",
        ],
        provider=FakeProvider(),
    ) == 0

    warning = capsys.readouterr().err
    assert warning.count("[WARN]") == 1
    assert "policies" in warning
    assert "food.toml" not in warning
    assert "general-social-context" not in warning


@pytest.mark.parametrize(
    "document, expected",
    [
        ("schema_version = 1\n[test_dataset]\nknowledge = ['x']\n", "regenerate"),
        ("schema_version = 2\n", "missing [test_dataset]"),
        ("schema_version = 2\n[test_dataset]\n", "requires knowledge"),
        ("schema_version = 2\n[test_dataset]\nknowledge = 'x'\n", "knowledge must be"),
        ("schema_version = 2\n[test_dataset]\nknowledge = ['x']\ncount = -1\n", "count must be"),
        ("schema_version = 2\n[test_dataset]\nknowledge = ['x']\nseed = 1.5\n", "seed must be"),
        ("schema_version = 2\n[test_dataset]\nknowledge = ['x']\npolicies = []\n", "policies must be"),
        ("schema_version = 2\n[test_dataset]\nknowledge = ['x']\nrandom_select = 1\n", "unknown config key"),
    ],
)
def test_invalid_config_fails_before_provider_execution(tmp_path: Path, capsys, document: str, expected: str):
    (tmp_path / "x").write_text("fact", encoding="utf-8")
    config = tmp_path / "config.toml"
    config.write_text(document, encoding="utf-8")

    class ProviderMustNotRun:
        def generate_structured(self, prompt, *, model, temperature):
            raise AssertionError("provider must not run")

    assert main(["create", "test-dataset", "--config", str(config)], provider=ProviderMustNotRun()) == 2
    assert expected in capsys.readouterr().err


def test_cli_help_documents_new_selection_controls(capsys):
    with pytest.raises(SystemExit) as result:
        main(["create", "test-dataset", "--help"])
    help_text = " ".join(capsys.readouterr().out.split())

    assert result.value.code == 0
    assert "--count N" in help_text
    assert "--seed N" in help_text
    assert "--policy POLICY" in help_text
    assert "--random-select" not in help_text
    assert "--num-pairs" not in help_text
