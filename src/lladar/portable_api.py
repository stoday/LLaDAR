"""Export a verified project-derived HTTP client without project/runtime state."""
from __future__ import annotations

import ast
from pathlib import Path
import re
import sys


PORTABLE_PROMPT = """
PORTABLE API EXPORT CONTRACT (overrides project-runtime instructions):
This adapter will be shared with another person WITHOUT the project or LLaDAR.
Only an existing public HTTP service is supported. Never start a service, import
project code, read project files or depend on a project working directory.
Use Python standard library plus `from lladar_api_runtime import request`.
Send ALL HTTP requests through request(relative_path, method=..., data=bytes,
headers=dict, timeout=seconds); it returns a urllib response context manager and
uses the recipient's LLADAR_SERVICE_URL. Never embed a base URL or use another
HTTP client. Parse the actual response/completion contract without rewriting it.
Preserve the project's authentication method (Bearer/API key/custom headers/cookie,
or documented credential exchange). Read credential VALUES from named environment
variables at execution. Never embed credentials, browser profiles or sessions.
Declare REQUIRED_ENV = ["NAME", ...] as a literal list of every required credential
environment variable, or [] for a confirmed unauthenticated service. Use the
project's existing names where possible. Optional public configuration may use
environment variables with safe defaults. Do not treat an unknown auth method as
unauthenticated; report blockers if it cannot be reproduced independently.
LLaDAR folds the HTTP helper and CLI into a single standalone adapter.py on export;
no helper file, launcher file, manifest or requirements file is distributed.
Reserve identifiers starting with _lladar_ for the inlined runtime and CLI.
The CLI checks REQUIRED_ENV before executing. It supplies the same JSON
stdin/stdout protocol. It uses isolated Python without site packages, an empty
working directory and no project files, including during verification.
Secrets and raw HTTP errors/bodies must never appear in diagnostics. Use the
runtime request helper for 401/403 and redirect handling. No automatic retries of
submitted questions. Return the normal harness/explanation/blockers proposal.
"""

LAUNCHER = '''
def _lladar_deadline():
    import os
    import sys
    import threading
    seconds = float(os.environ.get("LLADAR_API_TIMEOUT", "60"))
    if not 0 < seconds < float("inf"):
        raise ValueError("API timeout must be finite and positive")
    output = sys.stderr
    def expired():
        output.write("API adapter timed out\\n")
        output.flush()
        os._exit(1)
    timer = threading.Timer(seconds, expired)
    timer.daemon = True
    timer.start()
    return timer

def _lladar_prepare(required):
    import argparse
    import io
    import json
    import os
    import sys
    import uuid
    from contextlib import redirect_stdout, redirect_stderr
    sys.dont_write_bytecode = True
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Standalone API adapter (Python 3.11+, standard library only)")
    parser.add_argument("--service-url", help="API base URL (or LLADAR_SERVICE_URL)")
    parser.add_argument("--message", help="Question; omit to read the adapter JSON protocol from stdin")
    args = parser.parse_args()
    if args.service_url:
        os.environ["LLADAR_SERVICE_URL"] = args.service_url
    _lladar_http_service_url()
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        print("Missing required environment variables: " + ", ".join(missing), file=sys.stderr)
        raise SystemExit(1)
    incoming = ({"request_id": uuid.uuid4().hex, "message": args.message}
                if args.message is not None else json.load(sys.stdin))
    if (not isinstance(incoming, dict) or not isinstance(incoming.get("request_id"), str)
            or not isinstance(incoming.get("message"), str)):
        raise ValueError("Invalid adapter input")
    sys.stdin = io.StringIO(json.dumps(incoming))
    sys.argv = [__file__]
    captured = io.StringIO()
    return incoming, captured, redirect_stdout(captured), redirect_stderr(io.StringIO())

def _lladar_finish(incoming, captured):
    import json
    result = json.loads(captured.getvalue())
    if (not isinstance(result, dict) or result.get("request_id") != incoming["request_id"]
            or not isinstance(result.get("output"), str) or not result["output"].strip()
            or not isinstance(result.get("observation"), str) or not result["observation"].strip()):
        raise ValueError("Invalid adapter output")
    print(json.dumps(result, ensure_ascii=False))

def _lladar_fail(error):
    import sys
    # Never echo arbitrary generated exceptions or credential-bearing bodies.
    message = str(error)
    safe = {"Set LLADAR_SERVICE_URL to an HTTP(S) base URL without credentials, query or fragment",
            "API connection failed; check service address and network access",
            "API HTTP 401: check your credentials and server permissions",
            "API HTTP 403: check your credentials and server permissions"}
    safe.update("API HTTP " + str(code) for code in range(100, 600))
    print(message if message in safe else "API adapter failed; check configuration and service contract", file=sys.stderr)
'''

README = r"""# Portable API adapter

## Runtime and files

- Python 3.11 or newer, with its standard library and HTTPS/TLS support.
- Windows, Linux or macOS: use the matching commands below. Verification during
  generation runs on the creator's system; it is not certification on every OS.
- Network access to the API and your own authorized API credentials.
- No original project, LLaDAR installation, or third-party Python packages needed.

The delivery contains adapter.py, this README.md and .env.example. The Python file includes
HTTP handling, configuration checks and the CLI; it can run by itself without
this README. Required credential names are declared in REQUIRED_ENV in adapter.py.
There are no third-party dependencies, so no requirements.txt or pip install step
is needed. The adapter is verified with `-I -S`, which excludes installed site packages.
Installing a missing third-party package will not make it available in this mode;
an adapter requiring one needs to be regenerated or its runtime contract revised.

Use a Python installation for the recipient's OS. Do not copy the creator's .venv
between computers or operating systems. Creating a local .venv is optional but
recommended for keeping this client's interpreter separate. No activation is
needed with the explicit interpreter paths below. Run commands from this bundle's
directory; paths containing spaces should be quoted.

## Windows (PowerShell)

Install Python 3.11+ first. These commands use the Python launcher (`py`) to select
Python 3.11; if it is unavailable, use the path to your installed Python 3.11+.

```powershell
Set-Location 'C:\path\to\api-client'
py -3.11 --version
py -3.11 -m venv .venv
$env:LLADAR_SERVICE_URL = 'https://api.example.com'
{windows_credentials}
.\.venv\Scripts\python.exe adapter.py --message 'Your question'
```

## Linux and macOS (bash/zsh)

Install Python 3.11+ first. Check `python3 --version`; if it is older, replace
`python3` below with the executable for your Python 3.11+ installation.

```sh
cd '/path/to/api-client'
python3 --version
python3 -m venv .venv
export LLADAR_SERVICE_URL='https://api.example.com'
{posix_credentials}
./.venv/bin/python adapter.py --message 'Your question'
```

If `venv` is unavailable on Linux, install your distribution's venv support for
the selected Python version, or skip the venv step and run your Python 3.11+
directly with `adapter.py`. On Windows the equivalent direct command is
`py -3.11 adapter.py` after setting the environment variables.
You can optionally add `-I -S` before adapter.py to use the same Python isolation
flags as generation-time verification.

## Service address and authentication

Replace the example address and credential placeholders before running.
Required credential environment variables for THIS adapter: {required_names}.
Their names also appear in adapter.py under REQUIRED_ENV. Authentication is
derived from the inspected project; use credentials issued to your own API account.
Credential values, private .env files and the creator's login state are not included. The adapter
reads the process environment; it does not automatically load .env files. Missing
required variables stop execution before an API request is sent. Keep credential
values out of shared scripts, committed files and shell history.

`.env.example` lists the service address, optional timeout and this adapter's
required credential variables with comments. Credential entries are deliberately
empty. Use it as a checklist and set the corresponding process environment
variables using the commands above. Copying it to `.env` alone does not load it;
use your own environment loader if you maintain a local `.env` file.

The API server enforces account, role, tenant and resource permissions; sharing
this client grants no permissions. 401/403 means the server rejected authentication
or authorization. Browser login/MFA is not automatically transferred.

For a remote host replace localhost/127.0.0.1 with its reachable address; configure
the server's listener and network access separately. Use HTTPS when carrying
credentials remotely. Redirects are rejected; provide the final API base URL.
`--service-url URL` overrides LLADAR_SERVICE_URL without modifying adapter.py.
LLADAR_API_TIMEOUT sets the total question deadline and default HTTP timeout in seconds (default 60):

```powershell
$env:LLADAR_API_TIMEOUT = '120'
```

```sh
export LLADAR_API_TIMEOUT='120'
```

## Programmatic input and output

Omit --message to send one JSON object on stdin (UTF-8):

```json
{"request_id":"q1","message":"Your question"}
```

stdout returns one JSON object containing the same request_id, the final answer
string in output, and an observation string. Diagnostics go to stderr; failure
returns a nonzero exit code. No automatic retry of submitted questions is provided.
The adapter is generated executable code; review adapter.py before running it.
"""


def bundle_readme(required: list[str]) -> str:
    """Describe this adapter's actual credential names without including values."""
    windows = "\n".join(f"$env:{name} = 'REPLACE_WITH_YOUR_CREDENTIAL'" for name in required)
    posix = "\n".join(f"export {name}='REPLACE_WITH_YOUR_CREDENTIAL'" for name in required)
    no_credentials = "# No credential environment variables are required by this adapter."
    return (README.replace("{windows_credentials}", windows or no_credentials)
            .replace("{posix_credentials}", posix or no_credentials)
            .replace("{required_names}", ", ".join(f"`{name}`" for name in required) or "none"))


def bundle_environment(required: list[str]) -> str:
    """Document public configuration and credential names, never their values."""
    lines = [
        "# Configuration checklist; this file is NOT loaded automatically.",
        "# Set these variables in the process environment before running adapter.py.",
        "# Required: reachable HTTP(S) API base URL; use HTTPS for remote credentials.",
        "# --service-url overrides LLADAR_SERVICE_URL. Do not put credentials in the URL.",
        "LLADAR_SERVICE_URL=https://api.example.com", "",
        "# Optional: positive total question deadline and default HTTP timeout in seconds (default 60).",
        "# An explicit per-request timeout cannot extend the total question deadline.",
        "LLADAR_API_TIMEOUT=60", "",
    ]
    for name in required:
        if name in {"LLADAR_SERVICE_URL", "LLADAR_API_TIMEOUT"}:
            continue
        lines += [f"# Required: {name}; supply your own credential for the inspected API authentication method.",
                  "# Kept empty intentionally; never commit or share a real credential value.",
                  f"{name}=", ""]
    if not required:
        lines += ["# No credential environment variables are required by this adapter.", ""]
    return "\n".join(lines)


def required_environment(source: bytes) -> list[str]:
    tree = ast.parse(source)
    required = None
    allowed = sys.stdlib_module_names | {"lladar_api_runtime"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [alias.name.split(".")[0] for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [(node.module or "").split(".")[0]] if not node.level else [""]
        else:
            names = []
        if any(name not in allowed for name in names):
            raise ValueError("Portable API adapters may import only stdlib and lladar_api_runtime")
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "REQUIRED_ENV" for t in node.targets):
            required = ast.literal_eval(node.value)
    if (not isinstance(required, list) or not all(isinstance(name, str)
            and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) for name in required)):
        raise ValueError("Portable adapter must declare REQUIRED_ENV as a literal list of environment variable names")
    return sorted(set(required))


STANDALONE_HEADER = '"""Standalone LLaDAR API adapter; Python 3.11+; standard library only."""\n'


class _RuntimeNames(ast.NodeTransformer):
    """Keep the inlined HTTP implementation separate from generated globals."""

    def __init__(self, tree: ast.Module):
        names = {node.name for node in tree.body if isinstance(node, (ast.FunctionDef, ast.ClassDef))}
        for node in tree.body:
            if isinstance(node, (ast.Import, ast.ImportFrom)) and getattr(node, "module", None) != "__future__":
                names.update(alias.asname or alias.name.split(".")[0] for alias in node.names)
        self.names = {name: "_lladar_http_" + name for name in names}

    def visit_Name(self, node):
        node.id = self.names.get(node.id, node.id)
        return node

    def visit_FunctionDef(self, node):
        node.name = self.names.get(node.name, node.name)
        return self.generic_visit(node)

    visit_ClassDef = visit_FunctionDef

    def visit_Import(self, node):
        for alias in node.names:
            alias.asname = self.names[alias.asname or alias.name.split(".")[0]]
        return node

    def visit_ImportFrom(self, node):
        if node.module != "__future__":
            for alias in node.names:
                alias.asname = self.names[alias.asname or alias.name]
        return node


class _InlineImports(ast.NodeTransformer):
    def __init__(self):
        self.future = {"annotations"}

    def visit_ImportFrom(self, node):
        if node.module == "__future__":
            self.future.update(alias.name for alias in node.names)
            return None
        if node.module != "lladar_api_runtime":
            return node
        assignments = []
        for alias in node.names:
            if alias.name not in {"request", "service_url"}:
                raise ValueError("Portable API helper exposes only request and service_url")
            assignments.append(ast.Assign(targets=[ast.Name(id=alias.asname or alias.name, ctx=ast.Store())],
                value=ast.Name(id="_lladar_http_" + alias.name, ctx=ast.Load())))
        return assignments

    def visit_Import(self, node):
        ordinary = [alias for alias in node.names if alias.name != "lladar_api_runtime"]
        assignments = [ast.Assign(targets=[ast.Name(id=alias.asname or alias.name, ctx=ast.Store())],
                                 value=ast.Name(id="_lladar_api", ctx=ast.Load()))
                       for alias in node.names if alias.name == "lladar_api_runtime"]
        return ([ast.Import(names=ordinary)] if ordinary else []) + assignments


def _upgrade_standalone_deadline(source: bytes) -> bytes:
    """Add the current deadline to older exported CLIs, preserving input files."""
    tree = ast.parse(source)
    if any(isinstance(node, ast.FunctionDef) and node.name == "_lladar_deadline" for node in tree.body):
        return source
    main_test = ast.dump(ast.parse('__name__ == "__main__"', mode="eval").body)
    main = next((node for node in tree.body if isinstance(node, ast.If) and ast.dump(node.test) == main_test), None)
    if main is None or not main.body or not isinstance(main.body[0], ast.Try):
        raise ValueError("Standalone adapter must use the exported CLI structure")
    outer = main.body[0]
    prepare = outer.body[0] if outer.body else None
    if (not isinstance(prepare, ast.Assign) or not isinstance(prepare.value, ast.Call)
            or not isinstance(prepare.value.func, ast.Name) or prepare.value.func.id != "_lladar_prepare"):
        raise ValueError("Standalone adapter must use the exported CLI structure")
    tree.body.insert(tree.body.index(main), ast.parse(LAUNCHER).body[0])
    outer.body.insert(1, ast.parse("_lladar_timer = _lladar_deadline()").body[0])
    outer.finalbody.extend(ast.parse('if "_lladar_timer" in locals():\n    _lladar_timer.cancel()').body)
    result = (ast.unparse(ast.fix_missing_locations(tree)) + "\n").encode("utf-8")
    compile(result, "adapter.py", "exec")
    return result


def standalone_source(source: bytes) -> bytes:
    """Flatten verified code and runtime into readable Python, without exec or sidecar files."""
    required = required_environment(source)
    if source.decode("utf-8-sig").startswith(STANDALONE_HEADER):
        return _upgrade_standalone_deadline(source)
    generated = ast.parse(source)
    for node in ast.walk(generated):
        name = node.id if isinstance(node, ast.Name) else (getattr(node, "asname", None) or getattr(node, "name", ""))
        if isinstance(name, str) and name.startswith("_lladar_"):
            raise ValueError("Generated adapters must reserve the _lladar_ name prefix for the CLI")
    imports = _InlineImports()
    generated = imports.visit(generated)
    runtime = ast.parse(Path(__file__).with_name("api_runtime.py").read_bytes())
    runtime = _RuntimeNames(runtime).visit(runtime)
    runtime.body = [node for node in runtime.body if not (isinstance(node, ast.ImportFrom) and node.module == "__future__")]
    wrapper = ast.parse('''
if __name__ == "__main__":
    try:
        _lladar_incoming, _lladar_captured, _lladar_stdout, _lladar_stderr = _lladar_prepare(REQUIRED_ENV)
        _lladar_timer = _lladar_deadline()
        with _lladar_stdout, _lladar_stderr:
            try:
                pass
            except SystemExit as _lladar_exit:
                if _lladar_exit.code not in (None, 0):
                    raise RuntimeError("Adapter exited unsuccessfully") from None
        _lladar_finish(_lladar_incoming, _lladar_captured)
    except Exception as _lladar_error:
        _lladar_fail(_lladar_error)
        raise SystemExit(1)
    finally:
        if "_lladar_timer" in locals():
            _lladar_timer.cancel()
''')
    # if -> outer try -> with -> generated try. These blocks keep module scope,
    # so generated functions retain their original global-variable semantics.
    wrapper.body[0].body[0].body[2].body[0].body = generated.body or [ast.Pass()]
    header = ast.parse(STANDALONE_HEADER + "from __future__ import " + ", ".join(sorted(imports.future))
                       + "\nREQUIRED_ENV = " + repr(required))
    namespace = ast.parse('''
from types import SimpleNamespace as _lladar_namespace
_lladar_api = _lladar_namespace(request=_lladar_http_request, service_url=_lladar_http_service_url)
''')
    tree = ast.Module(body=header.body + runtime.body + namespace.body + ast.parse(LAUNCHER).body + wrapper.body,
                      type_ignores=[])
    rendered = (ast.unparse(ast.fix_missing_locations(tree)) + "\n").encode("utf-8")
    compile(rendered, "adapter.py", "exec")
    return rendered


def write_bundle(destination: Path, source: bytes, *, resume: bool = False,
                 lang: str | None = None, model: str | None = None,
                 env_file: str | Path | None = None,
                 documentation_cache: Path | None = None,
                 max_input_tokens: int | None = None,
                 max_output_tokens: int | None = None) -> None:
    """Write only allowlisted code/config; never copy project/evidence/environment."""
    required = required_environment(source)
    if destination.exists() and not resume:
        raise FileExistsError(destination)
    standalone = standalone_source(source)
    from .adapter_documentation import localize_documents
    documents = localize_documents(
        {"README.md": bundle_readme(required), ".env.example": bundle_environment(required)},
        lang=lang, model=model, env_file=env_file, cache=documentation_cache,
        max_input_tokens=max_input_tokens, max_output_tokens=max_output_tokens)
    files = {"adapter.py": standalone,
             **{name: content.encode("utf-8") for name, content in documents.items()}}
    if resume and destination.is_dir():
        if ({p.name for p in destination.iterdir()} != set(files)
                or any((destination / name).is_symlink() or not (destination / name).is_file()
                       or (destination / name).read_bytes() != content for name, content in files.items())):
            raise ValueError("Existing export differs from the verified bundle; choose a new export directory")
        return
    destination.mkdir(parents=True, exist_ok=False)
    for name, content in files.items():
        (destination / name).write_bytes(content)
