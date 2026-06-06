"""Dense embeddings behind a swappable interface.

Default impl uses fastembed (ONNX, no PyTorch). Models are cached under
`config.model_cache_dir()`. Swap to an API embedder later by implementing
`Embedder` and changing `get_embedder()`.
"""

from functools import lru_cache
from typing import Protocol

from filer_backend.config import model_cache_dir
from filer_backend.settings import get_settings


class Embedder(Protocol):
    dim: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class FastEmbedEmbedder:
    def __init__(self, model_name: str):
        from fastembed import TextEmbedding

        self.model_name = model_name
        self._model = TextEmbedding(
            model_name=model_name, cache_dir=str(model_cache_dir())
        )
        # Probe once to learn the dimensionality (also warms the model).
        self.dim = len(next(iter(self._model.embed(["probe"]))))

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        return [v.tolist() for v in self._model.embed(list(texts))]


@lru_cache
def get_embedder() -> FastEmbedEmbedder:
    return FastEmbedEmbedder(get_settings().embedding.model)
