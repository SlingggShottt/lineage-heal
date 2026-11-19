"""Turning text into vectors for similarity search (D-06).

Embeddings run on this machine through fastembed, so they cost nothing, have no rate limit and
give the same vectors every time, which the benchmark needs. Agents and the indexer depend on
the small `Embedder` interface, so tests use a fake and never download a model.
"""

import hashlib
import math
import re
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from lineageheal.core.errors import KnowledgeError
from lineageheal.core.settings import Settings
from lineageheal.store.types import EMBEDDING_DIMENSIONS

if TYPE_CHECKING:
    from fastembed import TextEmbedding


class Embedder(Protocol):
    """Embeds documents and search queries into vectors of a fixed size."""

    @property
    def model_name(self) -> str:
        """Identifies the model; stored with each vector, since vectors only compare within one."""
        ...

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed passages to be stored."""
        ...

    def embed_query(self, text: str) -> list[float]:
        """Embed a search query. Some models treat queries differently from passages."""
        ...


class FastEmbedder:
    """The real embedder: an ONNX model run on the CPU through fastembed.

    The model is downloaded on first use (about 130 MB for the default) into `cache_dir`, and
    loaded lazily so that importing this module, or running a command that never embeds, is free.
    """

    def __init__(self, model_name: str, cache_dir: Path) -> None:
        """Remember which model to load; nothing is downloaded yet."""
        self._model_name = model_name
        self._cache_dir = cache_dir
        self._model: TextEmbedding | None = None

    @property
    def model_name(self) -> str:
        """The fastembed model id."""
        return self._model_name

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed passages; the vectors are normalised, so cosine and dot product agree."""
        vectors = [vector.tolist() for vector in self._load().embed(list(texts))]
        return _checked(vectors, self._model_name)

    def embed_query(self, text: str) -> list[float]:
        """Embed a query with the model's query prefix, which bge models are trained to expect."""
        vectors = [vector.tolist() for vector in self._load().query_embed([text])]
        return _checked(vectors, self._model_name)[0]

    def _load(self) -> "TextEmbedding":
        if self._model is None:
            from fastembed import TextEmbedding

            try:
                self._model = TextEmbedding(
                    model_name=self._model_name, cache_dir=str(self._cache_dir)
                )
            except Exception as exc:  # fastembed raises ValueError, network and ONNX errors
                raise KnowledgeError(
                    f"Could not load the embedding model {self._model_name!r}: {exc}"
                ) from exc
        return self._model


def _checked(vectors: list[list[float]], model_name: str) -> list[list[float]]:
    """Refuse vectors the knowledge tables cannot hold, with a message that names the setting."""
    for vector in vectors:
        if len(vector) != EMBEDDING_DIMENSIONS:
            raise KnowledgeError(
                f"{model_name} gives {len(vector)}-dimension vectors but the knowledge tables "
                f"hold {EMBEDDING_DIMENSIONS}; set EMBEDDING_MODEL to a {EMBEDDING_DIMENSIONS}"
                "-dimension model or add a migration"
            )
    return vectors


class HashingEmbedder:
    """A deterministic stand-in for tests: words are hashed into buckets of a vector.

    Texts that share words get similar vectors, which is enough to check ranking and filtering
    without a model, though it knows nothing about meaning.
    """

    model_name = "hashing-test-embedder"

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed each text as a normalised bag of hashed words."""
        return [self._embed(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        """Embed a query the same way as a passage."""
        return self._embed(text)

    @staticmethod
    def _embed(text: str) -> list[float]:
        vector = [0.0] * EMBEDDING_DIMENSIONS
        for word in re.findall(r"[a-z0-9_]+", text.lower()):
            bucket = int.from_bytes(hashlib.sha256(word.encode()).digest()[:4], "big")
            vector[bucket % EMBEDDING_DIMENSIONS] += 1.0
        norm = math.sqrt(sum(x * x for x in vector)) or 1.0
        return [x / norm for x in vector]


def build_embedder(settings: Settings) -> Embedder:
    """The embedder the settings name."""
    return FastEmbedder(settings.embedding_model, settings.embedding_cache_dir)
