# LLaDAR domain language

LLaDAR is a four-stage Agent-evaluation pipeline.

## Language

**Question record**

One JSONL object containing exactly `question`, `expected_answer`, and
`actual_response`.

**Expected answer**

The source-grounded answer generated with the question. It is evaluation
evidence, not a score.

**Actual response**

The target Agent's final textual response. Execution failure is represented by
`null` and detailed in the run sidecar.

**Captured response**:

The ordered response material observed for one identified target request,
including its observed completion state. It may contain progress or other
material that is not the target's final answer.

**Answer extraction**:

Selection and assembly of the target's existing final answer from its captured
response, without correcting, summarizing, or judging that answer.
_Avoid_: Answer generation, answer evaluation

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

## Proposed situation language

**Situation config**

A reusable, frozen specification of one behavior to observe, how to vary its
test situations, when to stop, and how to judge the resulting dialogue.

**Scenario instance**

One concrete starting situation generated under a situation config, including
the declared variation choices.

**Trial**

One execution of a scenario instance against a target.

**Transcript**

The ordered messages and target observations from one trial, including a
partial trial that stopped with an error.
