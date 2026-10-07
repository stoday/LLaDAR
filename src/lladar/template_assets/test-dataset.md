# Editing this test-dataset Skill

## Author guide: purpose, customization, and tool selection

This guide is for Skill authors. The active rules are in `SKILL.md`.
The guide is not automatically registered as another Skill, but an Agent can
read it as a reference resource. Alternative examples do not replace the active
method. When adopting an alternative, replace the original rule in `SKILL.md`
and keep one consistent method. Editing only this guide does not change the rules.

Edit the relevant stage's steps in `SKILL.md`: use a customer support tone,
emphasize conditions and exceptions, or choose source-supported test types.
The host still owns citations, validation, IDs, and file writing.

### Which tools to use in each situation

| Stage | Use case | Complete LLaDAR tool list |
| --- | --- | --- |
| `knowledge_points` | Find independently answerable source facts | `list_sources`, `read_source`, `submit_knowledge_points` |
| `semantic_graph` | Organize facts into entities, attributes, concepts | `list_knowledge_points`, `submit_semantic_graph` |
| `test_plans` | Generate direct questions, concept questions, controlled comparisons | `read_semantic_graph`, `submit_test_plans` |
| `qa` | Generate a candidate question for an assigned point | `read_knowledge_point`, `list_knowledge_points`, `submit_qa` |

The host chooses `stage`; a Skill cannot switch stages or access another stage's
tools. `source_id` identifies a source, `read_id` a read operation, and
`knowledge_point_id` an accepted fact. Use actual IDs from tools or the request,
not IDs copied from examples.

### Minimal submissions: knowledge point, graph, direct-fact question

Each block is a JSON tool argument for its own stage. Assume the source says
`Service A keeps data for 30 days.`. After reading the assigned source, use the
returned `read_id` in `submit_knowledge_points(points)`:

```json
[
  {"statement": "Service A keeps data for 30 days.", "topic": "Retention",
   "evidence": [{"read_id": "read_000001", "quote": "Service A keeps data for 30 days."}]}
]
```

The tool returns `accepted` (ID list) and `rejected` (list with index and error).
Correct rejections; finish after reading all ranges and resolving every rejection.
`read_source` positions are zero-based character offsets, not line numbers.
Long sources are paginated; follow `next_start` until null.

In the graph stage, `list_knowledge_points()` returns objects with id, statement,
topic, evidence. Assuming `kp_000001` was accepted, call `submit_semantic_graph(graph)`:

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

Define node IDs; evidence IDs must identify accepted points. Node types are
`entity`, `attribute`, `concept`; concepts need `member_ids` for at least two
distinct entities. Origin is `source` or `inferred`; inferred concepts still need
source evidence. Each entity has at most one comparable fact; `unit` may be empty.
A complete relation edge is
`{"from":"node-a","relation":"belongs-to","to":"node-b","origin":"source","evidence_refs":["kp_000001"]}`.
Both endpoints must be submitted node IDs, with evidence supporting the relation.
Acceptance returns `accepted: true` and node and fact counts.

In the plan stage, call `read_semantic_graph()` for the verified graph with nodes,
edges, facts, evidence, then `submit_test_plans(plans)`:

```json
[
  {"type": "direct_fact", "entity_id": "service-a",
   "question": "How long does Service A keep data?", "expected_answer": "30 days"}
]
```

`expected_answer` equals the fact's value combined with its unit. The host generates
the dataset after acceptance. A concept question has exactly `type: concept_mapping`,
`concept_id`, `question`, `expected_answer`. Its answer is the complete
source-supported candidate set for all concept members, not one member.

### When to use controlled comparisons

Use `controlled_invariance` only when the source leaves the relevant condition
unspecified, to compare responses after changing one condition. A group needs at
least two plans. Complete fields: `type`, `pair_id`, `source_concept`,
`source_support: group_unspecified`, `answer_contract: invariant`,
`varied_dimension`, `control_value`, `question_template`, `question`, `expected_answer`.
`varied_dimension` is an object: `id`, `label`, `semantic_scope`,
`mutual_exclusivity` are nonempty strings; `coexists_with` is a list of dimension
ID strings. `mutual_exclusivity` cannot be `unknown`. Each question contains its
control_value; question_template equals question. Replacing control_value with
the same placeholder must yield the same question skeleton. Within a group,
source_concept, dimension, and complete candidate answer match; control_value differs.
This is an advanced method; start with direct_fact for ordinary questions.

### Minimal `qa` tool argument

`read_knowledge_point(knowledge_point_id)` returns the assigned point object.
Use `list_knowledge_points()` to compare facts. A free-response `record` argument
to `submit_qa(record)` is:

```json
{"knowledge_point_id": "kp_000001", "question": "How long does Service A keep data?", "expected_answer": "30 days"}
```

The ID identifies the assigned point. Typed answers have additional question-type
contracts; the free-response example does not cover them.

## Stage execution contracts

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
