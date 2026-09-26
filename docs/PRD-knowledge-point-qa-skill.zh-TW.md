# LLaDAR knowledge-point-qa skill

## Status

Proposed prototype requirement. This document defines the first skill-based dataset-generation method. It is intentionally smaller than the graph-based generation PRD and is meant to validate the skill runtime, tool boundary, provenance, and multi-stage generation model before implementing graphify skills.

## Summary

Add a local Akasha skill named `knowledge-point-qa` that generates LLaDAR test datasets through two explicit stages:

```text
knowledge source
  → semantic chunk
  → source-grounded knowledge points
  → one QA record per knowledge point
  → validation and deduplication
  → ordinary three-field LLaDAR dataset
```

The existing chunk-based generator remains available and remains the default. The new skill is selected explicitly and writes a provenance sidecar containing the knowledge points and their mapping to final records.

## Problem

The current simple generator asks for one question and answer per chunk. A chunk can contain several independent facts, so the model may represent only one fact and silently drop the others. The resulting dataset has no reliable knowledge-point coverage measure and no durable trace between a question and the exact fact that motivated it.

## Goals

- Prove that a dataset-generation method can be loaded as a local skill.
- Separate knowledge-point extraction from QA wording generation.
- Extract multiple independently answerable knowledge points from one semantic chunk.
- Generate at least one source-grounded QA record per accepted knowledge point.
- Preserve exact evidence text and source location for every knowledge point.
- Preserve the mapping from final dataset records to knowledge points and source files.
- Keep the existing three-field JSONL contract compatible with `run-agent`, `eval`, and `report`.
- Provide deterministic validation, deduplication, caching, and coverage statistics.
- Keep all final dataset writes and artifact validation under LLaDAR core control.

## Non-goals

- Implementing a knowledge graph, ontology induction, inferred concepts, or GraphML.
- Implementing open-choice probes.
- Replacing the current simple generator.
- Letting the skill write arbitrary files or directly bypass LLaDAR validation.
- Supporting remote skill installation or a public skill marketplace.
- Generating multiple question styles per knowledge point in the first prototype.
- Changing `run-agent`, `eval`, or `report`.

## User experience

The current command is unchanged:

```bash
lladar create test-dataset --knowledge ./knowledge
```

The prototype is explicit:

```bash
lladar create test-dataset \
  --knowledge ./knowledge \
  --skill ./skills/knowledge-point-qa
```

The first prototype supports one dataset-generation skill per invocation. The runtime and artifact model must be designed so repeatable multi-skill selection can be added later without changing the skill contract.

## Skill package

The local skill package must contain at least:

```text
skills/knowledge-point-qa/
├── SKILL.md
└── prompts/
    ├── extract-knowledge-points.txt
    └── generate-qa.txt
```

`SKILL.md` is the only required skill entry point and is loaded by Akasha. It contains the agent-facing method instructions and uses the existing skill frontmatter convention. The first prototype uses a fixed LLaDAR dataset-generation contract and fixed core tool allow-list; the skill does not need a separate machine-readable manifest.

Expected frontmatter:

```yaml
---
name: knowledge-point-qa
description: Extract source-grounded knowledge points and generate one QA per point.
---
```

Skill instructions live in `SKILL.md`. LLaDAR owns tool availability, permissions, output contracts, and validation. No separate skill manifest is part of this design, including its future extensions. The runtime records content fingerprints of the skill files for reproducibility without requiring an author-maintained version number.

## Domain model

### Knowledge point

A concise, independently understandable statement of one source-supported fact or rule. It is not a heading, a vague topic, a multi-fact summary, or a model-generated recommendation.

Required fields:

```json
{
  "id": "kp_0001",
  "statement": "進食順序建議為湯、菜、肉、蛋、飯。",
  "evidence_text": "進食順序：「湯菜肉蛋飯」...",
  "source_file": "knowledge/diet.md",
  "source_location": "核心三項重點 > 吃對比吃少更重要",
  "topic": "進食順序"
}
```

The core assigns or validates stable IDs. `evidence_text` must be present in the declared source content, subject to the source loader's normalization rules.

### Rich QA record

```json
{
  "id": "kp_0001__qa__001",
  "knowledge_point_id": "kp_0001",
  "question": "減重時建議的進食順序是什麼？",
  "answer": "湯、菜、肉、蛋、飯。",
  "source_file": "knowledge/diet.md",
  "source_location": "核心三項重點 > 吃對比吃少更重要"
}
```

The answer must be supported by the knowledge point and its evidence. The QA stage must not introduce a fact that was absent from the point or source.

### Primary LLaDAR record

The final JSONL record remains exactly:

```json
{
  "question": "減重時建議的進食順序是什麼？",
  "expected_answer": "湯、菜、肉、蛋、飯。",
  "actual_response": null
}
```

## Core tools

The runtime provides a fixed tool allow-list with enforced access boundaries. Skills do not declare a separate tool manifest. The first prototype provides:

- `knowledge.list_sources`: list declared knowledge sources.
- `knowledge.read`: read bounded source text or a semantic chunk.
- `knowledge.emit_point`: submit a candidate knowledge point for core validation.
- `qa.emit`: submit a candidate rich QA record for core validation.
- `artifact.write`: write an approved skill artifact through the artifact manager.

The skill cannot directly write the final dataset, read files outside declared knowledge and configured prompts, execute arbitrary shell commands, or alter validation rules.

Core-owned operations include:

- source loading and source-boundary checks;
- ID assignment and uniqueness;
- evidence-text verification;
- QA schema validation;
- answer/source consistency checks where deterministic checks are possible;
- deduplication;
- coverage calculation;
- flattening;
- final dataset and manifest writes.

## Functional requirements

### FR1 — Skill discovery and validation

The CLI must accept a local skill path. The runtime must locate and validate `SKILL.md`, reject a missing or unreadable skill entry point, load it through Akasha, and record the resolved skill path, skill name, and skill content fingerprint in the generated dataset manifest. The first prototype uses the fixed `knowledge-point-qa` contract and runtime-owned tool allow-list.

### FR2 — Two-stage execution

The runtime must execute knowledge-point extraction before QA generation. QA generation must receive validated knowledge points, not raw unvalidated model output. If point extraction fails, the runtime must not generate QA for that failed point.

The two stages must be independently cacheable:

```text
source + extraction prompt + model → knowledge-points artifact
knowledge-points + QA prompt + model → rich-QA artifact
```

### FR3 — Knowledge-point extraction

The skill must process each accepted semantic chunk and may emit zero or more points. Every accepted point must:

- be independently understandable;
- contain a non-empty statement and evidence text;
- cite one declared source file and location;
- have evidence text found in that source;
- avoid unsupported numbers, causes, recommendations, exceptions, or conditions;
- not duplicate an existing point in the same corpus;
- remain separate from other facts when combining them would obscure answerability.

The system must report chunks with zero accepted points rather than silently treating them as covered.

### FR4 — QA generation

The first prototype requires exactly one QA record per accepted knowledge point. Each question must be standalone, source-grounded, and answerable from the point. Each answer must be concise and supported by the point's evidence.

The runtime must reject:

- missing or unknown knowledge-point IDs;
- empty questions or answers;
- answers that add unsupported facts;
- questions that cannot be understood without referring to an omitted chunk;
- evaluation fields such as `actual_response`, scores, bias labels, or judgments;
- duplicate question/answer pairs.

### FR5 — Provenance and artifacts

The core must automatically produce a dataset run manifest and a rich artifact containing the following information. This manifest records execution results; it is not a skill configuration file and requires no manual maintenance.

- skill name, resolved path, and content fingerprints of the skill files;
- model and relevant prompt fingerprints;
- source/corpus fingerprint;
- all accepted knowledge points;
- rejected candidates and validation reasons, when verbose artifact output is enabled;
- every rich QA record;
- final-record mapping;
- point coverage and counts;
- limitations and skipped chunks.

Suggested files:

```text
<dataset>.jsonl
<dataset>.manifest.json
<dataset>.knowledge-points.json
<dataset>.rich-qa.jsonl
<dataset>.coverage.json
```

### FR6 — Flattening

The core must convert validated rich QA records to the current three-field records. `actual_response` is always `null`. The flattening order must be deterministic by source order, chunk order, point ID, and QA ID, unless an explicit seed is supplied.

### FR7 — Count and deduplication

`--count` is the maximum number of final ordinary QA records, not the maximum number of chunks or knowledge points. The runtime should complete point extraction first, then generate and validate QA until the count is reached or all points are exhausted.

Deduplication must be performed before final count selection. The manifest must report candidate, accepted, duplicate, rejected, and emitted counts.

### FR8 — Coverage

The primary coverage metric is:

```text
knowledge-point QA coverage
= points with at least one emitted QA / accepted knowledge points
```

The report must separately include:

- source files and chunks processed;
- accepted points;
- points with valid QA;
- points without valid QA;
- final emitted records;
- records removed as duplicates;
- records omitted because of `--count`.

This metric measures point-to-question generation coverage. It does not measure whether the model extracted every fact present in the source.

### FR9 — Caching and refresh

The runtime must cache knowledge points independently from QA records. A QA prompt/model change must not require point extraction again. A source or extraction-prompt change invalidates point and downstream QA artifacts.

The implementation must provide refresh behavior for at least:

```text
--refresh-knowledge-points
--refresh-qa
```

### FR10 — Safety and output protection

Knowledge text is untrusted data, not instructions. Skill instructions and source text must be passed in separate trust-boundary sections. Existing output files require `--force`. A failed run must not replace a valid existing dataset with a partial result.

## Compatibility

- The default command remains the current simple generator.
- The resulting JSONL passes the existing `read_records` validator.
- `run-agent`, `eval`, and `report` require no changes.
- The skill path, generated dataset manifest, and rich artifacts are optional metadata from the perspective of the three-field dataset reader.
- The first prototype supports one selected skill per invocation, while the generated dataset manifest and artifact model reserve `generation_method` for future multi-skill runs.

## Testing requirements

### Unit tests

- `SKILL.md` discovery and validation, including successful loading without a separate skill manifest;
- automatic dataset manifest generation and skill content fingerprint recording;
- missing required skill files;
- tool allow-list enforcement;
- evidence text must belong to the declared source;
- invalid point schema and duplicate point rejection;
- unknown point ID rejection during QA generation;
- unsupported QA fields rejection;
- duplicate QA handling;
- deterministic ordering and `--count` behavior;
- point and QA fingerprint invalidation;
- final output protection;
- exact compatibility with `read_records`.

### Fake-provider integration test

Use a multi-section fixture where one chunk contains at least three independent facts. Assert that:

1. the skill emits three accepted knowledge points;
2. each point has source evidence;
3. each point receives one QA record;
4. the final dataset has three valid LLaDAR records;
5. the rich artifact maps all three records to their points;
6. one invalid QA response is rejected without invalidating the other points;
7. rerunning QA with an unchanged point artifact reuses point extraction.

### Live acceptance test

A provider-backed skill run may be added separately. It must use a small fixture, remain outside normal offline CI, and assert artifact contracts rather than exact wording.

## Acceptance criteria

1. The existing default generation behavior remains unchanged.
2. A local `knowledge-point-qa` skill can be selected from the CLI.
3. The skill executes point extraction and QA generation as separate validated stages.
4. Every emitted QA record maps to exactly one accepted knowledge point and source evidence.
5. The output remains readable by the existing LLaDAR dataset reader.
6. Point coverage, QA coverage, deduplication, rejection, and count statistics are persisted.
7. Invalid skill output cannot write an invalid final dataset or escape the declared knowledge/tool boundary.
8. Stage-level cache reuse and refresh behavior are tested.
9. No changes are required in `run-agent`, `eval`, or `report`.

## Future extensions

- Multiple `--skill` values with explicit merge policies.
- Multiple QA variants per knowledge point.
- Graphify skill using ontology and graph tools.
- Probe skill with a separate non-QA output contract.
- Runtime-managed sharing of intermediate artifacts between skills.
- Installed or remote skill discovery after local skill security is proven.
