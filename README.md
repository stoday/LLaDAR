# LLaDAR

[English](https://github.com/stoday/LLaDAR/blob/main/README.md) | [繁體中文](https://github.com/stoday/LLaDAR/blob/main/README.zh-TW.md) · [Documentation](https://stoday.github.io/LLaDAR/)

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
| Does the Agent give the correct answer from your knowledge documents? | Generate questions and expected answers from your knowledge, then compare the target Agent's responses. This is not a complete hallucination check. |
| How does a deployed chat website respond? | Test supported question pages through a guided browser flow, without supplying project source. |
| Does the Agent stay consistent across follow-up questions? | Create multi-turn situations with an observation goal and stopping rules. |
| Does it meet your own evaluation criteria? | Describe the criteria in plain language, or supply a Skill for a custom method. |

For example, you could test a support Agent's answers against a refund policy,
check whether it asks for missing information, or observe whether it stays
consistent when a user follows up. A source-grounded dataset does not by itself
prove that every extra claim is unsupported; that requires suitable evidence and
evaluation criteria.

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

To clarify a goal before testing, start an interactive planning conversation:

```bash
lladar plan "Check whether my support Agent follows the refund documents" --debug
```

The planner selects a workflow profile, asks for missing decisions, and submits
a draft `PlanSpec`. Python validates local inputs and stage dependencies, then
compiles the commands. It supports document QA, behavior situations, reevaluating
saved responses, and reporting an existing evaluation. Planning does not call the
target or run any test stage. See [the plan v2 specification](docs/PRD-plan-v2.zh-TW.md).

Each new plan directory contains `plan.md`, `plan.json`, `plan-events.jsonl` and
`plan.log`. `--debug` adds detailed redacted console events; `--log PATH` selects
a new text log file. `ready` plans include `commands.ps1` for review and later
execution; `blocked` and `failed` plans contain `diagnostics.json` with error
codes, trace IDs and next actions. A ready plan confirms static checks, not that
the target works. Existing plan directories and log files are preserved.

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
sign-in, discovery approval, and independent full-answer verification before running the tests.
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
Omitting it writes into `runs/` under the current directory: a timestamped
`test-dataset-YYYYMMDD-HHMMSS.jsonl`, `situation.json`, `responses.jsonl`,
`evaluation.json`, or `report.md`. With `--output ./artifacts`, the same names
and their sidecars go into `./artifacts/`. Existing directories, trailing
separators, and new paths without a suffix are directories; existing files and
new paths with a suffix are filenames. For a new directory containing a dot,
use `./results.v1/`. Existing outputs still require `--force`.

Generated answers, sidecars, resume workspaces and exported adapters stay beside
their main output in `runs/` unless you set `--output` or `--export-adapter`.
Repository tests keep temporary files in `.tmp/pytest/` and their cache in
`.tmp/pytest-cache/`; an explicit pytest `--basetemp` overrides that location.
To preview cleanup of test/build caches, `runs/`, and legacy root-level
`responses.jsonl` / `response.jsonl` and evaluation artifacts, run:

```bash
python scripts/cleanup_generated.py --all-generated
python scripts/cleanup_generated.py --all-generated --apply
```

The second command lists the candidates and asks you to type `DELETE`. Deleting
run artifacts loses saved answers, resume checkpoints, exported adapters and
captured state inside them. Tracked files, root `.env` / `.venv`, and `.lladar`
login profiles are preserved. Existing artifacts are not moved automatically.

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

This step creates questions and expected answers from your knowledge documents. It is
not a complete hallucination detector. Checking unsupported extra claims needs an
evaluation method with enough source evidence.

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

Adapter discovery, generation and repair use a model request timeout of up to 180 seconds (or the shorter target timeout), without SDK retries. Model timing, safe token usage and finish reasons are saved in `adapter/model-events.jsonl`. Explicit, request-correlated target provider failures and overall target execution timeouts stop the run instead of triggering adapter repair; a process timeout alone does not establish a provider failure.

For `gemini-3.8-flash`, adapter-model calls use `LOW` thinking with thought summaries disabled. This model cannot turn reasoning off completely; `LOW` is its minimum supported effort. Input and output token ceilings are unchanged.

Run the target Agent. LLaDAR inspects a copied project and creates a temporary
adapter for its real public workflow:

```bash
lladar run-agent dataset.jsonl --project ../my-agent --output responses.jsonl
```

Which target option should you use?

- `--project PATH`: inspect the project's source and documentation to learn its public interface. A project alone can be enough: when startup details and the required runtime configuration are available, LLaDAR can start the service in an isolated project copy, test it, and stop only the service it started.
- `--project PATH --service-url URL`: inspect the same project, but use an already running test service. The URL supplies its **base address**, not its startup command or API contract. LLaDAR still learns the HTTP method, route, payload, authentication requirements, and response format from the project; it does not start or stop that existing service.
- `--service-url URL --api-guide FILE`: build an HTTP adapter from API usage documentation and examples without a target project. Supports TXT, MD, DOCX and text PDF; PDFs without readable text fail immediately, with no OCR.
- `--page-url URL`: use a normal question webpage without providing project source. Sign in in LLaDAR's browser, handle any website dialogs, open the conversation page/window and keep the input box empty, then return to the terminal and press Enter. If several pages are open, select the conversation page by its displayed index. An Akasha Agent explores observed communication and page operations, qualifies a generated protocol/session programme, and a separate tool-free model faithfully extracts the captured answers.

HTTP, persistent SSE, native text/binary WebSocket and DOM fallback use the same discovery tools and qualification contract. The Agent investigates actual session IDs, envelopes and terminal evidence instead of assuming a website schema. Local fixtures validate these transport paths; real-site acceptance is recorded separately.

For an already running test service:

```bash
lladar run-agent dataset.jsonl --project ../my-agent --service-url http://127.0.0.1:8000 --output responses.jsonl
```

For documentation-only APIs:

```bash
lladar run-agent dataset.jsonl --service-url http://127.0.0.1:8000 --api-guide docs/api-guide.md --env-file .env --lang en
```

The guide must document the HTTP method, relative endpoint, authentication or explicit absence of authentication, required headers/parameters and question mapping, request/response examples, unchanged answer extraction and completion rules. Missing contracts stop before API calls; multiple candidates require selection. TXT/MD must use UTF-8 (BOM accepted); DOCX includes paragraphs and tables; PDF reads existing text only. Example hosts never override `--service-url`; credentials come from the environment or `--env-file`.

Guide mode supports synchronous JSON and documented SSE/polling completion and correlation. It shares independent adapter verification, `--adapt`, `--resume` and automatic `OUTPUT.api-adapter` export. It cannot be combined with `--project`, `--page-url` or multi-turn situation mode. Resume requires the unchanged guide and service configuration. See the [document-mode specification](docs/PRD-api-guide-adapter.zh-TW.md).

Generating a single-question HTTP adapter with `--project --service-url` automatically produces a shareable API client bundle at `OUTPUT.api-adapter`. No export flag is needed:

```powershell
lladar run-agent dataset.jsonl --project ../my-agent --service-url http://127.0.0.1:8000 --output responses.jsonl
```

Generation inspects the project for the HTTP contract and authentication method. A selected existing public HTTP interface must pass verification from an empty working directory using isolated Python without site packages before delivery. `responses.jsonl.api-adapter` contains a self-contained `adapter.py`, a cross-platform `README.md`, and a commented `.env.example` listing the service URL, timeout and required credential names with empty credential values. The adapter reads process environment variables; it does not load `.env` automatically. With `--lang`, non-English documentation and configuration comments are translated through `akasha.ask` using the adapter model; code, commands and configuration names stay unchanged. The translation is cached for resume. English needs no translation call. The Python file runs on its own: recipients need Python 3.11+, their own credentials and API network access, without the project or a LLaDAR installation. Optionally use `--export-adapter api-client` to customize the directory.

```powershell
$env:LLADAR_SERVICE_URL = 'https://your-api.example.com'
# Set your own credential variables listed in the README or adapter.py: REQUIRED_ENV.
python responses.jsonl.api-adapter/adapter.py --message 'What can this service help with?'
```

`--service-url URL` also overrides the address. The generated adapter preserves project authentication (Bearer, API key, custom headers, cookies, or a reproducible credential exchange), reading credential values from the recipient's environment. HTTP helpers, required variable declarations, CLI and validation are folded into `adapter.py`: no `.env`, project, browser login state or run evidence is exported. `LLADAR_API_TIMEOUT` sets the total question deadline and default HTTP timeout in seconds (default 60), including streams and polling; an explicit per-request timeout cannot extend that deadline. For programmatic use, omit `--message` and send `{"request_id":"q1","message":"question"}` on stdin; stdout is JSON with the same `request_id`, `output` and `observation`.

The API server remains responsible for account/role/tenant/resource authorization, credential issuance, expiry and revocation. Sharing a client grants no server permissions. Missing required variables stop before requests; 401/403 are not bypassed. Redirects are rejected: specify the final API base URL. Replace localhost with a reachable server address for remote use and configure its listener/network separately; use HTTPS when sending credentials remotely. Browser login, MFA and cross-site authentication need an independently reproducible contract.

The generated README includes Python version checks, local venv setup, this adapter's required credential variable names, and launch commands for Windows PowerShell and Linux/macOS bash/zsh. Only the Python 3.11+ standard library is needed, so no requirements.txt or pip install step is included. Do not distribute the creator's `.venv`. Optionally add `-I -S` to match generation-time Python isolation. Verification covers the system running it, not every recipient OS.

Bundles support single-question DATASET mode with `--service-url` and either `--project` or `--api-guide`. The destination must not exist; `--resume` accepts only an identical existing bundle. To export an older project adapter, combine `--adapt` with `--export-adapter`; guide mode also exports reused adapters automatically. Supplied source must satisfy the portable contract and pass verification again. Without explicit export, ordinary reused project adapters retain the existing `--adapt --project` flow. Generated code runs with user permissions; directory/Python isolation is not an OS sandbox.
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

When you only have an authenticated question page, provide `--page-url` and demonstrate one complete question and answer. You do not need to find API methods, cookies or selectors.

```bash
python -m playwright install chromium
lladar run-agent dataset.jsonl --page-url https://example.test/chat --output responses.jsonl
```

Run directly in an interactive terminal. Review the website, actual model destination, material scope and budgets, then type `YES`.
In the visible browser, sign in, finish website verification, enter the service, open a new conversation, submit the full displayed demonstration question and wait for the complete answer. Return to the terminal and press Enter only when done.
The program saves events, snapshots and necessary session state, then closes the recording browser.

The host builds a bounded recording index, then the material Agent selects useful events and explains why. The adapter Agent receives that material packet and can only submit complete `adapt(payload)` code or name a specific missing observation; it has no recording search or read tools. The host locally checks that a fresh question enters the send action, then immediately tests the candidate on the real website. A failed preflight or live trial returns the diagnostic and previous source to the adapter Agent for a new submission. Only a specific evidence gap returns work to the material Agent. Initial selection and submission each allow one model call; a missing submission stops with an error. Generation stops at the first accepted live test.
The host executes the generated HTTP/SSE/WebSocket plans using its own connections. In the default independent mode, two fresh verification questions follow the accepted probe before formal dispatch.
Formal execution does not depend on a visible or headless browser. Unsupported dependencies are reported instead of silently switching to DOM dispatch.

Use `--demo-question TEXT` to customize the demonstration; copy the full displayed question including its unique marker.
`--conversation-policy independent` starts a fresh conversation per formal trial by default; `continuous` accumulates formal history in a new conversation. Demonstration and verification history are excluded. `--conversation-policy demonstrated` explicitly continues the demonstrated conversation; its probe and formal answers are checked for substantive, on-topic reasonableness with `akasha.ask`, and may depend on demonstration and probe history. This mode does not establish new-conversation support.
`--model` selects the exploration/repair model, default `gemini:gemini-3.8-flash`, separately from the tested website model. Token limits follow the model profile or explicit overrides.
The discovery Agents do not read future dataset questions or expected answers, or rewrite target answers.

Authorized raw recordings and scoped service state can include URLs, headers, Cookie/Authorization, bodies, client scripts, binary material and necessary storage items.
Provider keys, `.env`, full browser profiles and unrelated website storage remain host-owned. Sign-in and MFA/CAPTCHA are manual; DOM password/OTP values are not recorded.
Verbose Agent traces may contain authorized raw material; treat saved logs as private. `--no-verbose` retains necessary user instructions.

Every turn checks source and completion. Without an independent full-answer reference, a temporary browser permits same-turn review: `MATCH` if identical, `MISMATCH` if different, `WAIT` if unfinished.
After three distinct trusted turns qualify the same native JSON answer path, a separate host reader can automatically check formal answers while the path and correlated natural completion remain valid. Other sources may still require manual review.
Review or sign-in browsers close afterwards; the adapter independently sends and collects formal questions.

Private recordings, state, adapter, ledger and verification evidence live in `OUTPUT.work/browser-discovery`.
Reuse with `--adapter PATH/adapter/manifest.json`; normally no browser opens, but fresh verification remains required. Resume with `--resume OUTPUT.work`; submitted or uncertain turns are recovered, never blindly resent.
The old `--probe-question` / `--browser-session-policy` flags and old adapter formats are unsupported.

Set workload budgets with `LLADAR_BROWSER_DISCOVERY_*`, `LLADAR_INDEPENDENT_*`, `LLADAR_PROTOCOL_*` and `LLADAR_MAX_*`.
The two Agents repair failures in alternating material-selection and code/test rounds; changed candidates need a fresh probe. Independent mode then runs two verification questions. Exhausted budgets preserve progress and stop dispatch.
See the [single-demonstration PRD](docs/PRD-browser-single-demonstration.zh-TW.md) and [validation record](docs/browser-single-demonstration-validation.zh-TW.md).
Local real-model success does not establish live ChatGPT, Gemini, EIP or native Streamlit acceptance.

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

## Operation result reports

Each CLI stage command automatically attempts to save a UTF-8 Markdown operation
report after success, failure, cancellation, or a request for human confirmation.
This includes the five main workflows, situation modes, and five Skill-template
commands. Help, version queries, and Python API calls do not create these reports.

For `--output artifacts/evaluation.json`, the report is
`artifacts/evaluation.json.operation.md`. A template at `lladar-skills/eval`
gets a sibling `lladar-skills/eval.operation.md`. Repeated runs use
`.operation-2.md`, `.operation-3.md`, and so on, including with `--force`.
If the output location is unresolved or unwritable, the fallback is
`.lladar/operation-reports/STAGE.operation.md` in the original working directory.

The report follows `--lang` and records status, exit code, timing, permitted
settings, input paths, available counts, artifact locations, sanitized problems,
and next steps. It distinguishes new or modified files from existing files and
includes resume guidance when the failed invocation supplies a work-session hint.
Wrong answers are evaluation findings; execution or judging errors are operation
issues. No extra model call is made, and the original exit code is preserved.

The report location remains visible with `--no-verbose` and is saved in `--log`
when the log is active. These reports omit raw prompts, answers, URLs,
credentials, and arbitrary exception messages. They complement the terminal log,
generation evidence report, and evaluation analysis report. If neither location
can be written, a warning is shown; forced termination cannot guarantee a report.

## Language

Use `--lang CODE` on any workflow or Skill-template command, including `--help`.
The default for new work is English (`en`), including newly generated questions
and explanations. This replaces implicit source-language following.

| Code | Language |
| --- | --- |
| `en` | English |
| `zh-TW` | Traditional Chinese |
| `ja` | Japanese (`jp` / `Jp` are aliases) |
| `ko` | Korean |
| `zh-CN` | Simplified Chinese |

These five languages have interface, fixed report-heading, and offline Skill-template
resources. Other language codes, such as `fr`, are accepted for model-generated
content; the interface, fixed headings, and templates use English with a short
notice. Codes must contain letters and optional hyphen-separated alphanumeric
subtags, such as `en-US`; acceptance does not guarantee that your model supports
the language. Generated prose can still contain source quotes and proper names.

```powershell
lladar --lang ja --help
lladar create test-dataset --knowledge knowledge.md --lang zh-TW --output dataset.jsonl
lladar run-agent dataset.jsonl --project ../my-agent --lang zh-TW --output responses.jsonl
lladar eval responses.jsonl --lang zh-TW --output evaluation.json
lladar report evaluation.json --lang ko --output report.md
lladar create report-skill --lang zh-CN
```

Language selection applies to progress, prompts, summaries, and newly generated
LLaDAR content. Saved test questions, target Agent answers, source quotes, IDs,
schema keys, code, and measurements stay unchanged. It does not ask the target
Agent to switch language or add a language-scoring criterion. Situation-mode
free conversation generation uses the selected language; exact probes and tokens
are preserved. Website answer extraction always preserves the original answer.
Technical diagnostic details and third-party traces may retain their original language.

Next-step commands include `--lang`. A new stage without the option uses `en`;
resume without it restores the session's saved language. A different explicit
language is rejected before further work. Sessions created without language
metadata require their original version or a new work session.

Python stage APIs accept `lang="ja"` (the default `None` resolves to English for
new work and the saved language on resume). Work metadata distinguishes the
requested `lang` from the actual `ui_lang`; generated templates also include
`language.json` with `template_lang`. Localized templates use the same tools and
contracts, retain executable examples, and require no model calls or API key.

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

With `--page-url`, progress shows the current discovery stage, safe tool names and
ledger counts, with periodic waiting messages. Tool arguments/results and model
reasoning stay out of normal logs; `--no-verbose` hides detailed progress.

In a Windows, Linux or macOS interactive terminal with `--verbose` (enabled by default), press
Enter during work to see the current activity, time in that stage and time since
the last progress message. This covers input checks, graph building, adapter
generation/repair, model waits and target subprocess execution. Status goes to
stderr and is also saved by `--log`. Keyboard polling pauses for human input
prompts, including sign-in, choices and YES/MATCH confirmation. It is disabled
with piped stdin, redirected stderr or `--no-verbose`. Linux and macOS retain
normal canonical terminal input and Ctrl+C behavior; text typed ahead is kept
for the next human prompt. Native PTY handoff tests run in the Ubuntu/macOS CI jobs.

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

If browser discovery, consent, or verification stops before dataset
execution, LLaDAR writes only a redacted `<output>.run.json` blocker record; it
does not create responses or trials files.

Existing outputs are protected unless `--force` is specified. See
[the current product contract](docs/PRD-simple-agent-evaluation.md) for the full
behavior and safety boundaries.

## Development

The five target examples have separate uv environments and lockfiles. See the
[example setup guide](example_project/README.md) for installation and certifi security updates.

The GitHub Actions live test reads the repository secret `GEMINI_API_KEY`.
The repository variable `GOOGLE_GENAI_USE_VERTEXAI` selects its backend and defaults
to `true` for Vertex AI Express mode with an API key. Set it to `false` for a
Gemini Developer API key. CI checks the initialized endpoint before making live
calls; local `.env` settings are not uploaded to GitHub.

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
│   ├── browser_target.py       # Discovery, qualification, ledger and formal dispatch
│   ├── browser_discovery_driver.py # Generic browser/network observations and actions
│   ├── browser_agent_loop.py   # Isolated Akasha process and owner-thread tool broker
│   ├── browser_provenance.py   # Host turn/source and full-material validation
│   ├── protocol_runtime.py    # Capability-free generated protocol programme VM
│   ├── protocol_resources.py  # Public protocol documents and pinned schema assets
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
managed services; and the discovery modules above, `response_capture.py`, and
`answer_extraction.py` for browser operation and response extraction.

## Large inputs and model limits

LLaDAR's download and sampling limits are application safeguards, rather than the
language model's context limits. The current GitHub-generation budgets are:

| Budget | Current maximum | Purpose |
| --- | ---: | --- |
| Ordinary text download | 16 MiB per response | Bound source downloads |
| CSV/JSONL streaming download for sampling | 128 MiB per file | Bound downloaded source size |
| Cumulative downloads | 128 MiB | Bound total transfer |
| Sampling scan | 1,000,000 logical rows | Bound scanning work |
| Retained sample | 1,000 rows | Bound subsequent generation work |

A retained sample must also fit the selected-text and model budgets; 1,000 rows
does not guarantee that a sample fits. These limits apply to GitHub generation,
not to every input or browser response in the pipeline.

Model input/output token budgets are separate. Built-in generation assigns
source batches derived from the configured model input budget (one quarter
of the input-token budget in characters). Characters, UTF-8 bytes and tokens are different units.
The model receives selected or batched evidence, rather than an entire downloaded
file in one request.

Increasing `--max-input-tokens` or `--max-output-tokens` does not raise the
download or sampling caps, or extend the provider's actual model capacity.
Download and sampling budgets can be overridden with the `LLADAR_GITHUB_*`
environment variables in `resource_limits.py`; token flags do not replace those
byte/row budgets. When raising budgets, check scanning, storage, batch merging, cross-batch
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

### Workload limits

Model calls share the selected model profile. Browser extraction accepts both
`--max-input-tokens` and `--max-output-tokens`; the default Gemini profile uses
1,048,576 input and 65,536 output tokens. Unknown models use configurable fallback
request budgets of 131,072 input and 65,536 output tokens; these are defaults,
not a guarantee that an unknown provider supports them. Set explicit token flags
for models with a different capacity.

Memory, capture, download and waiting budgets are centralized in
[`resource_limits.py`](src/lladar/resource_limits.py). Every entry can be overridden
before starting LLaDAR with an environment variable named `LLADAR_` plus its key.
Defaults include 16 MiB captured responses, 4 MiB model-input JSON, 4 MiB answer
text, 32 MiB provider envelopes, 65,536 DOM nodes and 4,096 history snapshots per
node. Browser demonstrations have a separate 500 MiB total recording budget
(`LLADAR_MAX_RECORDING_BYTES`). The shared extraction deadline defaults to 3,600
seconds. Overrides become part of the resume configuration, so keep them
unchanged when resuming.

```powershell
$env:LLADAR_MAX_INPUT_BYTES = '8388608'
$env:LLADAR_MAX_CAPTURE_BYTES = '33554432'
$env:LLADAR_EXTRACTION_TIMEOUT_SECONDS = '7200'
lladar run-agent DATASET.jsonl --page-url URL --max-output-tokens 65536
```

Pagination sizes do not truncate the corpus: follow the returned continuation.
Schema validation, secret detection and authorized website-request counts remain
enforced independently of workload budgets.
