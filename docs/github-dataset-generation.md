# Generate QA from GitHub datasets

```powershell
lladar create test-dataset --from-github https://github.com/hsinmosyi/TWBias --count 20 --output dataset.jsonl
```

Use one public repository root URL, instead of `--knowledge`. The CLI resolves
the default branch to a fixed commit. The Agent browses files and documentation,
inspects actual data and selects files or specific 1-based data rows. Supported
formats are Markdown, TXT, CSV, JSON and JSONL in UTF-8. Documented public direct
HTTP(S) data links are supported; external data is frozen by content hash and
retrieval time, not by the repository commit. No repository programs are run.

The bundled `github-dataset` Skill has three stages: `github_discovery`,
`generation`, and `github_review`. A custom `--skill DIRECTORY` must handle
all three. Discovery uses `list_repository_files`, `read_repository_file`,
`read_dataset_rows`, `read_external_dataset` and `select_dataset`. Its quoted
selection evidence must match a repository read. Optional `row_selections`
maps selected paths to original 1-based data row numbers, excluding the header.

Generation uses the existing source, point, QA and optional graph tools. Every
final QA also requires `record_answer_basis(question, expected_answer,
answer_basis, rationale, evidence_point_ids)`. Origins are `source_answer`,
`source_annotation`, or `derived_criterion`. Refer to accepted knowledge point
IDs, including supporting label definitions. Derived criteria must not be
represented as original gold answers or factual claims about social groups.
Python delivery remains available and faces the same final validation.

A separate model pass receives frozen QA and source evidence and calls
`submit_generation_review` with each fingerprint, boolean approval and reason.
Rejected questions are omitted and recorded. Model approval checks semantic
consistency; it is not human review or author endorsement.

GitHub mode always saves these artifacts together:

- `dataset.jsonl`: the usual three-field records.
- `dataset.jsonl.generation.json`: settings, methods, evidence and reviews.
- `dataset.generation-report.md`: every final question, source excerpts,
  stable references, transformations, answer origin, checks and limitations.
- `dataset.sources.json`: downloaded originals, hashes, normalized data,
  selected row positions and external retrieval information.
- `dataset.jsonl.graph.json`: only when a valid graph was used.

Open the generation report before running the target Agent. Relative links
lead to saved evidence. A failed report publication does not count as success;
previous artifacts are restored if an overwrite fails. Without `--force`,
existing report and evidence paths are protected before any download or model work.

Download limits per run: 2 MiB per response, 20 MiB total, 100 HTTP requests,
20-second network timeout, 5 redirects per download and 2,000 repository files.
Private/local/reserved network targets and redirects to them are rejected.
Tool and model limits also apply. `--count` caps final questions, not downloaded
rows; fewer questions may pass validation. `0` does not remove resource limits.
Skipped or unprocessed data is not claimed as covered. Unsupported sources
include private repos, login/paywall pages, archives, LFS objects, submodules
and binary formats. The Python API requires `output` for GitHub mode so the
mandatory report and evidence can be saved.

Selected normalized text is limited to 24,000 characters (also bounded by half the model input token limit). Large tables require explicit row sampling. Each native stage allows up to 160 host tool calls and 80 model rounds; these limits and the selected rows are saved in the evidence. Failed runs save a collision-safe `*.generation-failure.json` diagnostic without publishing a successful dataset.
