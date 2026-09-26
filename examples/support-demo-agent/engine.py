"""Internal policy logic for the support-demo-agent example."""

from __future__ import annotations


def answer_question(question: str) -> str:
    """Return a concise answer from the example support policy.

    This is intentionally an internal seam.  The public user-facing boundary is
    the HTTP endpoint implemented in ``server.py``.
    """
    if not isinstance(question, str) or not question.strip():
        raise ValueError("question must be a non-empty string")

    normalized = question.casefold()
    if any(term in normalized for term in ("reply", "response", "回覆", "回應")):
        return "The standard reply target is one business day."
    if any(term in normalized for term in ("urgent", "emergency", "緊急")):
        return "Urgent incidents are handled by the on-call process."
    if any(term in normalized for term in ("email", "business day", "電子郵件", "工作日")):
        return "Email support is available on business days."
    return "The support plan covers business-day email support, a one-business-day reply target, and an on-call process for urgent incidents."
