"""Dependency-light console entry: acknowledge the command before loading it."""

import sys


def announce_startup():
    from .language import Language, language_scope, normalize_lang, tr

    # Read only the display language; argparse remains responsible for validation.
    # Never echo arguments here: they may contain private input or credentials.
    language = "en"
    invocation = sys.argv[1:]
    for index, token in enumerate(invocation):
        if token == "--":
            break
        option, separator, value = token.partition("=")
        if option in {"--la", "--lan", "--lang"}:
            if not separator:
                value = invocation[index + 1] if index + 1 < len(invocation) else ""
            try:
                language = normalize_lang(value)
            except ValueError:
                pass
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")
    with language_scope(Language(language)):
        print(tr("LLaDAR Starting... Loading components, please wait."), file=sys.stderr, flush=True)


def main():
    announce_startup()
    from .cli import main as run

    return run()
