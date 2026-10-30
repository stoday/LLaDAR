"""Versioned workflow profiles, static validation and deterministic compilation."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlsplit

from .plan_spec import PlanError
from .exceptions import LladarError
from .loaders import SUPPORTED_SUFFIXES, load_knowledge


@dataclass(frozen=True)
class WorkflowProfile:
    id: str
    method: str
    required: tuple[str, ...]
    stages: tuple[str, ...]
    targets: tuple[str, ...] = ("project", "api", "page")
    version: int = 1

    def contract(self):
        return {**asdict(self),
                "required_for_full_scope": required_fields({"workflow": self.id, "stages": self.stages}),
                "conditional": {
                    "run-agent": ["target.kind", "target.path (project)", "target.url (page)",
                                  "target.api_guide and target.service_url (api)"],
                    "create absent, run-agent present": ["inputs.dataset or inputs.situation_config"],
                    "eval absent, report present": ["inputs.evaluation"],
                    "eval present, run-agent absent": ["inputs.responses"],
                    "evaluation.kind=criteria": ["evaluation.criteria"],
                    "evaluation.kind=skill": ["evaluation.skill"],
                    "document_qa create": ["inputs.knowledge", "method.count"],
                    "eval present": ["evaluation.kind (expected_answer, criteria, skill; situation uses the frozen rubric)"],
                    "behavior_situation create": ["method.instructions", "method.stop_criteria", "method.max_turns"],
                    "behavior_situation run-agent": ["method.num_scenarios"],
                },
                "derive": ["stages from requested scope", "count from explicit request", "known user answers"],
                "forbidden_questions": ["secrets", "cookies", "tokens", "irrelevant fields", "already answered fields"],
                "completion": "All required/conditional fields and static dependencies validate; compiler succeeds.",
                "blocked": "Missing input, unsupported combination, or external state cannot be observed by this workflow."}


PROFILES = {profile.id: profile for profile in (
    WorkflowProfile("document_qa", "source_grounded_qa", ("user_goal", "observable_behavior"),
                    ("create", "run-agent", "eval", "report")),
    WorkflowProfile("behavior_situation", "behavior_situation", ("user_goal", "observable_behavior"),
                    ("create", "run-agent", "eval", "report"), ("project", "page")),
    WorkflowProfile("evaluate_existing_responses", "existing_responses", ("user_goal", "observable_behavior", "inputs.responses"),
                    ("eval", "report"), ()),
    WorkflowProfile("report_existing_evaluation", "existing_evaluation", ("user_goal", "observable_behavior", "inputs.evaluation"),
                    ("report",), ()),
)}


def field_value(spec, field):
    value = spec
    for part in field.split("."):
        value = value.get(part) if isinstance(value, dict) else None
    return value


def required_fields(spec):
    profile = PROFILES.get(spec.get("workflow"))
    if profile is None:
        return ("workflow",)
    fields = list(profile.required)
    stages = spec.get("stages", [])
    situation = profile.id == "behavior_situation"
    if "create" in stages:
        fields += (["method.instructions", "method.stop_criteria", "method.max_turns"] if situation
                   else ["inputs.knowledge", "method.count"])
    if "run-agent" in stages:
        fields.append("target.kind")
        fields += {"project": ["target.path"], "page": ["target.url"],
                   "api": ["target.api_guide", "target.service_url"]}.get(field_value(spec, "target.kind"), [])
        if situation:
            fields += ["method.num_scenarios"]
        if "create" not in stages:
            fields.append("inputs.situation_config" if situation else "inputs.dataset")
    if "eval" in stages and "run-agent" not in stages:
        fields.append("inputs.responses")
    if "report" in stages and "eval" not in stages:
        fields.append("inputs.evaluation")
    if "eval" in stages:
        fields.append("evaluation.kind")
        fields += {"criteria": ["evaluation.criteria"], "skill": ["evaluation.skill"]}.get(
            field_value(spec, "evaluation.kind"), [])
        if field_value(spec, "evaluation.kind") == "situation" and "create" not in stages:
            fields.append("inputs.situation_config")
    return tuple(dict.fromkeys(fields))


def validate_spec(spec, events):
    """Return all actionable validation failures without executing any workflow."""
    issues = []

    def issue(code, field, message, next_action="Supply or correct the indicated input."):
        error = PlanError(code, message, stage="validate", field=field, next_action=next_action)
        issues.append(events.diagnostic(error.diagnostic))

    profile = PROFILES.get(spec["workflow"])
    events.emit("profile", "start", "Selecting workflow profile", workflow=spec["workflow"])
    if profile is None:
        issue("PLAN_PROFILE_NOT_FOUND", "workflow", f"Unknown workflow: {spec['workflow']}",
              "Choose one of: " + ", ".join(PROFILES))
        return issues
    events.emit("profile", "success", "Selected workflow profile", profile=profile.contract(),
                reason={"requested_goal": spec["user_goal"], "requested_stages": spec["stages"], "method": spec["method"].get("kind")})
    if spec["method"].get("kind") != profile.method:
        issue("PLAN_UNSUPPORTED_COMBINATION", "method.kind", f"{profile.id} requires method {profile.method}")
    stages = spec["stages"]
    if not stages or stages != [stage for stage in profile.stages if stage in stages]:
        issue("PLAN_UNSUPPORTED_COMBINATION", "stages", "Stages must be nonempty, unique and in profile order")
    if profile.id == "evaluate_existing_responses" and "eval" not in stages:
        issue("PLAN_UNSUPPORTED_COMBINATION", "stages", "Existing responses profile requires eval")
    for field in required_fields(spec):
        value = field_value(spec, field)
        if value is None or value == [] or (isinstance(value, str) and not value.strip()):
            issue("PLAN_REQUIRED_FIELD_MISSING", field, f"Required field missing: {field}")
    if spec.get("open_questions"):
        issue("PLAN_REQUIRED_FIELD_MISSING", "open_questions", "Unresolved decisions remain", "Answer the saved open questions.")
    if spec["observation"].get("kind") == "external_state":
        issue("PLAN_EVIDENCE_INSUFFICIENT", "observation.kind",
              "Text evaluation cannot prove hidden business state or completion of an external action.",
              "Provide a supported observable text goal or an external verification method; this workflow cannot verify hidden state.")
    kind = spec["target"].get("kind")
    if "run-agent" in stages and kind and kind not in profile.targets:
        issue("PLAN_UNSUPPORTED_COMBINATION", "target.kind", f"{profile.id} does not support target {kind}")
    if "run-agent" not in stages and any(value for value in spec["target"].values()):
        issue("PLAN_UNSUPPORTED_COMBINATION", "target", "This plan has no run-agent stage; omit the target")
    if "run-agent" in stages:
        allowed = {"project": {"kind", "path"}, "page": {"kind", "url"},
                   "api": {"kind", "api_guide", "service_url"}}.get(kind, {"kind"})
        if any(value for field, value in spec["target"].items() if field not in allowed):
            issue("PLAN_UNSUPPORTED_COMBINATION", "target", "Target fields belong to different entry types")
        if kind == "project" and spec["target"].get("path") and not Path(spec["target"]["path"]).is_dir():
            issue("PLAN_EXISTING_INPUT_MISSING", "target.path", "Project directory does not exist")
        for field in ("target.url", "target.service_url"):
            value = field_value(spec, field)
            if value:
                parsed = urlsplit(value)
                if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                    issue("PLAN_UNSUPPORTED_COMBINATION", field, "Target URL must be an HTTP(S) URL")
    evaluation = spec["evaluation"]
    if "eval" in stages:
        mode = evaluation.get("kind")
        is_situation = profile.id == "behavior_situation" or spec["inputs"].get("situation_config")
        allowed = {"criteria", "skill", "situation"} if is_situation else {"expected_answer", "criteria", "skill"}
        if mode and mode not in allowed:
            issue("PLAN_UNSUPPORTED_COMBINATION", "evaluation.kind", f"Evaluation mode {mode} is not supported by {profile.id}")
        if evaluation.get("criteria") and mode != "criteria" or evaluation.get("skill") and mode != "skill":
            issue("PLAN_UNSUPPORTED_COMBINATION", "evaluation", "Choose exactly one evaluation method")
    for field in ("inputs.dataset", "inputs.responses", "inputs.evaluation", "inputs.situation_config", "target.api_guide", "evaluation.skill"):
        value = field_value(spec, field)
        if not value:
            continue
        try:
            path = Path(value)
            if field == "evaluation.skill":
                if not path.is_dir() or not (path / "SKILL.md").read_text(encoding="utf-8").strip():
                    raise ValueError("Skill must contain a nonempty SKILL.md")
            elif field in {"inputs.dataset", "inputs.responses"}:
                if field == "inputs.responses" and spec["inputs"].get("situation_config"):
                    from .situation import load_situation, read_situation_responses
                    _, config_hash = load_situation(spec["inputs"]["situation_config"])
                    rows, _ = read_situation_responses(path, config_hash)
                    if not rows:
                        raise ValueError("Situation responses contain no trials")
                else:
                    from .records import read_records
                    records = read_records(path)
                    if field == "inputs.dataset" and any(record["actual_response"] is not None for record in records):
                        raise ValueError("Dataset must have empty actual_response fields")
            elif field == "inputs.situation_config":
                from .situation import load_situation
                config, _ = load_situation(path)
                max_turns = spec["method"].get("max_turns")
                if max_turns is not None and max_turns != config["stop"]["max_turns"]:
                    issue("PLAN_UNSUPPORTED_COMBINATION", "method.max_turns", "max_turns differs from the frozen situation config")
                if profile.id == "behavior_situation":
                    spec["method"]["max_turns"] = config["stop"]["max_turns"]
            elif field == "inputs.evaluation":
                from .reporting import read_evaluation
                read_evaluation(path)
            else:
                from .api_guide import read_api_guide
                read_api_guide(path)
            events.emit("validate", "success", "Existing input validated", field=field, path=str(path.resolve()))
        except (OSError, ValueError, LladarError) as error:
            issue("PLAN_EXISTING_INPUT_MISSING", field, f"Input cannot be read or recognized: {error}")
    for index, source in enumerate(spec["inputs"].get("knowledge", [])):
        field = f"inputs.knowledge.{index}"
        try:
            path = Path(source)
            files = load_knowledge(path)
            if not files or any(item.suffix.lower() not in SUPPORTED_SUFFIXES for item, text in files):
                raise ValueError("No supported knowledge files")
            for item, text in files:
                if not text.strip():
                    raise ValueError("Knowledge is empty or contains no extractable text")
            if profile.id == "behavior_situation" and path.is_dir():
                raise ValueError("Situation knowledge must be individual .md or .txt files")
            events.emit("validate", "success", "Knowledge validated", field=field, path=str(path.resolve()))
        except (OSError, ValueError, LladarError) as error:
            issue("PLAN_EXISTING_INPUT_MISSING", field, f"Knowledge cannot be read or recognized: {error}")
    for index, evidence in enumerate(spec["evidence"]):
        if not Path(evidence["path"]).is_file():
            issue("PLAN_EXISTING_INPUT_MISSING", f"evidence.{index}.path", "Evidence file does not exist")
    if profile.id == "behavior_situation" and kind == "page" and "run-agent" in stages and spec["method"].get("max_turns") != 1:
        issue("PLAN_UNSUPPORTED_COMBINATION", "method.max_turns", "Website situations require exactly one target turn")
    events.emit("validate", "success" if not issues else "blocked", "Static validation complete", errors=len(issues))
    return issues


def compile_spec(spec, destination: Path, *, lang: str, env_file: str, events):
    """Construct argv only from validated typed fields and host-owned paths."""
    destination = destination.resolve()
    inputs, method = spec["inputs"], spec["method"]
    situation = spec["workflow"] == "behavior_situation"
    dataset = inputs.get("dataset")
    config = inputs.get("situation_config")
    responses = inputs.get("responses")
    evaluation = inputs.get("evaluation")
    commands = []
    for stage in spec["stages"]:
        if stage == "create":
            if situation:
                config = str(destination / "situation.json")
                argv = ["lladar", "create", "situation", "--instructions", method["instructions"],
                        "--stop-criteria", method["stop_criteria"], "--max-turns", str(method["max_turns"]), "--output", config]
            else:
                dataset = str(destination / "dataset.jsonl")
                argv = ["lladar", "create", "test-dataset", "--count", str(method["count"]), "--output", dataset]
            for source in inputs.get("knowledge", []):
                argv += ["--knowledge", str(Path(source).resolve())]
        elif stage == "run-agent":
            responses = str(destination / "responses.jsonl")
            argv = ["lladar", "run-agent"]
            argv += (["--situation-config", str(Path(config).resolve()), "--num-scenarios", str(method["num_scenarios"])] if situation
                     else [str(Path(dataset).resolve())])
            target = spec["target"]
            if target["kind"] == "project":
                argv += ["--project", str(Path(target["path"]).resolve())]
            elif target["kind"] == "api":
                argv += ["--api-guide", str(Path(target["api_guide"]).resolve()), "--service-url", target["service_url"]]
            else:
                argv += ["--page-url", target["url"]]
            argv += ["--output", responses]
        elif stage == "eval":
            evaluation = str(destination / "evaluation.json")
            argv = ["lladar", "eval", str(Path(responses).resolve()), "--output", evaluation]
            if config:
                argv += ["--situation-config", str(Path(config).resolve())]
            mode = spec["evaluation"]["kind"]
            if mode == "criteria":
                argv += ["--criteria", spec["evaluation"]["criteria"]]
            elif mode == "skill":
                argv += ["--skill", str(Path(spec["evaluation"]["skill"]).resolve())]
        else:
            argv = ["lladar", "report", str(Path(evaluation).resolve()), "--output", str(destination / "report.md")]
        argv += ["--lang", lang, "--env-file", str(Path(env_file).resolve())]
        # The same pure option validation is used by the CLI itself.
        from .cli import build_parser
        from .workflow_options import validate_workflow_options
        import argparse
        import contextlib
        import io
        try:
            parser = build_parser()
            with contextlib.redirect_stderr(io.StringIO()):
                args = argparse.ArgumentParser.parse_args(parser, argv[1:])
            validate_workflow_options(args)
        except (SystemExit, ValueError) as error:
            raise PlanError("PLAN_COMPILATION_ERROR", f"Compiled {stage} arguments rejected: {argv}: {error}",
                            stage="compile", field="stages", next_action="Update the workflow compiler to match the CLI grammar.") from error
        commands.append(argv)
        events.emit("compile", "success", "Compiled stage without execution", workflow_stage=stage, argv=argv)
    return commands
