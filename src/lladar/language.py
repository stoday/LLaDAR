"""Per-work language policy; presentation never rewrites evidence or target text."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from functools import wraps
import inspect
import json
from pathlib import Path
import re
import sys

SUPPORTED = {"en": "English", "zh-TW": "Traditional Chinese", "ja": "Japanese",
             "ko": "Korean", "zh-CN": "Simplified Chinese"}
_ALIASES = {key.lower(): key for key in SUPPORTED} | {"jp": "ja"}
_CURRENT = ContextVar("lladar_language", default=None)
_CATALOGS = {}


def normalize_lang(value: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*", value) is None:
        raise ValueError("invalid --lang code; examples: en, zh-TW, ja, ko, zh-CN, fr")
    return _ALIASES.get(value.lower(), value.lower())


@dataclass(frozen=True)
class Language:
    lang: str = "en"

    @property
    def ui_lang(self) -> str:
        return self.lang if self.lang in SUPPORTED else "en"

    def metadata(self) -> dict:
        return {"lang": self.lang, "ui_lang": self.ui_lang}

    def instruction(self) -> str:
        target = SUPPORTED.get(self.lang, f"the language identified by this code: {self.lang}")
        return (
            f"Write all newly generated human-readable content in {target}. "
            "This presentation language takes precedence over general language preferences in a skill. "
            "Keep JSON keys, IDs, enum values, tool names, code, exact-match probes, calibration tokens, "
            "verbatim source quotes, and saved questions and target-agent responses unchanged. "
            "Preserve evidence, negations, quantities, scoring rules, and deterministic measurements."
        )


def current_language() -> Language:
    return _CURRENT.get() or Language()


def resolve_language(lang: str | None = None, resume: str | Path | None = None) -> Language:
    if resume is not None:
        from .work_session import digest
        envelope = json.loads((Path(resume) / "session.json").read_text(encoding="utf-8"))
        if digest(envelope["data"]) != envelope["sha256"]:
            raise ValueError("checkpoint integrity check failed")
        saved = envelope["data"]["configuration"].get("lang")
        if saved is None:
            raise ValueError("session has no language setting; resume with its original version or start a new work session")
        saved = normalize_lang(saved)
        if lang is not None and normalize_lang(lang) != saved:
            raise ValueError(f"resume language mismatch: saved={saved}, requested={normalize_lang(lang)}; start a new work session to change language")
        return Language(saved)
    return Language(normalize_lang(lang) if lang is not None else current_language().lang)


@contextmanager
def language_scope(language: Language, *, announce: bool = False):
    previous = _CURRENT.get()
    token = _CURRENT.set(language)
    try:
        if announce and language.lang not in SUPPORTED and previous != language:
            print(f"Language {language.lang}: model-generated content uses this code; interface, fixed headings and offline templates use English.", file=sys.stderr)
        yield language
    finally:
        _CURRENT.reset(token)


def language_stage(function):
    """Validate before stage side effects and restore the previous context afterwards."""
    signature = inspect.signature(function)

    @wraps(function)
    def invoke(*args, **kwargs):
        bound = signature.bind(*args, **kwargs)
        language = resolve_language(bound.arguments.get("lang"), bound.arguments.get("resume"))
        bound.arguments["lang"] = language.lang
        with language_scope(language, announce=True):
            return function(*bound.args, **bound.kwargs)
    return invoke


def language_prompt(prompt: str) -> str:
    return prompt + "\n\nLLaDAR output language policy:\n" + current_language().instruction()


def localize_error(detail: str) -> str:
    """Translate known package diagnostics; retain unknown technical detail verbatim."""
    translated = tr(detail)
    if translated != detail or current_language().ui_lang == "en":
        return translated
    match = re.fullmatch(r"resume language mismatch: saved=(.*), requested=(.*); start a new work session to change language", detail)
    if match:
        return tr("resume language mismatch: saved={saved}, requested={requested}; start a new work session to change language",
                  saved=match[1], requested=match[2])
    for prefix in ("output already exists: ", "skill is missing SKILL.md: "):
        if detail.startswith(prefix):
            return tr(prefix + "{path}", path=detail[len(prefix):])
    return tr("Operation failed; diagnostic: {detail}", detail=detail)


def tr(template: str, *, language: Language | None = None, **values) -> str:
    """Translate a trusted fixed template, then interpolate untouched values."""
    code = (language or current_language()).ui_lang
    if code not in _CATALOGS:
        _CATALOGS[code] = json.loads((Path(__file__).parent / "locales" / f"{code}.json").read_text(encoding="utf-8"))
    translated = _CATALOGS[code].get(template, template)
    return translated.format(**values) if values else translated
