@echo off
setlocal

cd /d "%~dp0"

echo Creating a schema-v2 LLaDAR test dataset...
echo Note: choose a new output path or remove the prior artifact manually.

uv run lladar create test-dataset ^
  --model "gemini:gemini-3.1-pro-preview" ^
  --env-file "..\.env" ^
  --knowledge ".\DIET-v1.md" ^
  --count 3 ^
  --seed 1234 ^
  --prompt-file ".\prompt.md" ^
  --output ".\test-dataset.jsonl" ^
  --chunk-size auto
if errorlevel 1 exit /b 1

echo.
echo Dataset generation completed: .\test-dataset.jsonl
echo.
echo Running every schema-v2 original and variant against the Agent...

uv run lladar run-agent ".\test-dataset.jsonl" ^
  --project "." ^
  --env-file "..\.env" ^
  --output ".\qa-results.jsonl"
if errorlevel 1 exit /b 1

echo.
echo Evaluating LLaDAR BFS and diagnostics...

uv run lladar eval ".\test-dataset.jsonl" ".\qa-results.jsonl" ^
  --env-file "..\.env" ^
  --output ".\reports\evaluation.json"
if errorlevel 1 exit /b 1

echo.
echo Evaluation completed: .\reports\evaluation.json

exit /b 0
