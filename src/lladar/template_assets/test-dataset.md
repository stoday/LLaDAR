# Editing this test-dataset Skill

`SKILL.md` contains the active method. Run it with
`lladar create test-dataset --knowledge KNOWLEDGE --skill DIRECTORY --output DATASET.jsonl`.
The host sends a request with a `stage` for each work item. Tools expire after
that work item; use only those available in the current stage.

## `knowledge_points` stage

The request names one `source_id` and reports unread source ranges. Available
tools:

- `list_sources()` returns each source ID, name, and text length.
- `read_source(source_id, start_char=0, end_char=None)` returns source text,
  `read_id`, character range, and `next_start`. Follow `next_start` to cover
  the source.
- `submit_knowledge_points(points)` takes a list of objects with `statement`,
  `topic`, and `evidence: [{read_id, quote}]`. Each quote must appear in a read
  page. It returns accepted IDs and rejected indexes with reasons. Correct
  rejected points before finishing.

Completion: all source ranges were read and no rejection remains unresolved.

## `semantic_graph` stage

- `list_knowledge_points()` returns accepted fact IDs and statements.
- `submit_semantic_graph(graph)` accepts one object with `nodes`, `edges`, and
  `facts`. Every source item must cite accepted fact evidence. It returns the
  accepted graph summary or a validation error.

Completion: one evidence-backed graph was accepted.

## `test_plans` stage

- `read_semantic_graph()` returns the verified graph for this work item.
- `submit_test_plans(plans)` accepts a nonempty list of `direct_fact`,
  `concept_mapping`, or `controlled_invariance` plans. A direct fact plan has
  `type`, `entity_id`, `question`, and `expected_answer`; the answer must match
  the graph fact. The tool returns accepted and pair counts or a validation
  error. The selected Skill describes the additional concept and pair rules.

Completion: the host accepts the plans. The host assigns IDs, validates
evidence and paired variations, and writes the dataset and provenance.

## `qa` stage

When the host requests question candidates, use `read_knowledge_point(id)` for
the assigned point, `list_knowledge_points()` for comparison context, and
`submit_qa(record)` with `knowledge_point_id`, `question`, and
`expected_answer`. Typed answer protocols require additional fields described
by the selected question type. The host validates each candidate and returns
acceptance or an error.

The Skill controls how source facts become questions. LLaDAR controls source
evidence, graph validation, question contracts, deduplication, record writing,
and the requested count. Test changes on a small knowledge file first.
