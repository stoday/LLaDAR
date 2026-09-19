# CI and real LLM acceptance

The workflow `.github/workflows/release.yml` has three gates:

1. `test`: Python 3.11 and 3.12 run the offline pytest suite on every branch push,
   pull request, version-tag push, and manual workflow run.
2. `live-vibe-testing`: after pytest passes, Python 3.12 runs the real Gemini
   acceptance scripts below. A missing API key, API failure, invalid adapter,
   missing trace, or failed assertion fails the job.
3. `publish`: only a pushed version tag can publish. Both earlier jobs must pass,
   the tagged commit must belong to `origin/main`, and its version must match
   `pyproject.toml`. Existing `vX.Y` and `vX.Y.Z` tag support is unchanged.

Merging a PR into main creates a main push, so both offline and live tests run
again on the merged commit. Updating a branch with an open PR runs both its push
and PR workflows; each eligible live job uses paid API calls.

## Required GitHub configuration

In Settings > Secrets and variables > Actions, create a repository secret named
`GEMINI_API_KEY`. It is injected only into the credential check and live test
steps; no credential file is committed or included in artifacts. The controller
also receives the same value as `GOOGLE_API_KEY` for provider compatibility.

Optionally set repository variable `LLADAR_LIVE_MODEL` to a Gemini model ID
without the `gemini:` prefix. The default is `gemini-3-flash-preview`. Both the
coding agent and target fixture use that model.

Fork pull requests and Dependabot events do not receive repository secrets.
Their live job is explicitly skipped; their pytest checks still run. A skipped
live job is not evidence of real-model acceptance. After review, merging into
main runs live acceptance. Do not use `pull_request_target` to run untrusted PR
code with a secret.

## What live acceptance checks

Both scripts first import the required LangChain/Gemini APIs in the actual target
Python and check Node availability. Discovery receives these measured runtime
facts, so it need not infer installed API availability from model knowledge.

- `scripts/verify_rest_graph_live.py`: real Graphify AST extraction of Python,
  JavaScript and TypeScript inputs; LLM interface discovery and adapter creation;
  the original Node HTTP API; real LangChain/Gemini/tool calls; two independent
  adapter replays and three dataset sessions; final response formatting;
  service shutdown; and unchanged fixture source. If discovery asks for confirmation,
  the harness selects only the unique complete public `POST /api/chat` contract
  with the expected server and health route; missing or ambiguous contracts fail.
- `scripts/verify_interface_confirmation_live.py`: LLM discovery of two public
  features; pause before calling the target; select the chat feature; resume in
  a separate process; two independent replays and three dataset sessions with
  complete initialization, knowledge, tool, model and postprocessing traces.

The controller, target Python and Graphify use separate environments. Node is
installed explicitly. The live job has a 30-minute time limit. Acceptance
summaries (`verification.json`) are uploaded as `live-vibe-testing-summary` for
seven days. Raw workspaces, provider logs and environment files are not uploaded.

These are real execution/integration checks using a fixed dataset. They do not
measure dataset-generation quality or run the full answer-quality evaluation.
`verified` means an adapter can be replayed, not that every answer is correct.

## Local replay

Install LLaDAR in the controller environment, Node, an independent Graphify tool
(`uv tool install graphifyy==0.9.61`), and a separate target Python environment
with `langchain>=1,<2` and `langchain-google-genai>=4,<5`.

Run either script with `--target-python PATH_TO_TARGET_PYTHON`. Credentials can
come from the process environment, or from an explicit `--env-file PATH`.
Use `--model gemini:MODEL_ID` for the controller and `EXAMPLE_MODEL=MODEL_ID` for
the target when overriding the default locally. These commands call real APIs.

For WSL validation, keep the checkout and run artifacts on the native Linux
filesystem, as GitHub Ubuntu runners do. A concurrent fixture trace-write replay
on a Windows-mounted /mnt/c directory produced truncated JSONL; the same replay
on native /tmp preserved all 640 records. Keep the strict trace assertions.

## Local validation of this workflow

On 2026-09-19, Linux Python 3.12.13 / Node 22.20.0 / Graphify 0.9.61 passed
both real Gemini acceptance scripts. Each verified two independent replays and
three dataset sessions with complete execution traces. The offline suite passed
178 tests. Actionlint, shell syntax and the missing-secret failure check passed.
The local consolidated evidence is .lladar/ci-live-verification.json (ignored
runtime output). These results do not claim a GitHub Actions run; the workflow
still needs to be committed/pushed and GEMINI_API_KEY configured on GitHub.
