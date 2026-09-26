# LLaDAR

[English](README.md) | [繁體中文](README.zh-TW.md)

LLaDAR is a small Agent-evaluation pipeline with four workflows:

1. Generate questions and expected answers from knowledge.
2. Run a target Agent and capture its actual responses.
3. Let an evaluator Agent choose or follow an evaluation method, then compute
   statistics deterministically in Python.
4. Render an evidence-bounded Markdown report.

## Installation

```bash
python -m pip install lladar
```

Python 3.11 and 3.12 are supported. The default model provider reads its
credentials from `.env` or the process environment.

## Record format

Every JSONL line has exactly three fields:

```json
{"question":"What is Taiwan's capital?","expected_answer":"Taipei","actual_response":null}
```

`question` and `expected_answer` are non-empty strings. `actual_response` is a
string or `null`. There are no schema versions and unknown fields are rejected.

## Quick start

Generate a dataset. `--output` accepts an explicit `.jsonl` path (as below),
or a directory in which the CLI creates a timestamped dataset.

```bash
lladar create test-dataset --knowledge ./knowledge --output dataset.jsonl
```

The default method is the bundled Akasha `knowledge-point-qa` skill: it extracts
knowledge points, then generates one QA per point. It is included in pip installs
and writes `dataset.jsonl.generation.json` with evidence and processing status.

This requires Akasha 1.8 or later. Use `--skill DIRECTORY` to override the
default with one trusted local skill; there is no extra skill manifest or
installation command. The bundled method is not a removable user-installed skill.
See [skill generation](docs/skill-generation.md) for limits and Python usage.

Migration: dataset creation now uses a skill only, so `create test-dataset` no
longer accepts `--method`, `--chunk-size`, `--overlap`, `--strict`, `--prompt`, or
`--prompt-file`. Python `create_test_dataset()` no longer accepts the corresponding
legacy parameters or `provider`. Every stage now uses a bundled or local skill;
`run-agent` and `eval` no longer accept prompt guidance.

Run the target Agent. LLaDAR inspects a copied project and creates a temporary
adapter for its real public workflow:

```bash
lladar run-agent dataset.jsonl --project ../my-agent --output responses.jsonl
```

Which target option should you use?

- `--project PATH`: inspect the project's source and documentation to learn its public interface. A project alone can be enough: when startup details and the required runtime configuration are available, LLaDAR can start the service in an isolated project copy, test it, and stop only the service it started.
- `--project PATH --service-url URL`: inspect the same project, but use an already running test service. The URL supplies its **base address**, not its startup command or API contract. LLaDAR still learns the HTTP method, route, payload, authentication requirements, and response format from the project; it does not start or stop that existing service.
- `--page-url URL`: use a normal question webpage without providing project source. Sign in and submit a calibration question in LLaDAR's browser; LLaDAR records the request for replay and uses the model to extract answers from the captured responses.

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
Browser mode now has one answer-extraction path: a tool-free model reads the
approved complete response and reconstructs its original answer. No generated
Python, built-in answer rules, Monty, parser cache, or parser-policy option remains.
The browser-only default is `gemini:gemini-3.8-flash`; override it with
`--model gemini:MODEL`. Model availability depends on your provider account.
Project discovery and evaluation defaults are unchanged.

LLaDAR opens its own visible Chromium profile. Sign in yourself, submit the exact
calibration question, and wait until the answer is complete before pressing Enter.
Review the website requests, model destination and budgets together. One `YES`
approves both one verification question plus the displayed dataset trials AND
sending **real response content, including internal answers**, to
`https://generativelanguage.googleapis.com`. Ensure this is permitted by your
organization. `--confirm-browser-run` and
`--allow-response-model-transfer` approve only their respective scopes for this run.
Either flag alone still leaves the other scope unapproved; both skip the approval
prompt, not review. Old website-only or synthetic-evidence consent is not sufficient.

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
Old parser flags are rejected, and old private caches are left untouched but unused.

Evaluate automatically:

```bash
lladar eval responses.jsonl --output evaluation.json
```

Use `--skill ./skills/my-verdict` to supply a local evaluation method. Eval reads
the trials sidecar when present and Python calculates per-record stability.

Render the report:

```bash
lladar report evaluation.json --output report.md
```

Use `--skill ./skills/my-report` to supply a local evidence-summary method.

`eval` asks one Agent to freeze a set of boolean, categorical, or numeric
dimensions and judge each completed record. Counts, rates, distributions,
means, medians, minima, and maxima are calculated by Python. `report` renders
those saved facts and includes a record-level appendix.

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

## Outputs

- `create test-dataset`: three-field JSONL with `actual_response: null`; skill mode also writes `<output>.generation.json`
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
