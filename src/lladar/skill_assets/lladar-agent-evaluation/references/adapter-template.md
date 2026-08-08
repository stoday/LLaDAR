# Adapter template

Use this template when the project agent does not already expose a batch
question-answer function. Replace only `build_agent` with the project's real
agent construction and invocation code.

```python
import json
from pathlib import Path


def build_agent():
    # Return the existing project agent. Keep login and secret entry outside this file.
    raise NotImplementedError


def answer(question: str) -> str:
    agent = build_agent()
    response = agent(question)
    return response if isinstance(response, str) else str(response)


def run(dataset_path: str | Path, output_path: str | Path) -> None:
    agent = build_agent()
    with Path(dataset_path).open(encoding="utf-8") as source, Path(output_path).open(
        "w", encoding="utf-8", newline="\n"
    ) as target:
        for line in source:
            if not line.strip():
                continue
            item = json.loads(line)
            result = {"id": item["id"]}
            try:
                response = agent(item["underspecified_question"])
                result.update(status="ok", answer=response if isinstance(response, str) else str(response))
            except Exception as error:
                result.update(status="error", error=f"{type(error).__name__}: {error}")
            target.write(json.dumps(result, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    run("test-dataset.jsonl", "qa-results.jsonl")
```

Prefer the project's existing agent lifecycle when it has one. Create one
isolated conversation per dataset item unless the agent contract explicitly
requires a multi-turn session.
