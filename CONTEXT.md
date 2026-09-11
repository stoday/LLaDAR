# LLaDAR Test-Dataset Generation

LLaDAR creates controlled question groups from source knowledge, runs an Agent
against every original and variant, and measures how substantive answers change
when information or unrelated cues change.

## Language

**Question group**:
One source-grounded original case and all controlled variants derived from it.
One ready group tests exactly one key-information dimension.
_Avoid_: Pair, test type

**Original case**:
A standalone question plus a non-empty reference answer supported entirely by
the captured source text.
_Avoid_: Control outcome, expected variant answer

**Key information**:
The single concrete fact or condition present in the original question and
supported by the source. It is removed to create the omission variant.
_Avoid_: Missing answer, decision outcome

**Variant**:
A standalone question produced through one controlled transformation. Its
answer is always unset (`null`) in the generated dataset.
_Avoid_: Expected answer, correct answer

**Information-omission variant**:
The original task with exactly one key-information dimension removed.

**Peer-cue variant**:
The same task with a policy-defined contextual cue added in place of the key
information. The cue must not determine the source-grounded answer.

**Generation policy**:
Versioned, non-executable data describing candidate cue dimensions, values,
applicability guidance, matched-set behavior, and optional tags. A policy does
not define preferred answers, scores, or verdicts.

**Ready record**:
A complete schema-v2 question group that passed every structural and semantic
quality check.

**Skipped record**:
A minimal, traceable source candidate that could not produce a valid group or
duplicated an earlier ready group. It is evidence about dataset coverage, not
an Agent evaluation result.

**Observed answer**:
The Agent's response to one original or variant session. It lives in a separate
schema-v2 answer artifact and never mutates the generated dataset.

**Difference finding**:
An evaluator result for one original-to-variant comparison. It records session
eligibility, a protocol label, score, rationale, and transformation metadata.
