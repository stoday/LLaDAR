# Editing this test-dataset Skill

## Purpose and active instructions

SKILL.md defines the active method. This guide supplies contracts and replacement
examples; reading it does not override the active Skill. Replace the relevant
method in SKILL.md when choosing an alternative, and keep both files consistent.

The request's stage is `generation`. All generation tools are available together.
The host does not require separate knowledge_points, semantic_graph, or test_plans
stages. Choose direct QA, a graph, or a mixture according to the active Skill.
A requested controlled-variant workflow still requires a valid graph and pairs.

## Tools and completion

| Capability | Tools |
| --- | --- |
| Read declared sources | list_sources, read_source |
| Retain meaningful passages | submit_knowledge_points, list_knowledge_points, read_knowledge_point |
| Generate direct or typed questions | submit_qa |
| Optional graph and probes | submit_semantic_graph, read_semantic_graph, submit_test_plans |
| Record why the method was selected | record_method |

Read all declared sources. Character ranges start at zero and exclude the end.
Follow read_source.next_start until null. Point evidence uses actual read IDs
and verbatim quotes. A meaningful passage may contain several related facts;
keep its necessary conditions and exceptions together.

Completion means valid final questions, source evidence, and any graph/probe
contracts required by the method and request. The host freezes and revalidates
results before publishing. More than one QA may refer to the same point.

## Minimal point, optional graph, graph plan, and direct QA

Assume the source is `Service A keeps data for 30 days.` and its read ID is
`read_000001`. Submit this list through submit_knowledge_points(points):

```json
[
  {"statement": "Service A keeps data for 30 days.", "topic": "Retention",
   "evidence": [{"read_id": "read_000001", "quote": "Service A keeps data for 30 days."}]}
]
```

Use the returned point IDs. For the optional graph method, assuming kp_000001
was accepted, submit_semantic_graph(graph) takes:

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

Nodes and facts must be non-empty. Node types are entity, attribute, concept.
Concepts require at least two distinct entity member_ids. Origins are source
or inferred, with supporting point IDs. Facts refer to entity nodes, matching
labels and their evidence references. Each entity has one comparable fact.
Edges have from, relation, to, origin, evidence_refs; both endpoints must exist.
An empty edge list is valid. An empty graph is not a successful graph method.

After reading the graph, submit_test_plans(plans) accepts:

```json
[
  {"type": "direct_fact", "entity_id": "service-a",
   "question": "How long does Service A keep data?", "expected_answer": "30 days"}
]
```

The answer equals the graph fact's value and unit. A concept_mapping plan has
type, concept_id, question, expected_answer; its answer is the complete verified
candidate set, such as `Starter: 10 seats; Growth: 25 seats`.

For direct QA, skip graph and plans entirely and call submit_qa(record):

```json
{
  "knowledge_point_id": "kp_000001",
  "question": "How long does Service A keep data?",
  "expected_answer": "30 days"
}
```

For a direct-reading Skill, replace the graph-method instructions with:
Read sources in order, identify meaningful passages, and generate natural
standalone questions and source-supported answers. Retain exact source evidence.
Use direct QA; a graph and Python are not required for this method.

## Typed QA

For an explicit single-choice, multiple-choice, or ranking request, free graph
fact plans do not satisfy the requested type. Use submit_qa with these fields:
knowledge_point_id, knowledge_point_ids, question, expected_answer, question_type,
answer_protocol, options, correct_option_ids. Each option has id (A, B, ...),
text (the referenced point's exact statement), and knowledge_point_id. Referenced
points share a topic; every option maps once to a known point and is shown in
the question as `A. statement`.

| Type | Options | Correct IDs | Protocol | Expected answer |
| --- | --- | --- | --- | --- |
| single_choice | 2–5 | One | one_option_id | B |
| multiple_choice | 3–6 | Multiple, not all | option_id_list | A,C |
| ranking | 3–5 | Every option once | ordered_option_ids | B>A>C |

Ranking also requires ranking_axis and direction (ascending or descending).
Use source-supported relations and complete candidates, not invented distractors.

## Controlled comparisons

A controlled_invariance plan has type, pair_id, source_concept,
source_support (group_unspecified), answer_contract (invariant), varied_dimension,
control_value, question_template, question, expected_answer. varied_dimension
has id, label, semantic_scope, mutual_exclusivity, coexists_with (ID list).
Unknown value relationships do not qualify.

A pair has at least two distinct control values and preserves one concept,
dimension, source candidate set, and answer contract. Each question contains its
control value; question_template equals question. Replacing the control values
with one placeholder yields the same question skeleton. The host selects the
requested dimensions and keeps pairs whole when deduplicating or applying count.

## Python and candidate-file delivery

Python may explore, calculate, read files, and repair the active workspace.
Results need not all pass through submission tools. Every delivery path faces
final source, format, graph, and probe validation.

The request declares candidate_path and candidate_contract. A present JSON file
is the complete authoritative candidate snapshot, not an addition to workspace
results. Use only that declared delivery path; the host does not discover files
by scanning directories. Include these fields:

- reads: objects with read_id, source_id, start_char, end_char. The host reconstructs
  text from its loaded source. Include complete source reading ranges.
- knowledge_points: objects with id, statement, topic, evidence containing read_id
  and quote. IDs may be author-chosen, unique, and consistent with references.
- qa: the same raw direct/typed QA records described above.
- graph: optional raw nodes, edges, facts. Omit it for a direct method.
- plans: optional raw graph plans. These require a valid graph.
- method_reason: a brief explanation of the selected method.

Read candidate_contract from the actual request for the active delivery format.
You may also adjust workspace candidates directly; the host rechecks their final
snapshot rather than trusting an earlier accepted tool result. It validates
structure and source positions; this alone does not prove natural-language
entailment or complete fact coverage.

## Recovery

The request's validation_errors identifies final-result problems. existing_results
reports accepted question/plan counts, graph presence, and source coverage.
Repair incomplete work. If the Agent reaches its round limit after valid delivery,
the host can preserve the result while recording that execution error.
A completion summary alone is not candidate data. Normal CLI output protection,
force, log, count, and question-type contracts remain in effect.
