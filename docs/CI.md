# CI and real LLM acceptance

The workflow `.github/workflows/release.yml` has three gates:

1. `test`: Python 3.11 and 3.12 run the offline pytest suite on every branch
   push, pull request, and manual workflow run. Version-tag pushes skip this job.
2. `live-vibe-testing`: after pytest passes, Python 3.12 runs the real Gemini
   acceptance scripts on same-repository PRs, `main` pushes, and manual runs.
   Other branch pushes and tags skip paid API calls. A missing API key, API
   failure, invalid adapter, missing trace, or failed assertion fails the job.
3. `publish`: only a pushed version tag can publish. The tag must point to the
   current `origin/main` commit, have a matching `pyproject.toml` version, and
   have a completed, successful `main` push run of this workflow for the exact
   commit. If that run is not visible yet or is still queued/in progress, the
   release gate polls every 15 seconds for up to 35 minutes. A completed failed
   run still fails immediately. The release check also requires successful
   Python 3.11, Python 3.12, and real Gemini jobs in that run; a skipped live job
   cannot authorize release. Existing `vX.Y` and `vX.Y.Z` tag support is unchanged.

Merging a PR into main creates a main push, so both offline and live tests run
again on the merged commit. Updating a branch with an open PR runs pytest on
both its push and PR workflows, but only the PR runs the paid live check.
You may push a version tag while the matching `main` workflow is still running;
the publish job waits for it. Waiting is bounded, so a stuck or unusually delayed
run still fails after 35 minutes and can be rerun later. The tag does not repeat
pytest or the paid Gemini check.

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

The live script first imports the required LangChain/Gemini APIs in the actual
target Python and checks Node availability. This is a fail-fast runtime preflight;
interface discovery itself uses the checked-out project evidence.

- `scripts/verify_rest_graph_live.py`: real Graphify AST extraction of Python,
  JavaScript and TypeScript inputs; LLM interface discovery and adapter creation;
  the original Node HTTP API; real LangChain/Gemini/tool calls; one independent
  verification request and three trials of the selected dataset record; final
  response formatting; service shutdown; and unchanged fixture source. The
  live harness accepts discovery only when exactly one candidate matches the
  fixture's complete public `node server.js` / `POST /api/chat` / `/health`
  contract. Missing, incomplete or ambiguous matching contracts fail closed;
  unrelated discovery uncertainty cannot silently redirect the acceptance target.

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

Run the script with `--target-python PATH_TO_TARGET_PYTHON`. Credentials can
come from the process environment, or from an explicit `--env-file PATH`.
Use `--model gemini:MODEL_ID` for the controller and `EXAMPLE_MODEL=MODEL_ID` for
the target when overriding the default locally. These commands call real APIs.

```bash
python scripts/verify_rest_graph_live.py \
  --target-python PATH_TO_TARGET_PYTHON \
  --model gemini:MODEL_ID
```

For WSL validation, keep the checkout and run artifacts on the native Linux
filesystem, as GitHub Ubuntu runners do. A concurrent fixture trace-write replay
on a Windows-mounted /mnt/c directory produced truncated JSONL; the same replay
on native /tmp preserved all 640 records. Keep the strict trace assertions.

## Local validation of this workflow

The offline suite checks that every `python scripts/*.py` workflow target exists,
that the live entrypoint can load and show `--help`, and that its runner call uses
the current public arguments and expected observation counts. Run it with
`python -m pytest` before pushing. These checks do not call Gemini and do not
replace the live job: only a successful GitHub `live-vibe-testing` job verifies
the configured secret, current model and real external execution.
