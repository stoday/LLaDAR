# Editing this test-dataset Skill

## Start here

This directory has two important files:

- `SKILL.md` is the active recipe used by the generation agent. Change this
  file when you want to change what questions are generated.
- `AUTHORING.md` is this guide. It explains what is safe to change and keeps
  the tool contracts and advanced examples in one place.

The usual edit loop is:

1. Edit the plain-language rules in `SKILL.md`.
2. Run `lladar create test-dataset` with `--skill` pointing to this directory.
3. Inspect the generated JSONL and its `.generation.json` sidecar.
4. Adjust the rules and run again with a new output path (or `--force` when
   replacing an output is intentional).

You normally only need to edit the **Question policy** section in `SKILL.md`.
Do not change tool names, field names, or JSON contracts unless you are
intentionally maintaining a compatible custom method.

## What you can change

Replace the policy bullets in `SKILL.md` with rules that match your domain.
These are useful, safe examples:

```markdown
## Question policy

- Generate one standalone question for each meaningful passage.
- Ask only for facts stated in the source; do not require outside knowledge.
- Keep quantities, units, conditions, exceptions, and negation in the answer.
- Prefer short natural questions. Do not repeat the same question.
- Every expected answer must be supported by an exact source quote.
```

Common changes include:

| Goal | Rule to add or change in `SKILL.md` |
| --- | --- |
| Fewer questions | “Generate at most one question per passage.” |
| More coverage | “Generate up to three non-duplicate questions when a passage contains separate facts.” |
| Fact-only QA | “Do not ask comparison, opinion, or outside-knowledge questions.” |
| Allow safe inference | “Allow only the stated calculation; show the source facts used.” |
| Domain vocabulary | “Use the source’s product names and terminology exactly.” |
| Choice questions | State the required choice type and keep every option source-supported. |

If you only need ordinary source-grounded QA, use direct questions. You do not
need to add a graph or Python code. Use graph questions only when the source
contains comparable entities, relationships, or a controlled comparison that
you actually want to test.

## Active method and completion

`SKILL.md` defines the active method. This guide supplies contracts and
replacement examples; reading it does not override the active Skill. Replace
the relevant method in `SKILL.md` when choosing an alternative, and keep both
files consistent.

The request stage is `generation`. The host does not require separate
knowledge-points, graph, or plan stages. Choose direct QA, a graph, or a
mixture according to the active Skill. A requested controlled-variant workflow
still requires a valid graph and complete pairs.

Completion means valid final questions, source evidence, and any graph/probe
contracts required by the method and request. The host freezes and revalidates
results before publishing. Text alone is not delivery.

## Minimal direct-QA example

Assume the source is `Service A keeps data for 30 days.` and its read ID is
`read_000001`. Retain the passage with:

```json
[
  {"statement": "Service A keeps data for 30 days.", "topic": "Retention",
   "evidence": [{"read_id": "read_000001", "quote": "Service A keeps data for 30 days."}]}
]
```

For free questions, the preferred atomic submission is
`submit_question(request_id, question, expected_answer, source_refs,
answer_basis, rationale)`. Use `submit_qa` for the compatible route and for
typed questions. Do not add `id`, `actual_response`, or `question_type` to a
free QA record.

## Reading and evidence contract

Use `list_sources` and read every declared source with `read_source`. Character
ranges start at zero and exclude the end; follow `next_start` until it is null.
Keep subjects, quantities, units, conditions, exceptions, and negation
together. Evidence must contain an actual `read_id` and a verbatim quote.

The host validates source positions, structure, IDs, deduplication, count
limits, and final serialization. It cannot prove that a natural-language
answer is sensible or that every source fact was covered, so keep the policy
explicit and review the output.

## Optional graph questions

Use a graph only when it makes a useful comparison or concept probe possible.
Submit non-empty `nodes` and `facts` with source evidence, read the graph, and
then submit plans. A direct fact answer equals the fact value and unit. A
concept-mapping answer contains the complete verified candidate set. Do not
submit an empty graph or invent a concept just to use this path.

```json
{
  "nodes": [
    {"id": "service-a", "type": "entity", "label": "Service A",
     "origin": "source", "evidence_refs": ["kp_000001"]}
  ],
  "edges": [],
  "facts": [
    {"entity_id": "service-a", "label": "Service A", "value": "30",
     "unit": "days", "evidence_ref": "kp_000001"}
  ]
}
```

```json
[
  {"type": "direct_fact", "entity_id": "service-a",
   "question": "How long does Service A keep data?", "expected_answer": "30 days"}
]
```

For a compatible direct-QA submission, use:

```json
{
  "knowledge_point_id": "kp_000001",
  "question": "How long does Service A keep data?",
  "expected_answer": "30 days"
}
```

Controlled-variant pairs may change exactly one declared `control_value` while
preserving the task, concept, candidates, answer contract, and question
skeleton. Do not use an unknown or unsupported value relationship.

## Typed questions

For an explicit single-choice, multiple-choice, or ranking request, use the
typed `submit_qa` contract. Every option must map to a known source point;
never invent distractors.

| Type | Options | Correct IDs | Protocol |
| --- | --- | --- | --- |
| `single_choice` | 2–5 | One | `one_option_id` |
| `multiple_choice` | 3–6 | Multiple, not all | `option_id_list` |
| `ranking` | 3–5 | Every option once | `ordered_option_ids` |

## Recovery and advanced delivery

On retries, inspect `validation_errors` and `existing_results`. Repair an
existing result instead of resubmitting completed graph or plan work. After an
uncertain submission, query the original request ID and reuse it only for
identical content.

Python may explore, calculate, and repair candidates. If the request declares a
`candidate_path` and `candidate_contract`, that JSON file is the complete,
authoritative snapshot, not an incremental addition. Follow the actual
contract in the request; do not make the host discover files by scanning a
directory.

With `--from-github`, the selected Skill must also handle `github_discovery`
and `github_review`; see the repository GitHub-generation documentation for
that advanced workflow.
