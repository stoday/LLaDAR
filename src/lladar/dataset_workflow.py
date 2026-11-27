"""Selection and user-visible descriptions of document dataset workflows."""

from .language import tr


def resolve_workflow_level(level, *, from_github, skill, controlled_variants):
    if level is not None and (type(level) is not int or level not in (1, 2, 3)):
        raise ValueError(tr("Workflow level must be 1, 2, or 3."))
    if from_github is not None:
        if level is not None:
            raise ValueError(tr("Workflow level applies to --knowledge only."))
        return None
    return level if level is not None else 3 if skill is not None or controlled_variants else 1


def workflow_summary(level):
    behavior = {
        1: "Combined generation",
        2: "Combined generation, selective review and sampled source audit",
        3: "Staged generation and full source audit",
    }[level]
    return tr("Workflow level {level} · {behavior}", level=level, behavior=tr(behavior))
