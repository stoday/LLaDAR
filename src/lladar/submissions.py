"""Versioned business submissions with bounded local repair and replay."""
from copy import deepcopy
from .work_session import current_session
from .exceptions import EvaluationError, DatasetValidationError


class Submissions:
    def __init__(self, namespace):
        self.session = current_session()
        self.namespace = namespace
        self.state = deepcopy(self.session.data["state"].get(namespace, {})) if self.session else {}

    def get(self, item_id):
        return deepcopy(self.state.get(item_id, {"item_id": item_id, "status": "conflict",
            "issues": [{"field": "item_id", "code": "unknown_item",
                        "message": "Unknown item", "repair_action": "query_status"}]}))

    def submit(self, item_id, payload, validator, *, request_id=None, revision=None, frozen=False):
        previous = self.state.get(item_id)
        if request_id is None:
            request_id = f"legacy-{(previous or {}).get('revision', 0) + 1}"
        if not isinstance(request_id, str) or not request_id.strip():
            return {"status": "needs_revision", "issues": [{"field": "request_id", "code": "required",
                "message": "Provide a non-empty request ID", "repair_action": "replace_request_id"}]}
        identity = {"payload": payload, "revision": revision}
        requests = deepcopy((previous or {}).get("requests", {}))
        if request_id in requests:
            entry = requests[request_id]
            if entry["identity"] == identity:
                return deepcopy(entry["result"])
            return {"status": "conflict", "item_id": item_id,
                "issues": [{"field": "request_id", "code": "content_conflict",
                            "message": "Request ID has different content", "repair_action": "use_new_request_id"}]}
        if revision is not None and (previous is None or type(revision) is not int or revision != previous["revision"]):
            return {"status": "conflict", "item_id": item_id, "issues": [{"field": "revision",
                "code": "revision_conflict", "message": "Query the current revision", "repair_action": "query_status"}]}
        if previous and (previous["status"] == "rejected" or frozen and previous["status"] == "accepted"):
            return {"status": "conflict" if frozen else "rejected", "item_id": item_id,
                    "revision": previous["revision"], "issues": [{"field": "item_id", "code": "frozen",
                    "message": "Item cannot be changed", "repair_action": "create_new_session"}]}
        number = (previous or {}).get("revision", 0) + 1
        try:
            value = validator(payload)
        except (ValueError, TypeError, KeyError, EvaluationError, DatasetValidationError) as error:
            field = "values" if isinstance(payload, dict) and "values" in payload else "payload"
            result = {"status": "rejected" if number >= 4 else "needs_revision", "item_id": item_id,
                      "revision": number, "issues": [{"field": field, "code": "invalid_submission",
                      "message": str(error), "repair_action": "revise_item"}]}
        else:
            result = {"status": "accepted", "accepted": True, "item_id": item_id,
                      "revision": number, "issues": []}
        requests[request_id] = {"identity": deepcopy(identity), "result": deepcopy(result)}
        draft = {**result, "payload": deepcopy(payload), "requests": requests}
        if result["status"] == "accepted":
            draft["value"] = deepcopy(value)
        elif previous and "value" in previous:
            draft["value"] = deepcopy(previous["value"])
        updated = {**self.state, item_id: draft}
        if self.session:
            self.session.put(self.namespace, updated)
        self.state = updated
        return deepcopy(result)
