"""Validated question-type contracts carried outside the three-field dataset."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any
import unicodedata


GENERATION_SIDECAR_VERSION = 5


def record_fingerprint(record: dict[str, Any]) -> str:
    """Return the stable join key for a question and expected answer."""
    payload = json.dumps(
        [record["question"], record["expected_answer"]],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_question_type_contract(dataset: Path, records: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Return a minimal verified contract, or None when lineage cannot be trusted."""
    sidecar_path = Path(str(dataset) + ".generation.json")
    try:
        raw = sidecar_path.read_bytes()
        sidecar = json.loads(raw.decode("utf-8"))
        metadata = sidecar["dataset"]
        if (sidecar.get("schema_version") != GENERATION_SIDECAR_VERSION
                or metadata.get("sha256") != hashlib.sha256(dataset.read_bytes()).hexdigest()):
            return None
        lines = metadata["lines"]
        if not isinstance(lines, list) or len(lines) != len(records):
            return None
        contracts: dict[str, dict[str, Any]] = {}
        for index, record in enumerate(records, 1):
            line = lines[index - 1]
            fingerprint = record_fingerprint(record)
            if not isinstance(line, dict) or line.get("line") != index or line.get("record_fingerprint") != fingerprint:
                return None
            question_type, protocol = line.get("question_type"), line.get("answer_protocol")
            if not isinstance(question_type, str) or not isinstance(protocol, str):
                return None
            option_ids = []
            if question_type != "free":
                options = line.get("options")
                if not isinstance(options, list):
                    return None
                option_ids = [option.get("id") for option in options if isinstance(option, dict)]
                if len(option_ids) != len(options) or not option_ids or len(set(option_ids)) != len(option_ids):
                    return None
            contracts[fingerprint] = {
                "question_type": question_type,
                "answer_protocol": protocol,
                "option_ids": option_ids,
            }
        return {
            "dataset_sha256": metadata["sha256"],
            "generation_sidecar_sha256": hashlib.sha256(raw).hexdigest(),
            "records": contracts,
        }
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError):
        return None


def load_probe_contract(dataset: Path, records: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Return verified v5 semantic-probe metadata without exposing source text."""
    sidecar_path = Path(str(dataset) + ".generation.json")
    try:
        raw = sidecar_path.read_bytes()
        sidecar = json.loads(raw.decode("utf-8"))
        metadata = sidecar["dataset"]
        if (sidecar.get("schema_version") != GENERATION_SIDECAR_VERSION
                or metadata.get("sha256") != hashlib.sha256(dataset.read_bytes()).hexdigest()):
            return None
        lines = metadata["lines"]
        if not isinstance(lines, list) or len(lines) != len(records):
            return None
        contracts: dict[str, dict[str, Any]] = {}
        for index, record in enumerate(records, 1):
            line = lines[index - 1]
            fingerprint = record_fingerprint(record)
            if not isinstance(line, dict) or line.get("line") != index or line.get("record_fingerprint") != fingerprint:
                return None
            if line.get("plan_type") not in {"concept_mapping", "controlled_invariance"}:
                continue
            candidates = line.get("candidates")
            if not isinstance(candidates, list) or not candidates:
                return None
            normalized = []
            for candidate in candidates:
                if not isinstance(candidate, dict) or not all(isinstance(candidate.get(key), str) and candidate[key]
                                                              for key in ("entity_id", "label", "value", "unit")):
                    return None
                normalized.append({key: candidate[key] for key in ("entity_id", "label", "value", "unit")})
            contract = {"plan_type": line["plan_type"], "candidates": normalized,
                        "concept_id": line.get("concept_id"), "concept_origin": line.get("concept_origin"),
                        "pair_id": line.get("pair_id"), "varied_dimension": line.get("varied_dimension"),
                        "answer_contract": line.get("answer_contract")}
            if not isinstance(contract["concept_id"], str) or not isinstance(contract["concept_origin"], str):
                return None
            if contract["plan_type"] == "controlled_invariance" and (
                not isinstance(contract["pair_id"], str) or not isinstance(contract["varied_dimension"], str)
                or contract["answer_contract"] != "invariant"
            ):
                return None
            contracts[fingerprint] = contract
        return {"dataset_sha256": metadata["sha256"], "generation_sidecar_sha256": hashlib.sha256(raw).hexdigest(),
                "records": contracts}
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError):
        return None


def load_run_probe_contract(responses: Path) -> dict[str, dict[str, Any]]:
    """Read the verified semantic-probe snapshot written by run-agent."""
    try:
        run = json.loads(Path(str(responses) + ".run.json").read_text(encoding="utf-8"))
        records = run["probe_contract"]["records"]
        return records if isinstance(records, dict) else {}
    except (OSError, json.JSONDecodeError, KeyError, TypeError):
        return {}


def load_run_question_type_contract(responses: Path) -> dict[str, dict[str, Any]]:
    """Read only a complete contract snapshot carried by a responses run sidecar."""
    try:
        run = json.loads(Path(str(responses) + ".run.json").read_text(encoding="utf-8"))
        records = run["question_type_contract"]["records"]
        if not isinstance(records, dict):
            return {}
        return {
            fingerprint: value for fingerprint, value in records.items()
            if isinstance(fingerprint, str) and isinstance(value, dict)
            and isinstance(value.get("question_type"), str)
            and isinstance(value.get("answer_protocol"), str)
            and isinstance(value.get("option_ids"), list)
        }
    except (OSError, json.JSONDecodeError, KeyError, TypeError):
        return {}


def deterministic_judgment(contract: dict[str, Any], expected_answer: str, actual_response: str | None) -> dict[str, Any]:
    """Judge a typed response without interpreting natural-language content."""
    response = unicodedata.normalize("NFKC", actual_response or "").strip().upper()
    expected = unicodedata.normalize("NFKC", expected_answer).strip().upper()
    option_ids = [str(value).upper() for value in contract["option_ids"]]
    protocol = contract["answer_protocol"]
    valid = False
    if protocol == "one_option_id":
        valid = response in option_ids
    elif protocol == "option_id_list":
        parts = response.split(",")
        valid = bool(response) and all(part in option_ids for part in parts) and len(parts) == len(set(parts))
    elif protocol == "ordered_option_ids":
        parts = response.split(">")
        valid = (len(parts) == len(option_ids) and set(parts) == set(option_ids)
                 and len(parts) == len(set(parts)))
    else:
        raise ValueError(f"unsupported answer protocol: {protocol}")
    return {
        "values": {"correct": valid and response == expected, "response_format_valid": valid},
        "reason": f"Deterministic {protocol} comparison.",
    }
