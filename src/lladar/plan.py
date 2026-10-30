"""Interactive planning: models propose data; the host validates and compiles it."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
import sys
from time import monotonic
import traceback
from typing import Any, Callable

from .language import current_language, language_prompt
from .providers.akasha import AkashaProvider
from .providers.base import parse_json_object
from .exceptions import ProviderError
from .plan_artifacts import render_plan, write_package
from .plan_events import PlanEvents, redact
from .plan_spec import PLAN_SCHEMA, PlanError, check_schema, parse_spec
from .plan_workflows import PROFILES, compile_spec, field_value, required_fields, validate_spec
from .plan_ui import (_answer_from_option, _ask_interruptibly, _choose_option,
                      _is_free_input_option, _is_free_label, _plan_ui, _render_question)

DEFAULT_PLAN_MODEL = "gemini:gemini-3.8-flash"
MAX_PLAN_TURNS = 24
MAX_INVALID_RESPONSES = 3
PLAN_SKILL = Path(__file__).with_name("skill_assets") / "plan" / "SKILL.md"


def _parse_response(response):
    try:
        return parse_json_object(response)
    except ProviderError as error:
        raise PlanError("PLAN_MODEL_INVALID_RESPONSE", str(error), stage="model") from error


def _failure_diagnostic(error, stage):
    if isinstance(error, PlanError):
        return error.diagnostic
    if isinstance(error, ProviderError):
        chain, current = [], error
        while current is not None and len(chain) < 10:
            chain.append(str(current))
            current = current.__cause__
        limited = any("429" in message or "RESOURCE_EXHAUSTED" in message for message in chain)
        return PlanError("PLAN_MODEL_RATE_LIMITED" if limited else "PLAN_MODEL_PROVIDER_ERROR",
                         "Planning provider capacity or quota exhausted (429)" if limited else "Planning provider request failed",
                         stage="model", cause="Provider rejected the model request" if limited else str(error),
                         next_action="Check provider capacity, quota and configuration, then start a new plan.", recoverable=True).diagnostic
    return PlanError("PLAN_UNEXPECTED_EXCEPTION", f"{type(error).__name__}: {error}", stage=stage, recoverable=False,
                     next_action="Inspect the log and trace ID, fix the cause, then start a new plan.").diagnostic


def resolve_plan_output(destination: str | Path | None) -> Path:
    if destination is not None:
        path = Path(destination).resolve()
        if path.exists():
            raise PlanError("PLAN_INPUT_INVALID", f"plan output already exists: {path}", stage="input", field="output",
                            next_action="Choose a new plan output directory; existing plans are preserved.")
        return path
    stamp = datetime.now().astimezone().strftime("plan-%Y%m%d-%H%M%S")
    path, index = Path("runs") / stamp, 1
    while path.exists():
        path = Path("runs") / f"{stamp}-{index}"
        index += 1
    return path.resolve()


def _message_text(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return value.get("text") if isinstance(value.get("text"), str) else None
    if isinstance(value, list):
        return "".join(part for item in value if (part := _message_text(item)) is not None) or None
    return None


def _workflow_reference():
    return json.dumps({"profiles": [profile.contract() for profile in PROFILES.values()], "PlanSpec": PLAN_SCHEMA}, ensure_ascii=False)


class PlanAgent:
    def __init__(self, *, model: str, env_file: str, agent_factory: Callable[..., Any] | None = None,
                 max_input_tokens: int | None = None, max_output_tokens: int | None = None,
                 events: PlanEvents | None = None):
        self.model, self.env_file, self.agent_factory = model, env_file, agent_factory
        self.max_input_tokens, self.max_output_tokens = max_input_tokens, max_output_tokens
        self.events = events

    def ask(self, transcript):
        system = language_prompt(PLAN_SKILL.read_text(encoding="utf-8") + "\n" + _workflow_reference())
        prompt = (system + "\nCONVERSATION:\n" + json.dumps(transcript, ensure_ascii=False) +
                  "\nIf no initial request was supplied, ask for the test goal in field user_goal.")
        if self.agent_factory is not None:
            agent = self.agent_factory(model=self.model, env_file=self.env_file,
                                       max_input_tokens=self.max_input_tokens, max_output_tokens=self.max_output_tokens)
            response = agent(prompt)
            return response if isinstance(response, dict) else _parse_response(response)
        # Only host-redacted lifecycle events are displayed, also in debug mode.
        provider = AkashaProvider(env_file=self.env_file, max_input_tokens=self.max_input_tokens,
                                  max_output_tokens=self.max_output_tokens, verbose=False, stream=False)
        response = provider.generate_message(json.dumps(transcript, ensure_ascii=False),
                                             system_prompt=system, model=self.model, temperature=0.0)
        usage = getattr(response, "usage_metadata", None)
        if self.events and isinstance(usage, dict):
            self.events.emit("model", "usage", "Model token usage", **{
                key: value for key, value in usage.items() if key in {"input_tokens", "output_tokens", "total_tokens"} and type(value) is int})
        return _parse_response(_message_text(getattr(response, "content", response)))


def _merge(previous, update):
    merged = deepcopy(previous)
    for key, value in update.items():
        merged[key] = _merge(merged.get(key, {}), value) if isinstance(value, dict) else deepcopy(value)
    return merged


QUESTION_SCHEMA = {
    "type": "object", "required": ["kind", "field", "message", "known"],
    "properties": {
        "kind": {"type": "string", "enum": ["question"]}, "field": {"type": "string"},
        "message": {"type": "string"}, "context": {"type": "string"},
        "options": {"type": "array", "items": {}}, "recommended": {"type": "integer"},
        "known": PLAN_SCHEMA, "assumptions": {"type": "array", "items": {"type": "string"}},
        "open_questions": {"type": "array", "items": {"type": "string"}},
    },
}


class ConversationState:
    def __init__(self):
        self.known, self.answered, self.messages = {}, set(), set()

    def candidate(self, spec):
        def verify(known, candidate, prefix=""):
            for field, value in known.items():
                path = f"{prefix}.{field}" if prefix else field
                if isinstance(value, dict):
                    verify(value, candidate.get(field, {}), path)
                elif value not in (None, "", [], {}) and candidate.get(field) != value:
                    raise PlanError("PLAN_MODEL_INVALID_RESPONSE", f"Candidate contradicts current decision: {path}",
                                    stage="conversation", field=path,
                                    next_action="Use current decisions; update known explicitly when the user changes the goal.")
        verify(self.known, spec)

    def question(self, value, events):
        check_schema(value, QUESTION_SCHEMA, partial=True)
        for key in QUESTION_SCHEMA["required"]:
            if key not in value:
                raise PlanError("PLAN_MODEL_INVALID_RESPONSE", f"Question requires {key}", stage="conversation", field=key)
        field = value["field"]
        schema = PLAN_SCHEMA
        for part in field.split("."):
            schema = schema.get("properties", {}).get(part)
            if schema is None:
                raise PlanError("PLAN_MODEL_INVALID_RESPONSE", f"Unknown question field: {field}", stage="conversation", field=field)
        if field.split(".")[0] in {"schema_version", "status", "diagnostics", "assumptions", "limitations", "open_questions"} or not value["message"].strip():
            raise PlanError("PLAN_MODEL_INVALID_RESPONSE", "Question is empty or asks for a host-owned field", stage="conversation", field=field)
        updates = value["known"]
        previous_goal = self.known.get("user_goal")
        if previous_goal and updates.get("user_goal", previous_goal) != previous_goal:
            self.known, self.answered, self.messages = {}, set(), set()
            events.emit("conversation", "reset", "Goal changed; previous decisions invalidated")
        known = _merge(self.known, updates)
        if known.get("workflow") in PROFILES:
            needed = required_fields(known)
            if field not in {"workflow", "user_goal", "observable_behavior", "stages", "observation.kind", "evidence"} and not any(
                item == field or item.startswith(field + ".") or field.startswith(item + ".") for item in needed
            ):
                raise PlanError("PLAN_MODEL_INVALID_RESPONSE", f"Field is not needed by this profile/scope: {field}", stage="conversation", field=field)
        if field in self.answered or field_value(known, field) not in (None, "", [], {}):
            raise PlanError("PLAN_MODEL_INVALID_RESPONSE", f"Question repeats known/answered field: {field}", stage="conversation", field=field)
        if value["message"].strip() in self.messages:
            raise PlanError("PLAN_MODEL_INVALID_RESPONSE", "Repeated question", stage="conversation", field=field)
        options = value.get("options", [])
        for option in options:
            if isinstance(option, str) and option.strip():
                continue
            if not isinstance(option, dict) or not isinstance(option.get("label"), str) or not option["label"].strip() or set(option) - {"label", "description", "type"}:
                raise PlanError("PLAN_MODEL_INVALID_RESPONSE", "Invalid question option", field="options")
            if "description" in option and not isinstance(option["description"], str):
                raise PlanError("PLAN_MODEL_INVALID_RESPONSE", "Option description must be text", field="options")
        if len([option for option in options if not _is_free_label(option)]) > 4:
            raise PlanError("PLAN_MODEL_INVALID_RESPONSE", "Offer at most four alternatives", field="options")
        before, self.known = self.known, known
        self.messages.add(value["message"].strip())
        events.emit("conversation", "success", "Fields updated", before=before, update=updates,
                    missing=[item for item in required_fields(known) if field_value(known, item) in (None, "", [], {})])
        prepared = dict(value)
        prepared["options"] = list(options)
        if not any(_is_free_label(option) for option in options):
            prepared["options"].append({"label": _plan_ui("free_option"), "type": "free_input"})
        return prepared


def _get_answer(question, input_fn, output_fn):
    if question.get("options") and input_fn is input and sys.stdin.isatty():
        answer = _choose_option(question)
    else:
        output_fn(_render_question(question))
        answer = input_fn("> ")
    if answer.startswith("__free_text__"):
        answer = answer.removeprefix("__free_text__") + input_fn("> ")
    elif _is_free_input_option(answer, question):
        answer = input_fn("> ")
    return _answer_from_option(answer, question)


def run_plan(*, requirement: str | None, output: str | Path | None, model: str,
             env_file: str, agent_factory: Callable[..., Any] | None = None,
             input_fn: Callable[[str], str] = input, output_fn: Callable[[str], None] = print,
             max_input_tokens: int | None = None, max_output_tokens: int | None = None,
             debug: bool = False, log: str | Path | None = None) -> Path:
    destination = resolve_plan_output(output)
    events = PlanEvents(destination, log=log, debug=debug, output=output_fn)
    redact = events.redact
    destination.mkdir(parents=True, exist_ok=False)
    transcript = [{"role": "user", "content": redact(requirement)}] if requirement else []
    plan = {"status": "failed", "user_goal": redact(requirement or ""), "diagnostics": [], "commands": []}
    state, stage = ConversationState(), "input"
    try:
        events.emit("input", "start", "Starting planning-only conversation", model=model, schema_version=2,
                    requirement=redact(requirement or ""), log_path=str(events.log_path))
        if input_fn is input and agent_factory is None and not sys.stdin.isatty():
            raise PlanError("PLAN_INPUT_INVALID", "lladar plan requires an interactive terminal", stage="input")
        for field, value in (("max_input_tokens", max_input_tokens), ("max_output_tokens", max_output_tokens)):
            if value is not None and (type(value) is not int or value < 1):
                raise PlanError("PLAN_INPUT_INVALID", f"{field} must be positive", stage="input", field=field)
        if not isinstance(model, str) or not model.strip():
            raise PlanError("PLAN_INPUT_INVALID", "Planning model must be nonempty", stage="input", field="model")
        if events.errors:
            raise PlanError("PLAN_ARTIFACT_WRITE_ERROR", "Initial log creation failed", stage="log")
        events.emit("input", "success", "Planning options validated")
        output_fn(_plan_ui("intro"))
        agent = PlanAgent(model=model, env_file=env_file, agent_factory=agent_factory,
                          max_input_tokens=max_input_tokens, max_output_tokens=max_output_tokens, events=events)
        invalid, incomplete = 0, 0
        for turn in range(MAX_PLAN_TURNS):
            stage, started = "model", monotonic()
            events.emit(stage, "start", "Requesting planning model", model=model, attempt=turn + 1,
                        prompt_summary={"messages": len(transcript), "known": state.known, "profiles": list(PROFILES)}, retry=invalid)
            try:
                result = _ask_interruptibly(agent, transcript, progress=lambda elapsed: events.emit(
                    "model", "waiting", "Waiting for model", elapsed_seconds=round(elapsed, 2)))
                events.emit(stage, "success", "Model response received", elapsed_seconds=round(monotonic() - started, 3), response=redact(result))
                stage = "schema"
                events.emit(stage, "start", "Checking model response schema")
                if not isinstance(result, dict):
                    raise PlanError("PLAN_MODEL_INVALID_RESPONSE", "Model response must be an object")
                if result.get("kind") == "question":
                    stage = "conversation"
                    question = state.question(redact(result), events)
                    events.emit("schema", "success", "Question schema and state validated")
                    answer = redact(_get_answer(question, input_fn, output_fn))
                    events.emit("conversation", "answer", "User answered", field=question["field"], answer=answer)
                    state.answered.add(question["field"])
                    transcript.extend([{"role": "assistant", "content": json.dumps(redact(question), ensure_ascii=False)},
                                       {"role": "user", "content": answer}])
                    invalid = 0
                    continue
                if result.get("kind") != "complete" or set(result) != {"kind", "plan"}:
                    raise PlanError("PLAN_MODEL_INVALID_RESPONSE", "Expected question or complete draft PlanSpec")
                spec = parse_spec(result["plan"])
                state.candidate(spec)
                if redact(spec) != spec:
                    raise PlanError("PLAN_MODEL_INVALID_RESPONSE", "Plan contains credentials or sensitive values; use secret-free references")
                state.known = deepcopy(result["plan"])
                events.emit(stage, "success", "PlanSpec schema validated", schema_version=2)
            except PlanError as error:
                diagnostic = error.diagnostic
                item = events.diagnostic(diagnostic, stack_trace=traceback.format_exc())
                invalid += 1
                if invalid >= MAX_INVALID_RESPONSES:
                    plan["status"], plan["commands"] = "failed", []
                    plan["diagnostics"].append(item)
                    break
                events.emit(diagnostic["stage"], "retry", "Requesting a corrected structured response", retry=invalid)
                transcript.append({"role": "system", "content": json.dumps({"error": item, "known": state.known,
                                   "answered_fields": sorted(state.answered), "instruction": "Correct structured data; do not generate CLI commands."}, ensure_ascii=False)})
                continue
            plan, stage = spec, "validate"
            events.emit(stage, "start", "Validating profile, inputs and stage dependencies")
            plan["diagnostics"] = validate_spec(plan, events)
            if plan["diagnostics"]:
                plan["status"] = "blocked"
                missing = [item for item in plan["diagnostics"] if item["code"] == "PLAN_REQUIRED_FIELD_MISSING"]
                if missing and len(missing) == len(plan["diagnostics"]) and not plan["open_questions"] and incomplete < 2:
                    incomplete += 1
                    state.known = deepcopy(result["plan"])
                    transcript.extend([
                        {"role": "assistant", "content": json.dumps(redact(result), ensure_ascii=False)},
                        {"role": "system", "content": json.dumps({"missing_fields": missing, "known": state.known,
                            "instruction": "Fill fields already supplied by the user. Otherwise ask one missing necessary question. If input is unavailable, return draft with open_questions explaining it."}, ensure_ascii=False)},
                    ])
                    events.emit("validate", "retry", "Required decisions remain; continue clarification", retry=incomplete)
                    continue
                events.emit("compile", "skipped", "Blocked plan has no command script")
            else:
                stage = "compile"
                events.emit(stage, "start", "Compiling structured stages")
                plan["commands"] = compile_spec(plan, destination, lang=current_language().lang, env_file=env_file, events=events)
                plan["status"] = "ready"
            break
        else:
            raise PlanError("PLAN_MODEL_INVALID_RESPONSE", f"Conversation exceeded {MAX_PLAN_TURNS} turns", stage="conversation",
                            next_action="Review the saved conversation and narrow or clarify the goal.")
    except (KeyboardInterrupt, EOFError) as error:
        plan["status"], plan["commands"] = "failed", []
        plan["diagnostics"].append(events.diagnostic(PlanError("PLAN_INPUT_INVALID", f"Conversation interrupted: {type(error).__name__}",
                                                               stage=stage, next_action="Start a new plan when ready.").diagnostic))
    except Exception as error:
        diagnostic = _failure_diagnostic(error, stage)
        plan["status"], plan["commands"] = "failed", []
        plan["diagnostics"].append(events.diagnostic(diagnostic, exception_type=type(error).__name__, stack_trace=traceback.format_exc()))
    if plan["status"] == "failed":
        plan = {**(deepcopy(state.known) if state.known else plan), "status": "failed", "commands": [], "diagnostics": plan["diagnostics"]}
    if events.errors:
        plan["status"], plan["commands"] = "failed", []
        plan["diagnostics"].extend(events.errors)
    payload = {"schema_version": 2, "created_at": datetime.now().astimezone().isoformat(), "model": model,
               "run_id": events.run_id, "plan_id": events.plan_id, "log_path": str(events.log_path),
               "conversation": transcript, "plan": plan}
    failures = write_package(destination, payload, events)
    if failures or events.errors:
        plan["status"], plan["commands"] = "failed", []
        plan["diagnostics"] += [item for item in failures + events.errors if item not in plan["diagnostics"]]
        script = destination / "commands.ps1"
        if script.exists():
            script.unlink()
        write_package(destination, payload, events)
    events.emit("plan", "finished", "Plan saved; target not called", status=plan["status"],
                output=str(destination), log_path=str(events.log_path), diagnostics=plan["diagnostics"])
    if any(item not in plan["diagnostics"] for item in events.errors):
        plan["status"], plan["commands"] = "failed", []
        plan["diagnostics"] += [item for item in events.errors if item not in plan["diagnostics"]]
        script = destination / "commands.ps1"
        if script.exists():
            script.unlink()
        write_package(destination, payload, events)
        events.emit("plan", "finished", "Plan failed during final logging", status="failed",
                    output=str(destination), log_path=str(events.log_path), diagnostics=plan["diagnostics"])
    events.closed = True
    output_fn(redact(render_plan(plan)))
    output_fn(f"{_plan_ui('saved')} {destination}\nlog: {events.log_path}")
    return destination
