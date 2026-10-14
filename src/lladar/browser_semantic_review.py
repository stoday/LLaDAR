"""Model review of a captured answer when no independent answer oracle exists."""
from __future__ import annotations

import json

from .model_profiles import resolve_model_profile
from .providers.akasha import (disable_model_retries, resolve_model_destination,
                               verify_model_destination)


class SemanticReviewError(RuntimeError):
    reason = 'semantic_review_invalid'


def review_answer(question: str, answer: str, *, model: str, env_file: str,
                  expected_destination=None, max_input_tokens=None,
                  max_output_tokens=None) -> dict:
    """Judge whether a real captured answer is substantive and on topic, not text-equal."""
    import akasha

    profile = resolve_model_profile(model, max_input_tokens=max_input_tokens,
                                    max_output_tokens=max_output_tokens)
    destination = expected_destination or resolve_model_destination(
        model, env_file, profile.max_output_tokens)
    judge = akasha.ask(model=model, env_file=env_file, temperature=0,
                       max_input_tokens=profile.max_input_tokens,
                       max_output_tokens=profile.max_output_tokens,
                       system_prompt=(
                           'Review a response extracted from one real website chat turn. '
                           'Do not answer the question yourself. Decide whether the '
                           'candidate is a substantive, on-topic, reasonable answer. '
                           'Different wording and formatting are allowed. Do not compare '
                           'against a reference answer. Return only JSON with boolean '
                           'key reasonable and a short reason string.'))
    verify_model_destination(judge.model_obj, model=model,
        expected_destination=destination, max_output_tokens=profile.max_output_tokens)
    disable_model_retries(judge.model_obj)
    reply = judge(json.dumps({'question': question, 'candidate_answer': answer},
                             ensure_ascii=False))
    if not isinstance(reply, str):
        raise SemanticReviewError('semantic_review_invalid')
    start, end = reply.find('{'), reply.rfind('}')
    if start < 0 or end < start:
        raise SemanticReviewError('semantic_review_invalid')
    try:
        verdict = json.loads(reply[start:end + 1])
    except ValueError:
        raise SemanticReviewError('semantic_review_invalid') from None
    if type(verdict) is not dict or type(verdict.get('reasonable')) is not bool:
        raise SemanticReviewError('semantic_review_invalid')
    return {'reasonable': verdict['reasonable'],
            'reason': str(verdict.get('reason', ''))[:300]}
