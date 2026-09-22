# LLaDAR domain language

LLaDAR is a four-stage Agent-evaluation pipeline.

**Question record**

One JSONL object containing exactly `question`, `expected_answer`, and
`actual_response`.

**Expected answer**

The source-grounded answer generated with the question. It is evaluation
evidence, not a score.

**Actual response**

The target Agent's final textual response. Execution failure is represented by
`null` and detailed in the run sidecar.

**Evaluation plan**

A frozen set of boolean, categorical, or numeric dimensions proposed by the
evaluator Agent or shaped by the user's evaluation prompt.

**Judgment**

The evaluator Agent's structured values and rationale for one completed record.

**Aggregate**

Counts, rates, distributions, or descriptive statistics calculated by Python
from judgments. The evaluator Agent does not calculate aggregate arithmetic.

**Report**

An evidence-bounded presentation of one saved evaluation. Tables are rendered
deterministically; Agent-written prose may interpret but not replace them.
