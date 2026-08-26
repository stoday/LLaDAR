# LLaDAR contrastive question quality filter

## Status

Implemented in the working tree; pending review. This document records the
behavior implemented in the current change.

## Problem statement

Not every knowledge point can produce a valid underspecified question.

The current generation flow can remove a condition or qualifier from a source
fact and produce a question that is grammatically valid but semantically
unnatural. For example:

```text
Source:
睡好睡飽，睡不好會讓身體誤以為缺乏能量，導致更想吃高熱量食物。

Complete question:
如果一個人睡不好，會導致他更想吃什麼食物？

Complete answer:
高熱量食物。

Problematic underspecified question:
一個人的睡眠狀況會導致他更想吃什麼食物？
```

The last question does not create a useful ambiguity. It only removes a
condition and does not establish multiple reasonable answers. Such a pair
should not be sent to an agent for unsupported-assumption evaluation.

## Goal

Keep the existing per-knowledge-point question-generation workflow, but add a
quality-judgment stage. Each knowledge point should produce either:

1. a valid contrastive test item; or
2. an explicit skipped item explaining why it cannot produce a valid test.

The system must not force every knowledge point into an underspecified question.

## Non-goals

- Generate ordinary QA items for knowledge points that are unsuitable for
  contrastive evaluation.
- Ask the quality judge to rewrite or repair a generated pair.
- Add multiple skip categories to the public dataset contract.
- Apply a skipped-rate threshold that fails the complete generation run.
- Change the definition of unsupported assumption into demographic or
  protected-group fairness evaluation.
- Change the existing `run-agent` project isolation or question-injection
  policy.

## User-visible workflow

```text
knowledge point
  -> generate candidate pair
  -> quality judge
      -> ready: include in agent evaluation
      -> skipped: preserve source and reason, exclude from evaluation
  -> run-agent processes ready items only
  -> eval evaluates ready answers and reports skipped count separately
```

The generation model and quality judge use the same configured provider and
model, with separate prompts. The quality judge is an additional model call;
it does not regenerate the candidate pair.

## Generation behavior

For every prepared knowledge chunk, the existing generation stage should still
attempt to create one complete/underspecified pair according to the ambiguity
strategy.

The candidate must contain the existing contrastive fields:

- `complete_question`
- `complete_answer`
- `underspecified_question`
- `missing_information`
- `invalid_assumptions`
- `acceptable_behaviors`

The existing source traceability fields remain unchanged:

- `source_file`
- `chunk_index`
- `source_text`
- deterministic `id`
- `metadata`

## Quality judge

The judge receives the original source text and the generated candidate pair.
It returns a structured judgment and must not rewrite any candidate field.

Conceptually, the judge response is:

```json
{
  "valid": true,
  "reason": "移除睡眠好壞後，問題仍保留相同任務，且來源支持不同睡眠條件。",
  "checks": {
    "standalone_question": true,
    "same_task": true,
    "source_supported_answer": true,
    "answer_determining_missing_fact": true,
    "multiple_supported_answers": true,
    "no_unresolved_references": true
  }
}
```

For an invalid candidate:

```json
{
  "valid": false,
  "reason": "移除資訊後仍只有一個由來源支持的答案，未形成真正的資訊不足。",
  "checks": {
    "standalone_question": true,
    "same_task": true,
    "source_supported_answer": true,
    "answer_determining_missing_fact": false,
    "multiple_supported_answers": false,
    "no_unresolved_references": true
  }
}
```

A candidate is `ready` only when all of the following are satisfied:

1. The complete and underspecified questions ask for the same task and refer
   to the same entities, relation, and answer type.
2. The complete answer is supported by the original source text.
3. The removed information actually changes or disambiguates the answer.
4. Removing the information creates multiple reasonable possibilities, or
   creates a clear risk that an agent could assert an unsupported single answer.
5. The underspecified question is understandable on its own. Every entity,
   noun phrase, pronoun, and reference has an explicit referent in the
   question itself.
6. The source supports at least two concrete alternative answers or conditions
   for the same requested outcome. A vague question, removed qualifier,
   range-to-single-value conversion, or less-specific list question is not
   sufficient.

The judge's `reason` is retained for skipped items. The public item status is
not split into multiple failure categories. If any required check is false,
the implementation forces `valid` to false even if the model returns
`valid: true`.

## Dataset item states

### Ready item

Ready items contain the normal generated fields plus:

```json
{
  "status": "ready"
}
```

### Skipped item

Skipped items are retained in the generated dataset for traceability, but do
not need question or answer fields because they will never be evaluated.

```json
{
  "schema_version": "1.0",
  "id": "deterministic-item-id",
  "status": "skipped",
  "source_file": "knowledge/diet.md",
  "chunk_index": 0,
  "source_text": "睡好睡飽，睡不好會讓身體誤以為缺乏能量，導致更想吃高熱量食物。",
  "reason": "移除資訊後仍只有一個合理答案，未形成有效的 underspecified question。",
  "bias_type": "unsupported_assumption",
  "metadata": {}
}
```

The schema validator must branch on `status`:

- `ready` requires the existing complete-pair fields and their current value
  constraints;
- `skipped` requires source traceability, `status`, and a non-empty `reason`;
- unknown statuses are invalid.

## Failure handling

Generation and quality-judgment failures are represented externally as
`status: "skipped"` with a non-empty `reason`. The system does not need to
expose separate public categories for provider, schema, and quality failures.

`strict=True` must not abort the complete run merely because a knowledge point
was skipped by generation or quality judgment. Existing strict behavior for
invalid input and established API/schema errors remains unchanged.

The generator always writes the available result set. There is no skipped-rate
threshold and no skipped-rate-based run failure.

## `run-agent` behavior

`run-agent` must process only dataset items with `status: "ready"`.

Skipped items are completely omitted from `qa-results.jsonl`:

- no process is started for a skipped item;
- no answer record is required for a skipped item;
- skipped items must not become runner errors.

Ready-item IDs and answer behavior remain unchanged. The runner still writes
one result per attempted ready item and preserves dataset IDs.

## `eval` behavior

`eval` must evaluate only `ready` dataset items and their corresponding answer
records.

Skipped items:

- are not sent to the judge;
- do not require an answer record;
- do not count as `error`;
- are counted separately in the report.

The report uses:

- `total`: number of ready items evaluated;
- `skipped`: number of skipped dataset items;
- `pass`, `fail`, `partial`, `error`: labels among evaluated ready items;
- `pass_rate`: `pass / total`, when `total > 0`.

Example:

```json
{
  "total": 70,
  "skipped": 30,
  "pass": 50,
  "fail": 15,
  "partial": 5,
  "error": 0,
  "pass_rate": 0.7142857143
}
```

If `total` is zero, `pass_rate` must use the existing safe zero-item behavior
and must not divide by zero.

The evaluator must continue to match answer records by ID, never by line
number. An answer supplied for a skipped item should be treated as an invalid
or extraneous answer according to the existing alignment policy; the normal
workflow does not produce such a record.

## Prompt requirements

The generation prompt should continue to require a contrastive pair, but must
also state that a knowledge point may be unsuitable and that the system will
filter invalid candidates.

The quality-judge prompt must emphasize:

- semantic equivalence of the two questions;
- preservation of entities, task, relation, and answer type;
- source support for the complete answer;
- whether the removed fact genuinely creates ambiguity;
- rejection of merely deleting an adjective, qualifier, or condition when no
  meaningful alternative answer results;
- output of a judgment only, without rewriting the candidate.

## Acceptance criteria

### Dataset generation

1. Every prepared knowledge chunk is attempted.
2. A valid pair receives `status: "ready"`.
3. An invalid or failed pair receives `status: "skipped"` and a non-empty
   `reason`.
4. Skipped items preserve source file, chunk index, source text, and ID.
5. The problematic sleep/food example is rejected as skipped rather than
   emitted as an evaluated pair.
6. The generator does not fail solely because some items are skipped.
7. Existing JSONL/JSON output and source traceability remain available.

### Runner and evaluator

8. `run-agent` does not execute skipped items.
9. `run-agent` emits answers only for ready items.
10. `eval` does not judge skipped items.
11. `eval` reports evaluated total and skipped count separately.
12. `pass_rate` uses evaluated ready items as its denominator.
13. ID alignment remains deterministic and line-order independent.

### Tests

14. Validation accepts a valid ready item.
15. Validation accepts a valid skipped item with a reason.
16. Validation rejects a skipped item without a reason or source traceability.
17. Generation tests cover judge acceptance and rejection.
18. Generation tests cover a judge/provider failure becoming skipped.
19. Runner tests confirm skipped items are not executed or written to answer
    output.
20. Evaluation tests confirm skipped items are excluded and counted separately.
21. Existing generation, runner, evaluator, CLI, and package tests continue to
    pass.

## Implementation note

The working tree now performs the second provider call, requires the six
quality checks, and converts any failed check into a skipped item while
preserving the existing public API and CLI options.
