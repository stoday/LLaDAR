# Custom batch-adapter template

Use this only when `lladar run-agent` cannot execute the project's established
Agent seam. Replace `build_agent` and `answer` with the project's real lifecycle;
keep dataset expansion and output metadata unchanged.

```python
import json
from pathlib import Path


def build_agent():
    raise NotImplementedError


def answer(agent, question: str) -> str:
    response = agent(question)
    return response if isinstance(response, str) else str(response)


def cases(group: dict):
    if group["status"] == "skipped":
        return
    yield group["id"], group["id"], "original", group["original"]["question"]
    for variant in group["variants"]:
        yield variant["id"], group["id"], variant["kind"], variant["question"]


def run(dataset_path: str | Path, output_path: str | Path) -> None:
    agent = build_agent()
    with Path(dataset_path).open(encoding="utf-8") as source, Path(output_path).open(
        "x", encoding="utf-8", newline="\n"
    ) as target:
        for line in source:
            if not line.strip():
                continue
            group = json.loads(line)
            if group.get("schema_version") != 2:
                raise ValueError("regenerate the dataset with schema version 2")
            for case_id, group_id, kind, question in cases(group):
                result = {
                    "schema_version": 2,
                    "id": case_id,
                    "group_id": group_id,
                    "kind": kind,
                    "question": question,
                }
                try:
                    result.update(status="ok", answer=answer(agent, question))
                except Exception as error:
                    result.update(
                        status="execution_error",
                        error=f"{type(error).__name__}: {error}",
                    )
                target.write(json.dumps(result, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    run("test-dataset.jsonl", "qa-results.jsonl")
```

Prefer one isolated Agent conversation per case. If the Agent object carries
conversation state, construct it inside the case loop instead of once above.
