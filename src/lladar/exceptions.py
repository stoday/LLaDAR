class LladarError(Exception):
    """Base exception for LLaDAR."""


class KnowledgeLoadError(LladarError):
    pass


class ChunkingError(LladarError):
    pass


class ProviderError(LladarError):
    pass


class GenerationError(LladarError):
    pass


class DatasetValidationError(LladarError):
    pass

class EvaluationError(LladarError):
    pass
