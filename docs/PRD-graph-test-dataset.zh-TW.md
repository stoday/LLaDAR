# LLaDAR graph-based test-dataset generation

## Status

Proposed product requirement. This document defines the opt-in `graph` generation strategy for `lladar create test-dataset`. It does not change the existing `simple` strategy or the current three-field dataset contract.

## Summary

LLaDAR currently generates one question and expected answer per knowledge chunk. The new `graph` strategy introduces a source-grounded semantic intermediate pipeline:

```text
knowledge files
  → corpus ontology
  → atomic knowledge graph
  → inferred concepts
  → deterministic question plans
  → LLM wording
  → validated rich artifacts
  → flattened three-field JSONL dataset
```

The graph is an intermediate model for generation and provenance. Existing `run-agent`, `eval`, and `report` continue to consume the current JSONL records.

## Problem

The current chunk-based generator cannot reliably preserve cross-document entities, enumerate all structured facts, expose a stable question-coverage denominator, or explain which source facts support an answer. Open-choice probes also do not fit the current `expected_answer` contract.

## Goals

- Add an opt-in `graph` strategy to `lladar create test-dataset`.
- Infer a reusable ontology from the complete knowledge corpus.
- Extract atomic, normalized, source-grounded graph facts with selector-to-answer structure.
- Infer conservative, non-exclusive parent concepts with evidence and qualifiers.
- Enumerate question plans deterministically before an LLM writes wording.
- Generate complete-fact and parent-substitution QA with graph provenance.
- Report graph-to-question coverage using a stable denominator.
- Flatten valid QA into the existing three-field JSONL contract.
- Preserve ontology, graph, plans, provenance, and coverage as sidecars.

## Non-goals

- Making `graph` the default in the first release.
- Changing the three-field JSONL contract.
- Modifying `run-agent`, `eval`, or `report` to understand graph metadata in the first release.
- Automatically scoring probes.
- Building a public graph database service.
- Requiring HTML graph visualization in the first release.

## CLI

Existing behavior remains the default:

```bash
lladar create test-dataset \
  --knowledge ./knowledge \
  --output ./datasets
```

The new strategy is explicit:

```bash
lladar create test-dataset \
  --knowledge ./knowledge \
  --strategy graph \
  --output ./datasets
```

Recommended graph options:

```text
--strategy simple|graph              default: simple
--qa-mode free|systematic|compare   graph only; default: systematic
--ontology auto|PATH                default: auto
--refresh-ontology
--refresh-concepts
--refresh-qa
--refresh-probes
--probes
```

`simple` keeps current chunking, retry, deduplication, and output behavior. `graph` loads the complete corpus before question generation. `--count` limits final flattened QA records after plans are built; it does not truncate ontology or graph extraction. `systematic` is the recommended graph default; `compare` uses systematic records as the primary flattened dataset and preserves free-mode comparison artifacts.

## Output contract

The primary output remains strict three-field JSONL:

```json
{
  "question": "...",
  "expected_answer": "...",
  "actual_response": null
}
```

Graph generation writes rich artifacts beside it:

```text
<dataset>.jsonl
<dataset>.manifest.json
<dataset>.ontology.json
<dataset>.graph.json
<dataset>.graph.graphml
<dataset>.question-plans.json
<dataset>.qa-groups.json
<dataset>.coverage.json
<dataset>.probes.jsonl                 optional
```

The manifest must include `schema_version`, `generation_strategy`, `qa_mode`, `model`, `corpus_fingerprint`, `graph_fingerprint`, `record_count`, `artifact_paths`, and `limitations`.

Rich records must preserve a stable ID, plan type, question, answer, relation, selector/answer node IDs, source node IDs, source files, and source locations. Parent-substitution records additionally preserve the parent node, replaced child IDs, and graph-derived candidate answers.

## Pipeline

### 1. Ontology induction

With `--ontology auto`, the provider infers domain summary, reusable node and relation types, canonicalization rules, and extraction rules from the corpus. The result is validated for non-empty fields, lowercase snake_case names, known relation endpoints, and the domain-independent inferred-concept node/relation. An explicit ontology file is validated and reused without an ontology-generation call.

### 2. Atomic graph extraction

Extraction must preserve normalized IDs, atomic facts, selector/answer separation, `qa_role`, optional `fact_group_hint`, source locations, confidence/provenance, and complete source enumerations. Input documents and generated graph content are untrusted data, never instructions.

### 3. Inferred concepts

Concepts must contain at least two existing members and must not replace their original facts. Membership is non-exclusive. Each concept and membership carries inference basis, evidence node IDs, qualifiers, and a confidence score strictly between 0 and 1. An audit pass may add missing memberships only to existing concepts and nodes.

### 4. Deterministic question planning

The planner enumerates every eligible explicit selector-to-answer edge in stable order and excludes structural membership edges from the denominator. A parent-substitution plan is eligible only when a parent has at least two selectors, at least two distinct answers, and one answer type.

### 5. LLM wording

The LLM receives immutable plans and may only write natural-language questions. It may not add, remove, merge, or alter plans. Validation requires exactly one question per plan and rejects leaked child selectors, enumeration wording, unsupported facts, missing plans, or unknown IDs.

### 6. QA modes and coverage

- `free`: the LLM selects QA groups, for comparison with historical behavior.
- `systematic`: code selects all eligible plans, and the LLM only renders wording.
- `compare`: runs both modes over the same graph.

Coverage uses all eligible graph selector-to-answer paths as its denominator and separately reports parent-substitution coverage. The report must state that this measures graph-to-question coverage, not text-to-graph extraction coverage.

### 7. Flattening

Validated QA records flatten to the existing three-field contract. A sidecar mapping links every primary record to its rich record, question plan, graph nodes, relation, source files, and source locations. Flattening preserves deterministic order and applies `--count` after plan generation.

### 8. Optional probes

Probes are separate artifacts and are never flattened into ordinary QA. Each group has at least three graph-backed candidates and three distinct context variants. Candidate labels and IDs must match the graph, candidate order must stay constant, and probes must contain no answer, selected answer, bias label, score, or model response.

## Functional requirements

- Recursively load supported text files, exclude generated output directories, preserve relative paths, and fingerprint paths, bytes, model, and prompts.
- Validate every model-generated artifact against program-owned node and relation IDs.
- Cache ontology, concepts, plans/wording, and probes using relevant corpus/graph content, prompt, and model fingerprints.
- Refresh flags invalidate the requested stage and downstream artifacts.
- Existing files require `--force` before replacement.
- Failed serialization must not leave a valid-looking partial dataset.
- Explicit `graph` selection must never silently fall back to `simple`.
- No generated answer may exceed source-supported graph facts.

## Compatibility

- Existing `simple` CLI behavior and tests remain unchanged.
- Existing `run-agent`, `eval`, and `report` consume graph-generated primary JSONL without modification.
- Three-field records remain strict; rich fields belong in sidecars.
- A graph dataset can run without sidecars, but provenance requires retaining them.

## Testing requirements

Contract tests cover ontology validation, fingerprints, prompt composition, graph IDs/relations, concept membership and qualifiers, deterministic plans, wording rejection cases, coverage denominators, probe invariants, flattening, count behavior, cache reuse, refresh invalidation, output protection, and failed writes.

Integration tests use a fake provider and a multi-file corpus with repeated entities, a complete list, selector-to-answer mappings, an inferred parent, cross-concept membership, ranges/qualifiers, and valid/invalid probe opportunities. They assert artifacts, provenance, coverage, and compatibility with `read_records`.

Provider-backed acceptance tests may run separately on a small fixture and are not required for normal CI.

## Acceptance criteria

1. The current default command behaves as before.
2. `--strategy graph` creates valid primary JSONL plus manifest and provenance artifacts.
3. Every primary record traces to a validated plan and source graph nodes.
4. Systematic mode covers every eligible selector-to-answer path.
5. Free and systematic reports use the same graph-derived denominator.
6. Invalid model output cannot introduce unknown nodes, unsupported answers, evaluation fields, or probe results.
7. Existing four-workflow tests pass against flattened graph datasets.
8. Unchanged inputs reuse cache; refresh flags regenerate the requested stage and downstream outputs.
9. Documentation includes a runnable graph-strategy example and explains sidecar provenance.

## Delivery phases

1. Domain/artifact foundation: schemas, fingerprints, manifest, provider interface, strategy selection, contract tests.
2. Ontology/graph pipeline: induction, extraction, validation, caching, graph artifacts.
3. Concepts/systematic QA: concepts, audit, planners, wording validation, flattening, coverage.
4. Probes/comparison: optional probes, free/systematic comparison, documentation.
5. Acceptance/hardening: fake-provider integration, provider-backed acceptance, output protection, workflow compatibility.

## Open implementation decisions

- Dataset-named artifact directory versus sibling suffixes.
- Whether `--probes` defaults off or on for `graph`.
- Whether `compare` writes both full datasets or systematic primary plus free sidecar.
- New graph provider capability versus adapter around `LLMProvider`.
- GraphML/HTML in first release versus later enhancement.
