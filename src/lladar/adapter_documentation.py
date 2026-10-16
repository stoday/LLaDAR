"""Translate export documentation once; keep executable examples under host control."""
from __future__ import annotations

from collections import Counter
from contextlib import redirect_stdout
import hashlib
import json
from pathlib import Path
import re
import sys

from .language import current_language, normalize_lang
from .model_profiles import resolve_model_profile
from .providers.base import parse_json_object
from .live_status import status_activity


_TECHNICAL = re.compile(
    r"`[^`\n]+`|https?://[^\s]+|(?<!\w)-{1,2}[A-Za-z][A-Za-z-]*|\b[A-Z][A-Z0-9_]*_[A-Z0-9_]+\b"
    r"|(?<!\w)\.?[\w-]+\.(?:py|md|txt|example|env|venv)\b"
    r"|(?<!\w)\.(?:env(?:\.example)?|venv)\b"
    r"|(?<!\w)\d+(?:\.\d+)*(?:\+)?(?![\w.])")
_TOKEN = re.compile(r"\[\[T\d+\]\]")


def _segments(text: str, *, environment: bool):
    """Yield literal blocks or prose slots; shell/JSON and assignments stay literal."""
    fenced = False
    paragraph = []
    for line in text.splitlines(keepends=True):
        literal = line.startswith("```") or fenced or not line.strip()
        if environment:
            literal = not line.startswith("# ")
        if literal or line.startswith(("#", "- ")):
            if paragraph:
                yield False, "".join(paragraph)
                paragraph = []
            yield literal, line
        else:
            paragraph.append(line)
        if not environment and line.startswith("```"):
            fenced = not fenced
    if paragraph:
        yield False, "".join(paragraph)


@status_activity("Translating adapter documentation")
def localize_documents(documents: dict[str, str], *, lang: str | None,
                       model: str | None, env_file: str | Path | None,
                       cache: Path | None = None, max_input_tokens: int | None = None,
                       max_output_tokens: int | None = None) -> dict[str, str]:
    language = normalize_lang(lang or current_language().lang)
    if language == "en":
        return documents
    if not model:
        raise ValueError("A model is required to translate portable adapter documentation")
    blocks, slots = {}, {}
    for name, text in documents.items():
        blocks[name] = []
        for literal, block in _segments(text, environment=name == ".env.example"):
            if literal:
                blocks[name].append(block)
                continue
            prefix = re.match(r"^(?:#+ |[-] )?", block)[0]
            prose = block[len(prefix):].strip().replace("\n", " ")
            protected = []

            def protect(match):
                protected.append(match[0])
                return f"[[T{len(protected) - 1}]]"

            key = str(len(slots))
            slots[key] = (_TECHNICAL.sub(protect, prose), protected)
            blocks[name].append((key, prefix, "\n" if block.endswith("\n") else ""))
    texts = {key: item[0] for key, item in slots.items()}
    fingerprint = hashlib.sha256(json.dumps(
        [language, model, documents], ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    translated = None
    if cache is not None and cache.is_file():
        saved = json.loads(cache.read_text(encoding="utf-8"))
        if saved.get("fingerprint") == fingerprint:
            translated = saved.get("texts")
    if translated is None:
        import akasha

        profile = resolve_model_profile(model, max_input_tokens=max_input_tokens,
                                        max_output_tokens=max_output_tokens)
        # Only the trusted template prose and opaque placeholders enter the prompt.
        # No project files, generated source, private URLs or credential values.
        with redirect_stdout(sys.stderr):
            translate = akasha.ask(
                model=model, env_file=str(env_file or ""), temperature=0,
                max_input_tokens=profile.max_input_tokens,
                max_output_tokens=profile.max_output_tokens,
                stream=False, verbose=False, keep_logs=False,
                system_prompt=(
                    f"Translate every documentation string into language {language}. "
                    "Return ONLY a JSON object with exactly the same keys. "
                    "Preserve meaning, requirements, negations and technical terms. "
                    "Keep each [[T<number>]] placeholder exactly once as in its input; "
                    "these stand for immutable code or configuration. "
                    "Each value must be a nonempty single-line string. "
                    "Do not add instructions, Markdown fences, headings or list prefixes."))
            translated = parse_json_object(translate(json.dumps(texts, ensure_ascii=False)))
    if (not isinstance(translated, dict) or set(translated) != set(texts)
            or any(not isinstance(value, str) or not value.strip()
                   or "\n" in value or "\r" in value or "```" in value
                   or Counter(_TOKEN.findall(value)) != Counter(_TOKEN.findall(texts[key]))
                   for key, value in translated.items())):
        raise ValueError("Invalid adapter documentation translation: missing text or changed placeholders")
    result = {}
    for name, parts in blocks.items():
        rendered = []
        for part in parts:
            if isinstance(part, str):
                rendered.append(part)
                continue
            key, prefix, newline = part
            value = _TOKEN.sub(lambda match: slots[key][1][int(match[0][3:-2])], translated[key])
            rendered.append(prefix + value + newline)
        result[name] = "".join(rendered)
    if cache is not None:
        from .work_session import atomic_text
        atomic_text(cache, json.dumps({"fingerprint": fingerprint, "texts": translated},
                                     ensure_ascii=False), force=True)
    return result
