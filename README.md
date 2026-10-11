# LLaDAR

[English](README.md) | [繁體中文](README.zh-TW.md) · [Documentation](https://stoday.github.io/LLaDAR/)

**Generate test cases, run your Agent, and evaluate what it actually says.**

LLaDAR is a command-line tool for testing Agents and chat websites. Turn your
knowledge documents into questions and expected answers, collect real responses,
and get a report with judgments, statistics, and the evidence behind them.

Start with a question set, or create a multi-turn situation to observe how an
Agent responds as a conversation develops. Describe what you want to evaluate
in plain language; use a local Skill when you need more control over the method.

## What can you test?

| What you want to learn | How LLaDAR helps |
| --- | --- |
| Does the Agent answer according to your documents? | Generate questions and expected answers from your knowledge, then test the target Agent. |
| How does a deployed chat website respond? | Test supported question pages through a guided browser flow, without supplying project source. |
| Does the Agent stay consistent across follow-up questions? | Create multi-turn situations with an observation goal and stopping rules. |
| Does it meet your own evaluation criteria? | Describe the criteria in plain language, or supply a Skill for a custom method. |

For example, you could check whether a support Agent invents a refund policy,
asks for missing information, or stays consistent when a user follows up.

An evaluator Agent judges the responses; Python computes the statistics.
Saved answers and record-level evidence let you inspect the judgments and
reevaluate the same responses with different criteria.

## Quick start

Install LLaDAR (Python 3.11 or 3.12):

```bash
python -m pip install lladar
```

Configure your model provider's credentials in `.env` or the process environment.
Your target Agent also needs its own runtime configuration. Check the installed
version with `lladar --version`.

With your knowledge documents in `./knowledge` and an Agent project in
`../my-agent`, run:

```bash
lladar create test-dataset --knowledge ./knowledge --output dataset.jsonl
lladar run-agent dataset.jsonl --project ../my-agent --output responses.jsonl
lladar eval responses.jsonl --output evaluation.json
lladar report evaluation.json --output report.md
```

This takes you from **documents → test questions → actual responses → evaluation
→ report**. Open `report.md` to review the results and individual test evidence.
You can also use an existing question set or reevaluate saved responses.

### Have a chat website instead?

Replace the `run-agent` command above with:

```bash
python -m playwright install chromium
lladar run-agent dataset.jsonl --page-url https://example.test/chat --output responses.jsonl
```

Use your own question-page URL. LLaDAR opens a browser and guides you through
sign-in, calibration, approval, and answer verification before running the tests.
See the [target selection guide](site/guides/run-agent.html#choose-target)
for supported pages and setup details.

### Decide what a good answer means

For example, evaluate whether the Agent recognizes missing information:

```bash
lladar eval responses.jsonl --criteria "Check whether the answer asks for missing information rather than inventing facts. Cite evidence from the response." --output evaluation-missing-info.json
lladar report evaluation-missing-info.json --output report-missing-info.md
```

For more control, provide a local `SKILL.md` directory with `--skill` to customize
generation, test scheduling, evaluation, or reporting. The workflow options below
explain where each method applies.

For adaptive conversations, start with the
[multi-turn situation example](example_project/situation_demo/README.md).
For a complete walkthrough with custom methods, see the
[user guide](site/index.html).

## Workflow options

All main workflow commands accept an optional `--output` directory or file path.
Omitting it writes into the current directory: a timestamped
`test-dataset-YYYYMMDD-HHMMSS.jsonl`, `situation.json`, `responses.jsonl`,
`evaluation.json`, or `report.md`. With `--output ./artifacts`, the same names
and their sidecars go into `./artifacts/`. Existing directories, trailing
separators, and new paths without a suffix are directories; existing files and
new paths with a suffix are filenames. For a new directory containing a dot,
use `./results.v1/`. Existing outputs still require `--force`.

After each successful stage, a work summary lists the absolute output directory,
each generated file and its purpose, and the next command or reading step.
This summary remains visible with `--no-verbose` and is also saved by `--log`.

Generate a dataset. `--output` accepts an explicit `.jsonl` path (as below),
or a directory in which the CLI creates a timestamped dataset.

```bash
lladar create test-dataset --knowledge ./knowledge --output dataset.jsonl
```

The bundled Akasha `knowledge-point-qa` skill reads source passages and generates
source-grounded questions. The Agent follows the selected skill and chooses
direct QA or an evidence graph for concept and controlled-variant probes; graphs
are optional. Python can also produce or repair candidates. Final results are
validated before publishing. Every dataset includes `dataset.jsonl.generation.json`;
`dataset.jsonl.graph.json` is written only when a valid graph is used.

To let the Agent discover actual datasets in a public GitHub repository:

```bash
lladar create test-dataset --from-github https://github.com/hsinmosyi/TWBias --count 20 --output dataset.jsonl
```

Choose `--knowledge` or `--from-github`. GitHub mode reads repository documents,
inspects CSV/JSON/JSONL or text data, and can follow documented public direct
data links. It saves `dataset.generation-report.md` and `dataset.sources.json`
alongside the usual generation sidecar. The report covers every output question:
original samples, fixed-version links, transformations, answer origin, model
review and limits. Generated criteria are identified separately from original
answers or annotations; review them before using the dataset. Repository code
is read for context, not executed. Private repositories, sign-in downloads,
archives and binary formats are not supported. See [GitHub generation](docs/github-dataset-generation.md)
for resource limits and custom Skill stages.

Graph probes observe how a target maps an inferred concept such as “meal” to
source-backed peers; they do not have a single correct answer and are reported
separately from correctness. Synthetic controls are test inputs, not claims
about the source or people. They are off by default. Use
`--controlled-variant-probes` in a terminal after generation lists its
validated dimensions, or use `--controlled-variant-topics DIMENSION_ID` in a
script with an ID recorded for the same corpus fingerprint. The core accepts
only planner-validated dimensions, so every pair remains comparable.

This requires Akasha 1.8 or later. Use `--skill DIRECTORY` to override the
default with one trusted local skill; there is no extra skill manifest or
installation command. LLaDAR passes the one selected directory to Akasha as
`skills=[...]`: Akasha initially exposes its metadata, then the model loads the
selected `SKILL.md` on demand. The bundled method is not a removable
user-installed skill. See [skill generation](docs/skill-generation.md) for
limits and Python usage.

For knowledge-optional adaptive conversations, see the [situation example](example_project/situation_demo/README.md). Both project modes share coding-agent discovery, tools and verified repair. With `max_turns=1`, session calibration checks one real answer; multi-turn configurations also check memory and fresh-session isolation.

Add `--adapt PATH_TO_ADAPTER.py --project PATH` to reuse an existing adapter in either project mode. LLaDAR skips discovery and generation, verifies the selected protocol in project copies, and stops on failure without rewriting the supplied adapter. Keep `--project`: the adapter still needs the application's source and runtime.

Run the target Agent. LLaDAR inspects a copied project and creates a temporary
adapter for its real public workflow:

```bash
lladar run-agent dataset.jsonl --project ../my-agent --output responses.jsonl
```

Which target option should you use?

- `--project PATH`: inspect the project's source and documentation to learn its public interface. A project alone can be enough: when startup details and the required runtime configuration are available, LLaDAR can start the service in an isolated project copy, test it, and stop only the service it started.
- `--project PATH --service-url URL`: inspect the same project, but use an already running test service. The URL supplies its **base address**, not its startup command or API contract. LLaDAR still learns the HTTP method, route, payload, authentication requirements, and response format from the project; it does not start or stop that existing service.
- `--page-url URL`: use a normal question webpage without providing project source. Sign in and submit a calibration question in LLaDAR's browser; LLaDAR records the request for replay and uses the model to extract answers from the captured responses.

Native WebSocket supports sequential text/JSON question frames when the server normally closes the connection after each answer (code 1000). During calibration, a completed, correlated DOM interaction can provide a fallback for persistent or binary protocols. The selected route is frozen for verification and dataset trials; a failure stops later submissions. DOM support covers native Streamlit chat (validated with 1.65.0) and accessible form chats with a unique input placeholder, named submit button, named conversation log, user/assistant message roles and observed busy-to-idle completion, including named same-origin iframes. Other page structures require adaptation; unchanged text alone is never completion evidence.

For an already running test service:

```bash
lladar run-agent dataset.jsonl --project ../my-agent --service-url http://127.0.0.1:8000 --output responses.jsonl
```

`--service-url` is optional in project mode, not an arbitrary-API discovery option.
Missing startup, authentication, or request/response details require clarification;
a URL alone does not fill those gaps. `--page-url` cannot be combined with
`--project` or `--service-url`. See the [target selection guide](site/guides/run-agent.html#choose-target).

Use a local skill when selecting a different execution method. The bundled
`run-agent-stability` skill runs every question three times; the run sidecars
record the selected cases, repeat counts, and seed.

```bash
lladar run-agent dataset.jsonl --project ../my-agent \
  --skill ./skills/random-sample --seed 42 --output responses.jsonl
```

The skill receives read-only cases and schedules them through a host callback;
it does not write the dataset or response files.
The two packaged run skills execute their bundled, fingerprinted strategy
locally, so they do not require a model call merely to calculate the schedule.
Custom local run skills are still interpreted by the configured skill Agent.

When you only have an authenticated question page, you do not need to discover
the API method, payload, or Cookie. Provision Playwright's matching Chromium once,
then provide the question-page URL:

```bash
python -m playwright install chromium
lladar run-agent dataset.jsonl --page-url https://example.test/chat --output responses.jsonl
```

Browser mode always guides you through sign-in, calibration, consent and `MATCH`.
Do not add `--interactive` or `--no-interactive`: these are project-only options
and are rejected with `--page-url`. Run directly in a terminal with both stdin and
stderr attached; piped input or redirected stderr stops the CLI before it opens
the browser. The approval flags do not remove this terminal requirement.

Each website request may wait up to 60 minutes by default (`--timeout 3600`).
Browser startup, initial page navigation and reload separately allow 5 minutes
(300 seconds); `--timeout` does not shorten or extend navigation waits.
In browser mode, a tool-free model reads the approved complete response and
reconstructs its original answer.
The browser-only default is `gemini:gemini-3.8-flash`; override it with
`--model gemini:MODEL`. Model availability depends on your provider account.

LLaDAR opens its own visible Chromium profile. Sign in yourself, submit the exact
calibration question, and wait until the answer is complete before pressing Enter.
Optionally set `--calibration-question "What are your support hours?"` to use
the same normal question for manual calibration and automatic verification,
without appending an identifier. If omitted, the original random `LLaDAR`
calibration and verification questions remain the default. This option requires
`--page-url`; blank questions are rejected.
Review the website requests, model destination and budgets together. One `YES`
approves both one verification question plus the displayed dataset trials AND
sending **real response content, including internal answers**, to
`https://generativelanguage.googleapis.com`. Ensure this is permitted by your
organization. `--confirm-browser-run` and
`--allow-response-model-transfer` approve only their respective scopes for this run.
Either flag alone still leaves the other scope unapproved; both skip the approval
prompt, not review.

The model needs `GEMINI_API_KEY` or `GOOGLE_API_KEY` from the environment or
`--env-file`; credentials are loaded only after calibration and consent.
For N scheduled trials (including repeats), at most **N+2 model calls** cover
calibration, verification, and each trial. Each response gets at most one call
and 8,192 output tokens; all calls and intervening verification share one
60-minute deadline after approval. There are no automatic retries or redirect
following. These token/call limits bound consumption, not a guaranteed price.
Packaged scheduling stays local; custom run skills may use a separate model.

Before dataset requests, compare the extracted verification answer with its new,
complete recorded response directly in the terminal (stderr). Type `MATCH`
only if faithful and complete. Approval flags cannot bypass this check.
The CLI requires terminal input and review output before any browser work. No HTML review
tab opens. Cyan marks source headings, magenta extracted-answer headings and yellow
notices, not correctness. `NO_COLOR` or `TERM=dumb` selects plain text. Complete
content is quoted without truncation; backslashes and terminal control/format
characters are displayed escaped without changing saved answers. Terminal scrollback
or recording may retain this private content. Browser sign-in/capture still require
Chromium; terminal review does not add a browser-free mode or per-dataset-answer MATCH.
A new request may return the same answer, but relabeling an old capture is not
verification. A valid model envelope or one successful review does **not** prove
every later answer is faithful, nor that the target answer is correct.
Scoring remains a separate `lladar eval` step.

Text, JSON/+json, NDJSON and SSE bodies are supported as model input. The model
must preserve original wording, numbers, negations, whitespace and Markdown;
it must not answer the question itself, summarize or repair answers.
The host checks request identity, completion, limits and the fixed result schema,
not answer field names. Streams must close, or calibration must have an explicit
local completion confirmation; an unclosed automatic replay times out rather than
accepting a guessed final event or partial answer.

Capture is limited to 1 MiB of decoded bytes after HTTP decompression.
The stricter model input limit is **120 KiB of UTF-8 JSON**, including framing;
this reserves room under a local 128 KiB byte-based context budget for the fixed
prompt (not a claim about a model's tokenizer or advertised context window).
Oversize input is rejected without truncation, summarization or chunking.
Known echoed request credentials and credential-like body fields block transfer;
this conservative detector cannot certify that all sensitive content is absent.
Cookies, headers, request payloads, profiles and expected answers are not sent.
Raw responses, full prompts, model diagnostics and thoughts are not saved in normal
artifacts. Final answer text is saved as `actual_response` in responses/trials;
safe run metadata records status, model/prompt version, counts, duration and
provider token usage when available (otherwise unknown).

The local session is reused from `.lladar/browser-profiles`; use
`--fresh-browser-profile` for a temporary signed-out profile. It never imports
your ordinary Chrome/Edge profile. HTTP 401/403, extraction failure or size limits
stop later dispatch; completed dataset answers remain. Sign in again and obtain
new approval for another run; no hidden authentication retry is sent.

Evaluate automatically:

```bash
lladar eval responses.jsonl --output evaluation.json
```

`eval` shows settings, per-trial progress, waiting heartbeats and results by default. Use `--no-verbose` to hide progress, or `--log eval.log` to save it.

Both evaluation modes accept `--max-input-tokens N` and `--max-output-tokens N` (positive integers). Defaults follow the selected model profile; evaluation JSON records the effective limits.

Use `--criteria "Describe what to check and what counts as evidence"` for ordinary
evaluation requirements. Advanced users can instead supply a complete method
with `--skill ./skills/my-verdict`; the two options are mutually exclusive.
Without either, evaluation uses the default method for the record type.
An explicit criteria or Skill evaluates all completed responses using that method.
Eval reads the trials sidecar when present; Python calculates aggregates and
correctness stability only when the plan includes boolean `correct`.

For situation creation, `--max-input-tokens N` and `--max-output-tokens N`
independently override the authoring model's token budgets with positive integers.
Omitted budgets use the selected model profile; the effective values are saved
in `created_with`. These options apply to config creation only.

`create situation --instructions TEXT` (or `--instructions-file PATH`) plans
generation, execution, stopping and default evaluation in one config. To reevaluate
saved dialogue,
keep the original config and add `--criteria` or `--skill` to situation eval:

```bash
lladar eval transcripts.jsonl --situation-config situation.json --criteria "Observe target terms and distinguish quotation from direct use" --output terminology-evaluation.json
```

The evaluation saves `evaluation_settings` with the effective mode, criteria text
and hash, or Skill source and file evidence. Reevaluation does not modify the
config or captured answers. Knowledge supplied to situation creation guides the
test generator; configure the target Agent's own knowledge separately.

Render the report:

```bash
lladar report evaluation.json --output report.md
```

Use `--skill ./skills/my-report` to supply a local evidence-summary method.

`eval` asks one Agent to freeze a set of boolean, categorical, or numeric
dimensions and judge each completed record. Counts, rates, distributions,
means, medians, minima, and maxima are calculated by Python. `report` renders
those saved facts and includes a record-level appendix.

## Operation logs

Verbose generation shows candidate previews as they are submitted and each final
question with its expected answer. Batch-file results appear when the Agent
delivers them. `run-agent` shows both before each trial and the actual response
as soon as it arrives; missing responses are omitted. Questions are cyan,
expected answers green, and actual responses magenta in color terminals.
These details are flushed immediately so you can inspect them and press Ctrl+C.

Add `--log PATH` to any workflow command to save its terminal output to a new
UTF-8 file while continuing to display it:

```bash
lladar create test-dataset --knowledge ./knowledge --output dataset.jsonl --log logs/create.log
```

The file includes emitted progress, Agent traces, summaries, and errors without
ANSI color codes. `--no-verbose` suppresses detailed output in both the terminal
and the log. Parent directories are created automatically; use a new filename
for each run because existing logs are preserved.

With `--page-url`, verbose output shows browser loading, calibration capture,
request parsing, website response waits, and model answer extraction. Long waits
show elapsed time every five seconds; completed responses show their byte count.
These progress messages also reach `--log`; `--no-verbose` hides them.
Each LLaDAR progress message includes local time with milliseconds and a UTC
offset, plus cumulative elapsed seconds (`[+12.345s]`). Browser waits also show
the current stage's duration (`stage_elapsed`); the log retains the same markers.

Browser failures include safe diagnostics in verbose output and the log: the
failure stage, reason, observed HTTP status or timeout limit when available, and
whether a request was attempted. Later blocked trials show
`reason=extraction_blocked request_attempted=false blocked_by=...`. Diagnostics
are also saved in the trials/run sidecars; raw exceptions, URLs, headers and
response bodies are excluded from these diagnostic fields.

Stream failures distinguish `stream_read_failed` from `utf8_decode_failed`.
`received_bytes` counts bytes delivered by the browser reader after HTTP
decompression, including bytes that could not be decoded. `elapsed_seconds`
measures replay time from starting fetch to failure, excluding model extraction.

## Copy-and-run walkthrough

The [detailed user guide](site/index.html) contains a PowerShell walkthrough
that creates a small knowledge file, writes the complete `SKILL.md` contents
for a randomized run, answer verdict, and evidence report, and then invokes all
four commands. The only value a user must supply is the absolute path to their
target Agent project. The target project and the LLaDAR model provider must be
configured before `run-agent` can make a live call.

Each local method is just a directory containing one `SKILL.md`; pass that
directory with `--skill`. There is no skill installation or management command.
The walkthrough deliberately uses:

- the bundled `knowledge-point-qa` skill for dataset creation;
- a local `run-agent-random-sample` skill to select up to five cases using a
  supplied deterministic random helper;
- a local `eval-answer-verdict` skill that submits one boolean `correct`
  judgment per trial; and
- a local `report-evidence-summary` skill that interprets only saved facts.

See the [Traditional Chinese guide](site/zh-TW/index.html) for the same
copy-and-run instructions in Chinese.

## Record format

Every JSONL line has exactly three fields:

```json
{"question":"What is Taiwan's capital?","expected_answer":"Taipei","actual_response":null}
```

`question` and `expected_answer` are non-empty strings. `actual_response` is a
string or `null`. There are no schema versions and unknown fields are rejected.

## Outputs

- `create test-dataset`: three-field JSONL with `actual_response: null` and `<output>.generation.json`; `<output>.graph.json` only when a valid graph is used
- `run-agent`: completed three-field JSONL, `<output>.trials.jsonl`, and `<output>.run.json`
- `eval`: evaluation plan, judgments, coverage, and aggregates in JSON
- `report`: Markdown tables, interpretation, limitations, and audit appendix

If browser calibration, confirmation, or verification stops before dataset
execution, LLaDAR writes only a redacted `<output>.run.json` blocker record; it
does not create responses or trials files.

Existing outputs are protected unless `--force` is specified. See
[the current product contract](docs/PRD-simple-agent-evaluation.md) for the full
behavior and safety boundaries.

## Development

```bash
python -m pip install -e ".[test]"
pytest
python -m playwright install chromium
python -m pytest -m browser
```

## Repository structure

The main source, documentation, and example paths are shown below. This is a
navigation map, not an exhaustive listing; generated datasets, reports, caches,
virtual environments, and build artifacts are omitted.

```text
LLaDAR/
├── pyproject.toml              # Package metadata, dependencies, and lladar CLI entry point
├── uv.lock                     # Locked dependency versions for uv
├── README.md                   # English overview and quick start
├── README.zh-TW.md             # Traditional Chinese overview and quick start
├── qa_agent.py                 # Standalone Akasha QA script; not the lladar CLI entry point
├── src/lladar/                 # Installable Python package
│   ├── cli.py                  # CLI arguments and command dispatch
│   ├── api.py                  # Python API for creating test datasets
│   ├── skill_generation.py     # Source evidence, optional graphs, final validation, and dataset output
│   ├── skill_agent.py          # Thin adapter to Akasha's native dynamic skill loading
│   ├── method_skill.py         # Shared skill resolution and invocation for run/eval/report
│   ├── skill_assets/           # Bundled skills and supporting resources
│   │   ├── knowledge-point-qa/       # Dataset-generation method (SKILL.md)
│   │   ├── run-agent-stability/      # Repeated-run method (SKILL.md and strategy.py)
│   │   ├── run-agent-random-sample/  # Random-sampling method (SKILL.md and strategy.py)
│   │   ├── eval-answer-verdict/     # Answer-evaluation method (SKILL.md)
│   │   └── report-evidence-summary/ # Evidence-based report method (SKILL.md)
│   ├── runner.py               # Execute target agents and record responses/trials
│   ├── run_strategy.py         # Run scheduling and strategy validation
│   ├── auto_adapter.py         # Discover, verify, and replay target-project adapters
│   ├── project_profile.py      # Describe target projects from source evidence
│   ├── browser_target.py       # Browser-target calibration and question replay
│   ├── evaluation.py           # Answer judgments and deterministic aggregation
│   ├── reporting.py            # Render saved evaluation facts and report narratives
│   ├── semantic_graph.py       # Classify probe responses against source candidates
│   ├── controlled_variants.py  # Select and validate planner-declared control dimensions
│   ├── question_types.py       # Question-type and probe contracts, fingerprints, and scoring
│   ├── records.py              # Read, validate, and write dataset records
│   ├── loaders.py              # Load knowledge-source text
│   └── providers/              # Model-provider interfaces and Akasha implementation
├── tests/                      # Automated tests, browser tests, and target-project fixtures
├── scripts/                    # Live acceptance, packaging, and release verification scripts
├── example_project/            # End-to-end target projects
│   ├── diet/                   # Dietary QA agent and knowledge source
│   ├── resume_review/          # Resume-review service and console example
│   └── tainan_tutorial/        # Tainan recommendation demo, adapters, data, and tests
├── examples/support-demo-agent/ # Support-agent HTTP demo
├── akasha-agent-example/       # Small Akasha agent and hello-skill example
├── docs/                      # PRDs, design notes, verification records, and presentations
├── site/                      # English documentation website
│   └── zh-TW/                 # Traditional Chinese documentation website
├── .github/workflows/         # Release and documentation-deployment automation
└── .codex/skills/             # Development-assistant skills, separate from runtime skills
```

To follow dataset creation, start with `cli.py` → `api.py` →
`skill_generation.py`; skill loading is implemented in `skill_agent.py`, while
the method instructions live in `skill_assets/*/SKILL.md`. For the remaining
commands, start with `runner.py` (`run-agent`), `evaluation.py` (`eval`), and
`reporting.py` (`report`). To customize a method, pass a local skill directory
with `--skill` rather than editing the bundled assets.

For target integration, also see `adapter_workspace.py`, `project_inventory.py`,
`graph_discovery.py`, and `interfaces.py` for workspace inspection and discovery;
`target_environment.py` and `service_runtime.py` for execution environments and
managed services; and `playwright_driver.py`, `response_capture.py`, and
`answer_extraction.py` for browser operation and response extraction.

## Large inputs and model limits

LLaDAR's download and sampling limits are application safeguards, rather than the
language model's context limits. The current GitHub-generation budgets are:

| Budget | Current maximum | Purpose |
| --- | ---: | --- |
| Ordinary text download | 2 MiB per response | Bound source downloads |
| CSV/JSONL streaming download for sampling | 16 MiB per file | Bound downloaded source size |
| Cumulative downloads | 20 MiB | Bound total transfer |
| Sampling scan | 200,000 logical rows | Bound scanning work |
| Retained sample | 100 rows | Bound subsequent generation work |

A retained sample must also fit the selected-text and model budgets; 100 rows
does not guarantee that a sample fits. These limits apply to GitHub generation,
not to every input or browser response in the pipeline.

Model input/output token budgets are separate. Built-in generation assigns
source batches of at most 24,000 characters, reduced when the configured model
input budget is smaller. Characters, UTF-8 bytes and tokens are different units.
The model receives selected or batched evidence, rather than an entire downloaded
file in one request.

Increasing `--max-input-tokens` or `--max-output-tokens` does not raise the
download or sampling caps, or extend the provider's actual model capacity.
Download caps are currently built-in safeguards, not dedicated CLI options.
Raising them requires checking scanning, storage, batch merging, cross-batch
relationships and runtime cost.

Offline tests cover a 100,000-row CSV larger than 2 MiB, CSV fields containing
newlines, limited JSONL scans, an 84,000-character local text source, and complete
captures of about 1.7 MB per trial through a callable target. The callable test
does not change browser capture limits. GB/TB-scale inputs have not been
validated. Exceeding a budget or encountering a provider/disk failure can still
stop a stage; a saved session can be resumed using the interruption instructions.
Sampling and full-data processing are distinguished in the saved evidence.

See the [validation record](docs/validation/recoverable-stages-2026-10-11.zh-TW.md)
for tested scope and remaining acceptance work.

## Resume interrupted work

Generation, `run-agent`, `eval`, and `report` save a work session next to their
output, usually `<output>.work`. Continue explicitly with the same inputs,
model, method and options:

```bash
lladar create test-dataset --knowledge ./knowledge --output dataset.jsonl --resume dataset.jsonl.work
lladar run-agent dataset.jsonl --project ../my-agent --output responses.jsonl --resume responses.jsonl.work
lladar eval responses.jsonl --output evaluation.json --resume evaluation.json.work
lladar report evaluation.json --output report.md --resume report.md.work
```

Use the session path printed in the work summary if it has a suffix. Python APIs
accept `resume=Path(...)` too. `--resume` cannot be combined with `--force`.
Normal runs still support `--force`. If a stage is interrupted, the CLI prints the
working directory, input/output locations, actual session directory, and a complete
quoted command to resume with the original options. A run originally authorized
with `--force` retains that authorization in its session; the suggested resume
command omits `--force`. Private browser/service URLs are entered locally rather
than echoed into the command or log.
Changed inputs, saved evidence, criteria, Skill files or model settings are
rejected. Completed requests and accepted judgments are reused; a target request
whose completion is unknown is recorded without automatic replay. Starting a new
run creates distinct trials.

Keep the work directory until recovery and review are finished. It contains
source evidence, drafts and captured responses, so apply the same access controls
as to the original inputs. Credentials are not saved in the session configuration.

Built-in free-question generation uses atomic tools rather than model-written
candidate files. Large CSV/JSONL sources can be streamed and reproducibly sampled;
sampling limits and original row IDs are included in the generation report. A
sample is not a reproduction of the original benchmark. See the
[implementation and validation record](docs/validation/recoverable-stages-2026-10-11.zh-TW.md).
