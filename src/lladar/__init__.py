"""Public API, loaded on demand so the CLI can announce startup first."""

__all__ = [
    "ChunkingError",
    "DatasetValidationError",
    "GenerationError",
    "KnowledgeLoadError",
    "LladarError",
    "ProviderError",
    "create_test_dataset",
    "create_report",
    "eval",
    "evaluate",
    "run_agent",
]

_EXPORT_MODULES = {
    "EvaluationError": "exceptions",
    "create_test_dataset": "api",
    "create_report": "reporting",
    "evaluate": "evaluation",
    "eval": "evaluation",
    "run_agent": "runner",
}


def __getattr__(name):
    if name not in __all__ and name not in _EXPORT_MODULES:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module

    module = import_module(f".{_EXPORT_MODULES.get(name, 'exceptions')}", __name__)
    value = getattr(module, "evaluate" if name == "eval" else name)
    globals()[name] = value
    return value


def __dir__():
    return sorted(set(globals()) | set(__all__) | set(_EXPORT_MODULES))
