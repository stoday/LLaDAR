# LLaDAR test-dataset configuration

## Status

Implemented as schema version 2 on 2026-09-11. This document supersedes the
former schema-v1 config contract.

## Purpose

`lladar create test-dataset` needs a short, reproducible project configuration
without making users repeat model, chunking, policy, and output options. The
configuration must remain non-secret, deterministic, and subordinate to
explicit CLI overrides.

This configuration applies only to schema-v2 test-dataset generation. It does
not configure `lladar run-agent` or `lladar eval`.

## Commands

Generate a template:

```powershell
lladar create config
lladar create config --output .\config\lladar.toml
```

The default destination is `config.toml`. Existing files are refused unless
the user explicitly supplies `--force`.

Use a config:

```powershell
lladar create test-dataset --config .\config.toml
```

`knowledge` may come from the config, so `--knowledge` is not an unconditional
argparse requirement. The merged settings must still contain at least one
knowledge path before provider work.

## Template

The generated UTF-8 TOML template is:

```toml
schema_version = 2

[test_dataset]
# Replace this example with one or more .txt/.md files or directories.
knowledge = ["./knowledge"]
count = 0

# Optional domain and question-style guidance. Set at most one.
# prompt = "Prefer concise questions for restaurant recommendations."
# prompt_file = "./dataset-prompt.md"

# Optional selection and generation settings.
# chunk_size = "auto"
# overlap = 0.1
# seed = 1234
# policies = ["builtin:general-social-context"]
# model = "gemini:gemini-3.7-flash"

# Optional model-profile overrides.
# max_input_tokens = 1048576
# max_output_tokens = 65536
# auto_window_ratio = 0.8

# Optional paths and runtime behavior.
# output = "./test-dataset.jsonl"
# env_file = ".env"
# verbose = true
# trace = false
# trace_console = false
# trace_root = ".lladar/runs"
# strict = false
# cache = false
# cache_dir = ".lladar/cache"
# refresh_cache = false
```

Only `knowledge` and `count` are active. Commented values continue to follow
package defaults until the user deliberately pins them.

## Supported keys

| TOML key | Accepted type | CLI option |
| --- | --- | --- |
| `knowledge` | non-empty array of strings | `--knowledge` |
| `count` | non-negative integer; `0` means all candidate chunks | `--count` |
| `seed` | integer | `--seed` |
| `policies` | non-empty array of strings | repeatable `--policy` |
| `prompt` | non-empty string | `--prompt` |
| `prompt_file` | non-empty path string | `--prompt-file` |
| `chunk_size` | positive integer or `"auto"` | `--chunk-size` |
| `overlap` | number satisfying `0 <= value < 1` | `--overlap` |
| `model` | non-empty string | `--model` |
| `max_input_tokens` | positive integer | `--max-input-tokens` |
| `max_output_tokens` | positive integer | `--max-output-tokens` |
| `auto_window_ratio` | number satisfying `0 < value <= 1` | `--auto-window-ratio` |
| `verbose` | boolean | `--verbose` / `--no-verbose` |
| `trace` | boolean | `--trace` / `--no-trace` |
| `trace_console` | boolean | `--trace-console` / `--no-trace-console` |
| `trace_root` | non-empty path string | `--trace-root` |
| `output` | path string | `--output` |
| `env_file` | path string | `--env-file` |
| `strict` | boolean | `--strict` / `--no-strict` |
| `cache` | boolean | `--cache` / `--no-cache` |
| `cache_dir` | path string | `--cache-dir` |
| `refresh_cache` | boolean | `--refresh-cache` / `--no-refresh-cache` |

`num_pairs`, `random_select`, and all unknown keys are errors. Config schema
version 1 has no compatibility path and fails with an instruction to regenerate
the file.

## Policy selection

`policies` is an exact ordered list. When omitted, LLaDAR uses only
`builtin:general-social-context`. When present, no built-in is silently added.

```toml
policies = [
  "builtin:general-social-context",
  "./policies/food-recommendation.toml",
]
```

Local policy paths resolve relative to the config directory. Remote URLs,
includes, inheritance, environment interpolation, and executable policy code
are unsupported.

## Precedence and overrides

Settings merge in this order:

```text
built-in defaults < config.toml < explicit CLI options
```

Argparse defaults do not overwrite config values. Both positive and negative
boolean forms exist so users can explicitly override saved booleans.

`prompt` and `prompt_file` form one logical selector. Supplying either on the
CLI replaces either form in the config. A single source cannot specify both.

`--policy` is repeatable and the complete CLI list replaces the complete config
list. It does not append to it.

When an explicit CLI value differs from a config value, LLaDAR emits one stderr
warning before provider work. It lists only setting names; it never prints old
or new policy paths, prompt content, environment-file details, or secrets.
Equal effective values do not warn.

## Path rules

These config values resolve from the directory containing the config file:

- `knowledge`;
- `prompt_file`;
- local entries in `policies`;
- `output`;
- `env_file`; and
- `cache_dir`; and
- `trace_root`.

Explicit CLI paths resolve from the current working directory. If `output` is
absent from both sources, the collision-safe timestamped file is created in the
current working directory.

## Validation and safety

The complete merged input is checked before provider execution. Errors include:

- missing, unreadable, invalid-UTF-8, or invalid-TOML config files;
- missing or unsupported `schema_version`;
- missing `[test_dataset]`;
- unknown keys or tables;
- invalid types and ranges;
- missing effective knowledge;
- conflicting prompt selectors;
- missing knowledge or prompt files;
- invalid or missing local policy files; and
- protected output collisions.

The template contains no credential fields. Parsing does not execute code,
expand shell expressions, interpolate environment variables, or fetch remote
content. Provider secrets stay in the process environment or referenced `.env`
file.

`trace_console = true` requires `trace = true`. Trace artifacts preserve full
prompts and raw provider responses and may therefore contain complete knowledge
content. They are opt-in, default below the Git-ignored `.lladar/runs/` path,
and must be treated as sensitive diagnostic data.

## Acceptance criteria

1. `lladar create config` writes the documented schema-v2 template.
2. Existing template destinations require explicit `--force`.
3. Config-only generation works when `knowledge` is present.
4. CLI values override config values; config values override defaults.
5. Config and CLI path bases follow the documented rules.
6. Policy lists are exact, ordered, and secret-safe in warnings.
7. Invalid configs and policies fail before provider execution.
8. Schema-v1 configs and removed keys fail clearly.
9. Config-free schema-v2 generation remains supported.
10. Verbose output shows final safe settings and the config path.
11. `count = 0` is accepted and means no ready-group limit; negative values
    fail validation.

## Out of scope

- automatic config discovery;
- named profiles or overlays;
- includes, inheritance, or environment interpolation;
- remote policy/config loading;
- provider credentials in TOML;
- configuration for Agent execution or response evaluation; and
- migration of schema-v1 configs or datasets.
