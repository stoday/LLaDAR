# LLaDAR skill installer

## Purpose

LLaDAR 0.2.0 packages the `lladar-agent-evaluation` skill so it can be
installed into the current project for the agent platform being used. The
skill connects a project's own agent to the fixed LLaDAR workflow:

```text
knowledge files -> test dataset -> project agent adapter -> answer JSONL -> evaluation report
```

The evaluated agent may use any framework or model. LLaDAR's Akasha provider is
used only by LLaDAR's own generation and judge stages.

## Project-local targets

| Target | Destination |
| --- | --- |
| `codex` | `.codex/skills/lladar-agent-evaluation/` |
| `claude` | `.claude/skills/lladar-agent-evaluation/` |
| `antigravity` | `.agents/skills/lladar-agent-evaluation/` |

The installer does not modify global agent configuration. Use `--target all` to
install all three project-local variants.

## Commands

```bash
pip install lladar

lladar skill install --target codex
lladar skill install --target claude
lladar skill install --target antigravity
lladar skill install --target all

lladar skill list
lladar skill update --target claude
lladar skill uninstall --target claude
```

`--force` is required when replacing or removing files that differ from the
managed installation. The installer never silently overwrites a modified
skill. It records a `.lladar-skill-install.json` manifest with the package
version and SHA-256 hashes of managed files.

## Skill contents

The wheel contains one canonical skill resource with:

- `SKILL.md`: inspect, generate, adapt, run, evaluate, and improve workflow
- `references/answer-schema.md`: required id-keyed answer JSONL contract
- `references/adapter-template.md`: framework-neutral adapter template
- `scripts/validate_qa_answers.py`: deterministic dataset/answer preflight
- `agents/openai.yaml`: UI metadata for Codex skill discovery

The installer copies these resources into the selected platform directory. It
does not execute bundled scripts during installation.

## Evaluation workflow

After installation, the project agent skill guides this sequence:

```bash
lladar create test-dataset --knowledge ./knowledge --output test-dataset.jsonl
python path/to/project-agent-adapter.py
python .codex/skills/lladar-agent-evaluation/scripts/validate_qa_answers.py \
  test-dataset.jsonl qa-results.jsonl
lladar eval test-dataset.jsonl qa-results.jsonl \
  --prompt "Do not invent missing facts." \
  --output reports/evaluation.json
```

The adapter must preserve dataset IDs and produce actual agent answers. The
evaluator joins records by ID, never by line number.

## Release checklist

1. Update the package version in `pyproject.toml`.
2. Run the full test suite.
3. Build and inspect the wheel to ensure `skill_assets` is included.
4. Commit and push `main`.
5. Create a matching GitHub Release and tag, such as `v0.2.0`.
6. Confirm the PyPI Trusted Publishing workflow succeeds.
